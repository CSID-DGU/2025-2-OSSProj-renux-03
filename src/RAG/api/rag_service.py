import csv
import copy
import functools
import hashlib
import io
from importlib.metadata import PackageNotFoundError, version
import asyncio
import logging
import math
import re
import sys
import threading
import time
import uuid
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from dataclasses import dataclass, replace
from typing import AsyncIterator, Dict, List, Tuple

sys.path.append(str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
from scipy.sparse import vstack
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func
from starlette.concurrency import run_in_threadpool
from sklearn import __version__ as sklearn_version
from sklearn.metrics.pairwise import cosine_similarity

from src import config as rag_config
from src.config import (
    DEFAULT_TOP_K,
    HYBRID_ALPHA,
    MAX_CONTEXT_LENGTH,
    MIN_RETRIEVAL_SCORE,
    QUERY_ANALYSIS_MAX_QUERIES,
    RAG_COLLEGE_SCOPE_ENABLED,
    RAG_DECOMPOSE_ENABLED,
    RAG_EVIDENCE_CANDIDATES_PER_DATASET,
    RAG_EVIDENCE_MAX_CANDIDATES,
    RAG_GROUNDING_CHECK_ENABLED,
    RAG_GROUNDING_MIN_SCORE,
    RAG_MAX_SUBQUERIES,
    RAG_SEMANTIC_CACHE_ENABLED,
    RAG_SINGLE_QUERY_RETRIEVAL,
    RAG_SUGGEST_FOLLOWUPS,
    RAG_SUGGEST_FOLLOWUPS_COUNT,
    RECENCY_DECAY_DAYS_BY_DATASET,
    PARENT_CONTEXT_ENABLED,
    RECENCY_WEIGHT,
    RERANKER_CANDIDATES,
    USE_QUERY_ANALYSIS,
    VECTORIZER_DIR,
)
from src.database import (
    SessionLocal,
    PendingItem,
    CustomKnowledge,
    Chunk,
    DocumentQualityCheck,
    IngestionRun,
    Notice,
    Schedule,
    RagQueryLog,
    RagRetrievalLog,
    RagFeedback,
    SourceDocument,
    init_db,
    kst_now,
    verify_database_writable,
)
from src.pipelines.ingest import (
    DATASET_ARTIFACTS,
    build_notice_chunks,
    _extract_notice_apply_deadline,
    ingest_courses,
    ingest_meals,
    ingest_notices,
    ingest_rules,
    ingest_schedule,
    ingest_staff, # 추가
)
from src.search.hybrid import load_tfidf_with_ids, hybrid_search_with_meta
from src.search.hybrid import read_tfidf_metadata
from src.services import semantic_cache
from src.services.answer import format_citations
from src.services.langchain_chat import (
    append_manual_history,
    build_followup_question_details,
    generate_followup_questions,
    generate_langchain_answer,
    generate_langchain_answer_stream,
    get_recent_history_text,
)
from src.services.source_contract import source_reference
from src.services.grounding import check_answer_grounding
from src.services.query_analysis import QueryAnalysisResult, analyze_query
from src.services.router import keyword_route
from src.services.evidence_selector import EvidenceSelectionDecision, select_evidence_groups
from src.services.campus_scope import (
    apply_campus_safety_boundary,
    query_explicitly_requests_wise,
)
from src.services.data_quality import (
    build_notice_linkage_summary,
    build_source_document_quality_report,
    data_quality_mode,
)
from src.models.embedding import get_embedder, encode_texts
from src.utils.date_parser import QueryDateFilter, extract_date_filter_from_query
from src.utils.query_expansion import expand_query
from src.utils.dept_college import college_grad_queries, college_of, college_scope_queries, personalized_grad_queries, user_scope_label
from src.utils.preprocess import make_doc_id
from src.vectorstore.chroma_client import count_items, upsert_items, delete_items

app = FastAPI(
    title="동똑이",
    description="25-2 오픈소스소프트웨어프로젝트 팀 Renux의 동국대학교 캠퍼스 RAG 어시스턴트 API 서비스입니다.",
)


def _log_event(level: int, event: str, exc_info: bool = False, **fields) -> None:
    payload = {"event": event, **fields}
    logging.log(level, json.dumps(payload, ensure_ascii=False, default=str), exc_info=exc_info)


def _mark_stage(stage_timings: dict[str, float], stage: str, started_at: float) -> None:
    stage_timings[stage] = round((time.perf_counter() - started_at) * 1000, 2)


def _sum_estimated_llm_cost(llm_usage: list[dict] | None) -> float | None:
    if not llm_usage:
        return None
    values = [
        float(item["estimated_cost_usd"])
        for item in llm_usage
        if isinstance(item, dict) and item.get("estimated_cost_usd") is not None
    ]
    if not values:
        return None
    return round(sum(values), 8)


def _json_or_none(value) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _safe_package_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


@app.middleware("http")
async def add_request_context(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    started_at = time.perf_counter()

    try:
        response = await call_next(request)
    except Exception:
        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
        _log_event(
            logging.ERROR,
            "request_failed",
            exc_info=True,
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            duration_ms=duration_ms,
        )
        raise

    duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
    response.headers["X-Request-ID"] = request_id
    _log_event(
        logging.INFO,
        "request_completed",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=duration_ms,
    )
    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    return JSONResponse(
        status_code=exc.status_code,
        headers={"X-Request-ID": request_id},
        content={
            "error": {
                "code": "http_error",
                "message": exc.detail,
                "request_id": request_id,
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    _log_event(logging.ERROR, "unhandled_error", exc_info=True, request_id=request_id, path=request.url.path)
    return JSONResponse(
        status_code=500,
        headers={"X-Request-ID": request_id},
        content={
            "error": {
                "code": "internal_error",
                "message": "RAG service failed to process the request.",
                "request_id": request_id,
            }
        },
    )


class NotificationCandidate(BaseModel):
    id: str
    source: str
    source_id: str | None = None
    chunk_id: str | None = None
    title: str
    topic: str
    category: str | None = None
    target_date: str
    start_date: str | None = None
    end_date: str | None = None
    d_day: int
    published_at: str | None = None
    url: str | None = None
    snippet: str | None = None
    confidence: float | None = None
    date_source: str | None = None
    metadata: dict[str, str | None] = Field(default_factory=dict)


def _candidate_str(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _parse_candidate_date(value: str | None, field_name: str, fallback: date) -> date:
    if not value:
        return fallback
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}. Use YYYY-MM-DD.") from exc


def _notification_topic(source: str, title: str, topic: str, category: str | None) -> str:
    haystack = f"{source} {title} {topic} {category or ''}".lower()
    if "장학" in haystack:
        return "scholarship"
    if any(keyword in haystack for keyword in ("수강신청", "수강 신청", "수강정정", "수강 정정", "수강취소", "수강 취소")):
        return "course_registration"
    if any(keyword in haystack for keyword in ("등록금", "납부")):
        return "tuition_payment"
    if any(keyword in haystack for keyword in ("서류", "제출", "접수", "신청")):
        return "document_submission"
    return "academic_schedule"


def _notification_source_id(row: pd.Series, source: str) -> str:
    if source == "schedule":
        for column in ("schedule_id", "doc_id", "chunk_id"):
            value = _candidate_str(row.get(column))
            if value:
                return value
    for column in ("notice_id", "doc_id", "document_key", "url", "chunk_id"):
        value = _candidate_str(row.get(column))
        if value:
            return value
    return _candidate_str(row.name)


def _candidate_snippet(text: object, limit: int = 180) -> str:
    raw = _candidate_str(text).replace("\n", " ")
    return re.sub(r"\s+", " ", raw)[:limit]


def _candidate_notice_deadline(row: pd.Series) -> str:
    existing = _candidate_str(row.get("apply_deadline"))
    if existing:
        return existing
    return _extract_notice_apply_deadline(
        _candidate_str(row.get("title")),
        _candidate_str(row.get("chunk_text")),
        _candidate_str(row.get("published_at")),
    ) or ""


def _build_notification_candidates_from_frames(
    notices_df: pd.DataFrame,
    schedule_df: pd.DataFrame,
    start: date,
    end: date,
    sources: set[str],
    limit: int,
    today: date,
) -> list[NotificationCandidate]:
    candidates: list[NotificationCandidate] = []

    if "notices" in sources and not notices_df.empty:
        notices = notices_df.copy()
        if "apply_deadline" not in notices.columns:
            notices["apply_deadline"] = notices.apply(_candidate_notice_deadline, axis=1)
        else:
            missing = notices["apply_deadline"].isna() | notices["apply_deadline"].astype(str).str.strip().eq("")
            if missing.any():
                notices.loc[missing, "apply_deadline"] = notices.loc[missing].apply(_candidate_notice_deadline, axis=1)

        notices["_target_ts"] = pd.to_datetime(notices["apply_deadline"], errors="coerce")
        notices = notices[notices["_target_ts"].notna()].copy()
        if not notices.empty:
            notices["_target_date"] = notices["_target_ts"].dt.date
            notices = notices[(notices["_target_date"] >= start) & (notices["_target_date"] <= end)].copy()
            if "position" in notices.columns:
                notices["_position"] = pd.to_numeric(notices["position"], errors="coerce").fillna(0)
            else:
                notices["_position"] = 0
            notices["_source_key"] = notices.apply(lambda row: _notification_source_id(row, "notices"), axis=1)
            notices.sort_values(
                by=["_target_ts", "_source_key", "_position"],
                ascending=[True, True, True],
                kind="stable",
                inplace=True,
            )
            notices.drop_duplicates(subset=["_source_key"], keep="first", inplace=True)

            for _, row in notices.iterrows():
                target_date = row["_target_date"]
                title = _candidate_str(row.get("title")) or _extract_chunk_title(row.get("chunk_text"))
                raw_topic = _candidate_str(row.get("topics"))
                category = _candidate_str(row.get("category")) or None
                source_id = _notification_source_id(row, "notices")
                candidates.append(
                    NotificationCandidate(
                        id=f"notices:{source_id}",
                        source="notices",
                        source_id=source_id,
                        chunk_id=_candidate_str(row.get("chunk_id")) or None,
                        title=title,
                        topic=_notification_topic("notices", title, raw_topic, category),
                        category=category,
                        target_date=target_date.isoformat(),
                        start_date=None,
                        end_date=target_date.isoformat(),
                        d_day=(target_date - today).days,
                        published_at=_candidate_str(row.get("published_at")) or None,
                        url=_candidate_str(row.get("url")) or None,
                        snippet=_candidate_snippet(row.get("chunk_text")),
                        confidence=0.78,
                        date_source="apply_deadline_parsed",
                        metadata={
                            "raw_topic": raw_topic or None,
                            "board_code": _candidate_str(row.get("board_code")) or None,
                            "article_id": _candidate_str(row.get("article_id")) or None,
                            "attachments": _candidate_str(row.get("attachments")) or None,
                        },
                    )
                )

    if "schedule" in sources and not schedule_df.empty:
        schedule = schedule_df.copy()
        for column in ("schedule_start", "schedule_end"):
            if column not in schedule.columns:
                schedule[column] = ""
        schedule["_start_ts"] = pd.to_datetime(schedule["schedule_start"], errors="coerce")
        schedule["_end_ts"] = pd.to_datetime(schedule["schedule_end"], errors="coerce")
        schedule["_target_ts"] = schedule["_end_ts"].where(schedule["_end_ts"].notna(), schedule["_start_ts"])
        schedule = schedule[schedule["_target_ts"].notna()].copy()
        if not schedule.empty:
            schedule["_target_date"] = schedule["_target_ts"].dt.date
            schedule = schedule[(schedule["_target_date"] >= start) & (schedule["_target_date"] <= end)].copy()
            schedule["_source_key"] = schedule.apply(lambda row: _notification_source_id(row, "schedule"), axis=1)
            schedule.sort_values(
                by=["_target_ts", "_source_key"],
                ascending=[True, True],
                kind="stable",
                inplace=True,
            )
            schedule.drop_duplicates(subset=["_source_key"], keep="first", inplace=True)

            for _, row in schedule.iterrows():
                target_date = row["_target_date"]
                title = _candidate_str(row.get("title")) or _extract_chunk_title(row.get("chunk_text"))
                category = _candidate_str(row.get("category")) or None
                source_id = _notification_source_id(row, "schedule")
                candidates.append(
                    NotificationCandidate(
                        id=f"schedule:{source_id}",
                        source="schedule",
                        source_id=source_id,
                        chunk_id=_candidate_str(row.get("chunk_id")) or None,
                        title=title,
                        topic="academic_schedule",
                        category=category,
                        target_date=target_date.isoformat(),
                        start_date=_candidate_str(row.get("schedule_start")) or None,
                        end_date=_candidate_str(row.get("schedule_end")) or None,
                        d_day=(target_date - today).days,
                        published_at=_candidate_str(row.get("published_at")) or None,
                        url=_candidate_str(row.get("url")) or None,
                        snippet=_candidate_snippet(row.get("chunk_text")),
                        confidence=0.95,
                        date_source="schedule_end" if _candidate_str(row.get("schedule_end")) else "schedule_start",
                        metadata={
                            "department": _candidate_str(row.get("department")) or None,
                            "raw_topic": _candidate_str(row.get("topics")) or None,
                        },
                    )
                )

    return sorted(candidates, key=lambda item: (item.target_date, item.source, item.title))[:limit]


@app.get("/notifications")
async def notifications_dummy():
    return []


@app.get("/notifications/candidates", response_model=list[NotificationCandidate])
async def notification_candidates(
    from_date: str | None = Query(None, alias="from"),
    to: str | None = None,
    sources: str = "notices,schedule",
    limit: int = 100,
):
    today = kst_now().date()
    start = _parse_candidate_date(from_date, "from", today)
    end = _parse_candidate_date(to, "to", today + timedelta(days=60))
    if end < start:
        raise HTTPException(status_code=400, detail="to must be on or after from.")

    requested_sources = {
        source.strip().lower()
        for source in sources.split(",")
        if source.strip()
    }
    allowed_sources = {"notices", "schedule"}
    requested_sources &= allowed_sources
    if not requested_sources:
        raise HTTPException(status_code=400, detail="sources must include notices or schedule.")

    notices_df = pd.DataFrame()
    schedule_df = pd.DataFrame()
    if "notices" in requested_sources:
        notices_df, _, _, _ = await run_in_threadpool(_ensure_dataset, "notices")
    if "schedule" in requested_sources:
        schedule_df, _, _, _ = await run_in_threadpool(_ensure_dataset, "schedule")

    return await run_in_threadpool(
        _build_notification_candidates_from_frames,
        notices_df=notices_df,
        schedule_df=schedule_df,
        start=start,
        end=end,
        sources=requested_sources,
        limit=max(1, min(limit, 300)),
        today=today,
    )


@app.options("/notifications")
async def notifications_options_dummy():
    return {}


@app.options("/notifications/candidates")
async def notification_candidates_options_dummy():
    return {}

@app.options("/token")
async def token_options_dummy():
    return {}

_DATASET_LOADERS = {
    "notices": ingest_notices,
    "rules": ingest_rules,
    "schedule": ingest_schedule,
    "courses": ingest_courses,
    "staff": ingest_staff, # 추가
    # meals는 SQLite가 아닌 CSV에서 재구성된다. 로더를 등록해 startup 워밍업(인메모리 캐시)·
    # _ensure_dataset 폴백·reindex 검증에 포함시킨다(없으면 캐시가 비고 폴백 시 KeyError).
    "meals": ingest_meals,
}

@dataclass
class DatasetCache:
    chunks: pd.DataFrame
    vectorizer: object
    matrix: object
    chunk_path: Path
    chunk_mtime: float
    tfidf_mtime: float
    tfidf_chunk_ids: list | None = None


@dataclass(frozen=True)
class RetrievalPolicy:
    name: str
    min_score: float
    allow_recency_override: bool = False
    prefer_notices_with_dates: bool = False


@dataclass(frozen=True)
class QueryAnalysisMeta:
    result: QueryAnalysisResult | None
    used: bool = False
    failed: bool = False


_datasets: Dict[str, DatasetCache] = {}
# admin 리로드(del/재로드)와 검색 스레드의 _ensure_dataset 경합 방지용 락
_datasets_lock = threading.Lock()
# 로컬/운영 인덱스에 실제 데이터가 존재하는 6개 검색 데이터셋은 모두 서비스 준비에 필수다.
# scheduler는 데이터 신선도를 높이는 선택 기능이므로 readiness 필수 항목에서 제외한다.
_REQUIRED_DATASETS: tuple[str, ...] = tuple(_DATASET_LOADERS.keys())
_readiness_lock = threading.Lock()


def _new_readiness_state() -> dict:
    return {
        "startup_complete": False,
        "checks": {
            "configuration": {"required": True, "ready": False, "detail": "not_checked"},
            "database": {"required": True, "ready": False, "detail": "not_checked"},
            "data_quality": {
                "required": os.getenv("RAG_DATA_QUALITY_MODE", "observe").strip().lower() == "strict",
                "ready": False,
                "detail": "not_checked",
            },
            "datasets": {
                "required": True,
                "ready": False,
                "detail": "not_checked",
                "required_datasets": list(_REQUIRED_DATASETS),
                "counts": {},
                "dense_counts": {},
                "dense_errors": {},
                "errors": {},
            },
            "embedder": {"required": True, "ready": False, "detail": "not_checked"},
            "scheduler": {"required": False, "ready": False, "detail": "not_checked"},
        },
    }


_readiness_state = _new_readiness_state()


def _reset_startup_readiness() -> None:
    global _readiness_state
    with _readiness_lock:
        _readiness_state = _new_readiness_state()


def _readiness_error(code: str, exc: Exception) -> dict[str, str]:
    return {
        "code": code,
        "type": type(exc).__name__,
        "message": str(exc)[:500],
    }


def _set_readiness_check(name: str, **values) -> None:
    with _readiness_lock:
        _readiness_state["checks"][name].update(values)


def _set_startup_complete() -> None:
    with _readiness_lock:
        _readiness_state["startup_complete"] = True


def _readiness_snapshot() -> dict:
    with _readiness_lock:
        snapshot = copy.deepcopy(_readiness_state)

    required_checks = [check for check in snapshot["checks"].values() if check["required"]]
    snapshot["ready"] = snapshot["startup_complete"] and all(check["ready"] for check in required_checks)
    snapshot["status"] = "ready" if snapshot["ready"] else "not_ready"
    snapshot["failures"] = [
        {"component": name, "error": check.get("error"), "detail": check.get("detail")}
        for name, check in snapshot["checks"].items()
        if check["required"] and not check["ready"]
    ]
    return snapshot

# 라우팅 없이 전체 검색을 할 때 대상이 되는 모든 데이터셋(인덱싱된 순서 유지).
SEARCHABLE_DATASETS: List[str] = list(DATASET_ARTIFACTS.keys())
FALLBACK_REASON_NO_RESULTS = "no_results"
FALLBACK_REASON_DATE_FILTER_ELIMINATED_ALL = "date_filter_eliminated_all"
FALLBACK_REASON_DATASET_UNAVAILABLE = "dataset_unavailable"
FALLBACK_REASON_SCORE_BELOW_THRESHOLD = "score_below_threshold"
# 학과 필터를 적용하지 않는 sentinel 값들(백엔드는 보통 null을 보내지만 방어적으로 처리).
_NO_MAJOR_SENTINELS = {"Default", "Unknown"}


def _routerless_retrieval_route() -> list[str]:
    """Return every indexed dataset for the explicit diagnostic override."""
    return list(SEARCHABLE_DATASETS)


def _resolve_retrieval_route(raw_query: str, analysis: QueryAnalysisMeta) -> list[str]:
    """Limit retrieval to the analyzed intent plus deterministic safety coverage.

    Query analysis supplies the primary intent.  Keyword routing only adds a
    second corpus when the wording clearly calls for it (for example, a
    programme-specific application deadline needs both schedule and notices).
    The full-corpus search remains an explicit operational override rather
    than the production default.
    """
    if rag_config.RAG_SEARCH_ALL_DATASETS:
        return _routerless_retrieval_route()

    route = _merge_routes(analysis, keyword_route(raw_query))
    if _should_append_rules_route(raw_query, route):
        route.append("rules")
    if _should_append_notices_for_rules_query(raw_query, route):
        route.append("notices")
    if _should_append_notices_for_schedule_query(raw_query, route):
        route.append("notices")
    return route or ["notices"]


def _current_operational_notice_terms(query: str, route: List[str]) -> list[str]:
    """Return title terms for a current operational schedule notice lookup."""
    if "schedule" not in route or "notices" not in route:
        return []
    if not any(term in query for term in CURRENT_OPERATIONAL_TIME_TERMS):
        return []
    return [term for term in SCHEDULE_NOTICE_SUPPORT_TERMS if term in query]


def _semantic_cache_namespace(major: str, *, allow_wise: bool = False) -> str:
    # 실제 학과명과 값 공간이 겹치지 않도록 접두사로 구분한다(가령 major=="__anon__" 같은
    # 입력이 익명 버킷과 충돌하는 일을 막는다). 답변은 학과별로 달라지므로 네임스페이스는 학과 기준.
    user_scope = f"major:{major}" if major and major not in _NO_MAJOR_SENTINELS else "anon"
    # A WISE-explicit answer must never be a semantic-cache candidate for the
    # default Seoul/BMC product scope (or vice versa).
    campus_scope = "wise_allowed" if allow_wise else "seoul_bmc"
    # Invalidate answers cached before title-priority period scope, restricted
    # audience filtering, contact completeness, and the wider safe parent
    # window were enforced.
    return f"{user_scope}|retrieval-v8-source-scope:{campus_scope}"


def _should_cache_answer(
    route: List[str],
    fallback_triggered: bool,
    date_filter_applied: bool,
    grounded: bool | None,
    answer: str | None = None,
    *,
    recent_notice_query: bool = False,
) -> bool:
    if recent_notice_query:
        return False
    if fallback_triggered:
        return False
    if "meals" in route:
        return False
    if date_filter_applied is True:
        return False
    if grounded is False:
        return False
    if answer is not None and not answer.strip():
        return False
    return True


def _grounding_allows_followups(grounding_enabled: bool, grounding_result) -> bool:
    """Suggestions require a completed, positive grounding check."""
    return bool(
        grounding_enabled
        and grounding_result is not None
        and grounding_result.checked
        and grounding_result.grounded
    )


DATASET_REASON_EMPTY_COLLECTION = "empty_collection"
DATASET_REASON_ARTIFACT_MISSING = "artifact_missing"
DATASET_REASON_VECTORIZER_MISSING = "vectorizer_missing"
DATASET_REASON_VERSION_MISMATCH = "version_mismatch"
NOTICE_RECENCY_TERMS = ("장학", "공지", "모집", "발표")
RECENT_QUERY_TERMS = ("오늘", "최근", "최신", "방금", "올라온", "새로")
NOTICE_FOCUS_TERMS = ("장학", "학사", "입학", "유학생", "수강", "휴학", "복학", "등록", "졸업")
RULES_NOTICE_SUPPORT_TERMS = ("수강신청", "재수강", "휴학", "복학", "성적", "졸업", "전과", "복수전공")
SCHEDULE_NOTICE_SUPPORT_TERMS = (
    "수강신청", "수강정정", "장바구니", "희망강의", "보강", "등록금", "휴학", "복학", "학적",
)
CURRENT_OPERATIONAL_TIME_TERMS = ("이번 학기", "이번학기", "올해", "현재", "오늘", "내일", "이번 달", "이번달")
EVIDENCE_FALLBACK_STOP_TERMS = {
    "알려줘", "보여줘", "어떻게", "어떤", "언제", "무엇", "뭐야", "해주세요", "싶은데", "해야해",
    "대한", "관련", "정보", "문의", "학교", "동국대", "동국대학교",
}
ENTRY_YEAR_GUIDE_SOURCE_TYPE = "entry_year_guide_pdf"
ENTRY_YEAR_GUIDE_TERMS = (
    "학번",
    "신입생",
    "졸업",
    "졸업기준",
    "이수",
    "이수기준",
    "교양",
    "복수전공",
    "다전공",
    "전과",
    "수강신청",
    "재수강",
    "학점포기",
)
COURSE_GRADE_TERMS = ("1학년", "2학년", "3학년", "4학년", "1학기", "2학기")
NOTICE_BOARD_ALIASES = {
    "일반공지": "일반공지",
    "일반 공지": "일반공지",
    "장학공지": "장학공지",
    "장학 공지": "장학공지",
    "학사공지": "학사공지",
    "학사 공지": "학사공지",
    "유학생공지": "유학생공지",
    "유학생 공지": "유학생공지",
    "행사공지": "행사공지",
    "행사 공지": "행사공지",
    "국제교류공지": "국제교류공지",
    "국제교류 공지": "국제교류공지",
    "국제공지": "국제교류공지",
    "국제 공지": "국제교류공지",
    "학술공지": "학술공지",
    "학술 공지": "학술공지",
    "입학공지": "입학공지",
    "입학 공지": "입학공지",
    "안전공지": "안전공지",
    "안전 공지": "안전공지",
}
DEADLINE_NOTICE_HIT_COLUMNS = [
    "chunk_id", "title", "chunk_text", "hybrid_score", "vector_score", "sparse_score",
    "topics", "category", "published_at", "apply_deadline", "url", "source", "notice_id",
    "major", "entry_year", "source_type", "attachments",
    "doc_id", "position",
    "is_closed", "restaurant", "meal_date",
    "schedule_start", "schedule_end", "department", "campus_scope",
    "filename", "relative_dir", "source_file", "document_key", "source_id",
    "board_code", "article_id", "schedule_id", "staff_id", "course_id", "rule_id",
]
SCHOOL_INFO_TERMS = (
    "동국",
    "학교",
    "학사",
    "공지",
    "장학",
    "모집",
    "발표",
    "전화번호",
    "연락처",
    "사무실",
    "내선",
    "교과",
    "전공",
    "수업",
    "강의",
    "개강",
    "종강",
    "시험",
    "수강",
    "휴학",
    "복학",
    "성적",
    "졸업",
    "입학",
    "등록",
    "일정",
    "교수",
    "행정실",
    "장학금",
    "공지사항",
    "학칙",
    "규정",
    "교과과정",
    "과목",
)
class SourceChunk(BaseModel):
    source: str
    metadata: Dict
    snippet: str
    citation_number: int | None = None
    chunk_id: str | None = None
    title: str | None = None
    url: str | None = None
    published_at: str | None = None
    vector_score: float | None = None
    sparse_score: float | None = None
    hybrid_score: float | None = None
    recency_score: float | None = None
    final_score: float | None = None
    sort_date: str | None = None
    source_ref: str | None = None


class SuggestedQuestionDetail(BaseModel):
    question: str
    source_refs: list[str] = Field(default_factory=list)


_SOURCE_SCORE_COLUMNS = {
    "vector_score",
    "sparse_score",
    "hybrid_score",
    "norm_recency",
    "final_score",
}
_SOURCE_INTERNAL_COLUMNS = {
    "chunk_text",
    "dataset",
    "sort_date",
    "sort_timestamp",
    "norm_hybrid",
    "recent_notice_priority",
    "matched_query_count",
    "query_match_bonus",
    "date_unknown_auxiliary",
    "notice_topic_match",
    "matched_queries",
    "candidate_id",
    "dataset_rank",
    "retrieval_fusion_score",
    "evidence_group",
    "citation_number",
    "selector_fallback",
    "campus_allow_wise",
} | _SOURCE_SCORE_COLUMNS


def _source_metadata(row: pd.Series) -> dict:
    return {
        column: _clean_response_value(row.get(column))
        for column in row.index
        if column not in _SOURCE_INTERNAL_COLUMNS
    }


class AskResponse(BaseModel):
    request_id: str
    answer: str
    citations: str
    route: List[str]
    resolved_intents: list[str] = Field(default_factory=list)
    sources: List[SourceChunk]
    suggested_questions: list[str] = Field(default_factory=list)
    suggested_question_details: list[SuggestedQuestionDetail] = Field(default_factory=list)
    grounded: bool | None = None
    grounding_score: float | None = None
    fallback_triggered: bool = False
    fallback_reason: str | None = None


def _source_chunk_from_row(row: pd.Series) -> SourceChunk:
    chunk = SourceChunk(
        source=_clean_response_str(row.get("dataset")) or "",
        metadata=_source_metadata(row),
        snippet=_clean_response_str(row.get("chunk_text")) or "",
        citation_number=int(row.get("citation_number")),
        chunk_id=_clean_response_str(row.get("chunk_id")),
        title=_clean_response_str(row.get("title")),
        url=_clean_response_str(row.get("url")),
        published_at=_clean_response_str(row.get("published_at")),
        vector_score=_clean_response_float(row.get("vector_score")),
        sparse_score=_clean_response_float(row.get("sparse_score")),
        hybrid_score=_clean_response_float(row.get("hybrid_score")),
        recency_score=_clean_response_float(row.get("norm_recency")),
        final_score=_clean_response_float(row.get("final_score")),
        sort_date=_clean_response_str(row.get("sort_date")),
    )
    chunk.source_ref = source_reference(chunk.model_dump())
    return chunk


class FeedbackRequest(BaseModel):
    request_id: str = Field(..., alias="requestId")
    rating: int
    reason: str | None = None
    comment: str | None = None
    session_id: str | None = Field(None, alias="sessionId")
    major: str | None = None

    class Config:
        populate_by_name = True


class AskRequest(BaseModel):
    question: str = Field(..., description="사용자 질문", alias="question")
    session_id: str | None = Field(None, description="대화 세션 ID (없으면 기본 세션)", alias="sessionId")
    major: str | None = Field(None, description="사용자 학과") # 새로 추가

    class Config:
        populate_by_name = True


def _dataset_status_message(reason: str, **kwargs) -> str:
    if reason == DATASET_REASON_EMPTY_COLLECTION:
        return "Chroma collection is empty while chunk cache is loaded."
    if reason == DATASET_REASON_ARTIFACT_MISSING:
        return "Chunk artifact is missing."
    if reason == DATASET_REASON_VECTORIZER_MISSING:
        return "TF-IDF vectorizer artifact is missing."
    if reason == DATASET_REASON_VERSION_MISMATCH:
        return (
            "TF-IDF artifact version mismatch: "
            f"{kwargs.get('artifact_version')} != {kwargs.get('runtime_version')}"
        )
    return "Dataset status is degraded."


class SubmitRequest(BaseModel):
    source_type: str
    data: str


def _clean_response_value(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _clean_response_str(value) -> str | None:
    value = _clean_response_value(value)
    return None if value is None else str(value)


def _clean_response_float(value) -> float | None:
    value = _clean_response_value(value)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_retrieval_fallback_answer(
    route: List[str] = None,
    reason: str | None = None,
    *,
    date_filter_relaxed: bool = False,
    policy_name: str | None = None,
    clarification_reason: str | None = None,
) -> str:
    base_msg = (
        "제공된 동국대학교 자료에서 질문과 충분히 관련 있는 정보를 찾지 못했습니다.\n\n"
        "정확하지 않은 정보를 추측해서 답변하는 대신, 다음과 같은 방법을 권장합니다:\n"
    )

    if reason == FALLBACK_REASON_DATE_FILTER_ELIMINATED_ALL:
        if date_filter_relaxed and policy_name == "recent_notices":
            base_msg += "- **최신 범위 재확인**: 오늘 기준으로 찾지 못해 최근 며칠 범위까지 넓혀 다시 검색했지만 확인된 공지를 찾지 못했습니다.\n"
        else:
            base_msg += "- **날짜 범위 재확인**: 요청하신 날짜 범위 안에서는 확인된 공지를 찾지 못했습니다. 날짜를 넓혀서 다시 질문해 보세요.\n"
    elif reason == FALLBACK_REASON_DATASET_UNAVAILABLE:
        base_msg += "- **잠시 후 재시도**: 일부 학교 자료 인덱스를 지금 조회하지 못했습니다. 잠시 후 다시 질문해 주세요.\n"
    if route and "staff" in route:
        base_msg += "- **부서 연락처 확인**: 질문하신 내용과 관련된 부서의 연락처를 찾으시려면 '어느 부서 전화번호 알려줘'와 같이 다시 질문해 보세요.\n"
    elif route and "notices" in route:
        base_msg += "- **공지사항 검색**: 학교 홈페이지의 공지사항 게시판에서 키워드로 직접 검색해 보시는 것이 가장 정확합니다.\n"

    base_msg += (
        "- **질문 구체화**: 학과명, 날짜, 정확한 공지 제목 등을 포함해 주시면 더 나은 결과를 얻을 수 있습니다.\n"
        "- **공식 채널 이용**: 긴급한 사안은 해당 학과 사무실이나 행정 부서에 직접 유선으로 문의하시기 바랍니다."
    )
    if clarification_reason:
        base_msg += f"\n- **추가 정보 요청**: {clarification_reason}"
    return base_msg


def _deduplicate_clarification_fields(fields: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for field in fields:
        cleaned = str(field or "").strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result


def _first_turn_clarification_fields(
    raw_query: str,
    analysis: QueryAnalysisMeta,
    history_text: str,
) -> list[str]:
    """Return the missing fields that make a first-turn query unsafe to answer.

    The query analyser is allowed to mark a question ambiguous while still
    producing search queries.  That is useful for retrieval diagnostics, but
    it must not make the answer endpoint guess the referent.  A small
    deterministic layer also covers common Korean ellipses that an LLM may
    classify as a normal schedule/course question (for example ``시험 언제``).
    Existing conversation history is deliberately exempt: the analyser has
    already rewritten a follow-up against that context.
    """
    if str(history_text or "").strip():
        return []

    compact = re.sub(r"\s+", "", raw_query).lower()
    fields: list[str] = []
    if analysis.result is not None and analysis.result.needs_clarification:
        if analysis.result.clarification_reason:
            fields.append(analysis.result.clarification_reason)

    if "시험언제" in compact and not any(term in compact for term in ("중간", "기말", "학기", "모의", "종합")):
        fields.extend(["학기", "시험 종류(중간/기말)"])
    if "그강의" in compact or "이강의" in compact:
        fields.extend(["강의명", "학수번호"])
    if "졸업가능" in compact or "졸업할수있" in compact:
        fields.extend(["학번", "학과", "이수 내역"])
    if "내가받을수있는장학" in compact or "받을장학금" in compact:
        fields.extend(["학년", "성적", "소득 구간"])
    if "그거신청" in compact and "학적" in compact:
        fields.extend(["신청 종류", "대상"])
    if "거기오늘열" in compact:
        fields.extend(["시설명", "캠퍼스"])
    if "오늘거기뭐" in compact:
        fields.extend(["식당명", "날짜"])
    if "수업건물" in compact and "가까운식당" in compact:
        fields.extend(["수업 건물", "식사 시간"])
    if "지금바로이용" in compact and "가까운" in compact:
        fields.extend(["현재 위치", "시설 종류"])
    if "거기들어가" in compact:
        fields.extend(["생활관명", "캠퍼스"])
    if "나한테맞는회사" in compact:
        fields.extend(["희망 직무", "전공"])
    if "그나라" in compact:
        fields.extend(["국가", "프로그램"])
    if "거기담당자" in compact:
        fields.extend(["문의 업무", "부서"])
    if "그행사" in compact:
        fields.extend(["행사명", "날짜"])

    if not fields and analysis.result is not None and analysis.result.needs_clarification:
        fields.append("확인하려는 대상이나 조건")
    return _deduplicate_clarification_fields(fields)


def _build_clarification_answer(fields: list[str]) -> str:
    requested = ", ".join(fields)
    last_character = requested.rstrip()[-1] if requested.strip() else ""
    has_final_consonant = bool(last_character) and "가" <= last_character <= "힣" and (ord(last_character) - ord("가")) % 28 != 0
    object_particle = "을" if has_final_consonant else "를"
    return (
        "정확한 학교 정보를 확인하려면 몇 가지 정보가 더 필요해요. "
        f"{requested}{object_particle} 알려주실 수 있나요?"
    )


def _get_current_kst_string() -> str:
    from datetime import timedelta, timezone

    kst = timezone(timedelta(hours=9))
    return datetime.now(kst).strftime("%Y년 %m월 %d일 %H시 %M분 (KST)")


def _has_school_info_terms(raw_query: str) -> bool:
    normalized = re.sub(r"\s+", "", raw_query.lower())
    return any(term in normalized for term in SCHOOL_INFO_TERMS)


def _calculate_recency_score(sort_date, dataset: str, now: pd.Timestamp) -> float:
    decay_days = RECENCY_DECAY_DAYS_BY_DATASET.get(dataset)
    if not decay_days:
        return 1.0
    if pd.isna(sort_date):
        return 0.0

    age_days = max((now - sort_date).total_seconds() / 86400, 0.0)
    return math.exp(-age_days / decay_days)


def _is_recent_notice_query(query: str, route: List[str]) -> bool:
    if "notices" not in route:
        return False
    if not any(term in query for term in NOTICE_RECENCY_TERMS):
        return False
    return any(term in query for term in RECENT_QUERY_TERMS)


def _is_staff_lookup_query(query: str, route: List[str]) -> bool:
    if "staff" not in route:
        return False
    return any(term in query for term in ("전화번호", "연락처", "사무실", "내선", "번호", "전화", "문의"))


def _extract_notice_board_filter(query: str, route: List[str]) -> str | None:
    if "notices" not in route:
        return None

    for alias, normalized in NOTICE_BOARD_ALIASES.items():
        if alias in query:
            return normalized
    return None


def _extract_entry_year_from_query(query: str) -> int | None:
    explicit_match = re.search(r"\b(20\d{2})\s*(?:학번|신입생)?", query)
    if explicit_match:
        year = int(explicit_match.group(1))
        if 2000 <= year <= 2099:
            return year

    short_match = re.search(r"\b(\d{2})\s*학번", query)
    if short_match:
        year = 2000 + int(short_match.group(1))
        if 2000 <= year <= 2099:
            return year
    return None


def _has_entry_year_guide_intent(query: str) -> bool:
    return any(term in query for term in ENTRY_YEAR_GUIDE_TERMS)


def _should_append_rules_route(query: str, route: List[str]) -> bool:
    if "rules" in route:
        return False
    if "courses" in route and _has_entry_year_guide_intent(query):
        return True
    return False


def _should_append_notices_for_rules_query(query: str, route: List[str]) -> bool:
    """Operational student rules are often published as time-bound notices."""
    return (
        "rules" in route
        and "notices" not in route
        and any(term in query for term in RULES_NOTICE_SUPPORT_TERMS)
    )


def _should_append_notices_for_schedule_query(query: str, route: List[str]) -> bool:
    """Search time-bound notices alongside schedule for operational deadlines.

    The schedule corpus is authoritative for broad academic-calendar events,
    while exact hours, 대상, and exception rules for registration-related
    events are normally published as notices. Generic calendar questions do
    not take this companion path, retaining deterministic schedule retrieval.
    """
    return (
        "schedule" in route
        and "notices" not in route
        and any(term in query for term in SCHEDULE_NOTICE_SUPPORT_TERMS)
    )


def _is_entry_year_guide_row(row: pd.Series) -> bool:
    return str(row.get("source_type", "")).strip() == ENTRY_YEAR_GUIDE_SOURCE_TYPE


def _latest_entry_year_in_frame(merged: pd.DataFrame) -> int | None:
    if merged.empty or "entry_year" not in merged.columns:
        return None
    years = pd.to_numeric(merged["entry_year"], errors="coerce").dropna()
    if years.empty:
        return None
    return int(years.max())


def _build_guide_context_prefix(merged: pd.DataFrame, route: List[str], entry_year: int | None) -> str:
    if merged.empty or "source_type" not in merged.columns:
        return ""

    guide_mask = merged["source_type"].astype(str).eq(ENTRY_YEAR_GUIDE_SOURCE_TYPE)
    if not guide_mask.any():
        return ""

    latest_year = _latest_entry_year_in_frame(merged[guide_mask])
    effective_year = entry_year or latest_year

    if "courses" in route:
        year_label = f"{effective_year}학번 기준" if effective_year is not None else "최신 기준"
        return (
            f"안내 메모: 학번별 PDF 자료가 함께 검색되었습니다. 이 자료는 {year_label} 이수기준·졸업기준 안내용입니다. "
            "세부 과목표나 학년별 개설과목 목록처럼 단정하지 말고, 과목표가 확인되지 않으면 그 점을 명시하세요.\n\n"
        )
    return ""


def _build_guide_answer_prefix(merged: pd.DataFrame, route: List[str], entry_year: int | None) -> str:
    if merged.empty or "source_type" not in merged.columns:
        return ""

    guide_mask = merged["source_type"].astype(str).eq(ENTRY_YEAR_GUIDE_SOURCE_TYPE)
    if not guide_mask.any():
        return ""

    latest_year = _latest_entry_year_in_frame(merged[guide_mask])
    effective_year = entry_year or latest_year
    if effective_year is None:
        return ""

    if "courses" in route:
        has_course_chunks = "dataset" in merged.columns and (merged["dataset"].astype(str) == "courses").any()
        if not has_course_chunks:
            return (
                f"세부 과목표는 제공된 자료에서 충분히 확인되지 않아, 아래는 {effective_year}학년도 신입생 기준의 "
                "이수기준·졸업기준을 바탕으로 안내합니다.\n\n"
            )
        return f"참고: 일부 안내는 {effective_year}학년도 신입생 기준입니다.\n\n"

    if entry_year is None:
        return f"참고: 아래 내용은 최신 기준인 {effective_year}학년도 신입생 기준을 우선 반영했습니다.\n\n"
    return ""


def _analysis_to_meta(result: QueryAnalysisResult | None, *, failed: bool = False) -> QueryAnalysisMeta:
    if result is None:
        return QueryAnalysisMeta(result=None, used=False, failed=failed)
    return QueryAnalysisMeta(result=result, used=True, failed=False)


def _is_compound_analysis(analysis: QueryAnalysisMeta) -> bool:
    """질의 분해가 활성화되어 있고 분석이 복합 질문으로 판단했는지."""
    return bool(
        RAG_DECOMPOSE_ENABLED
        and analysis.result is not None
        and analysis.result.is_compound
        and analysis.result.sub_queries
    )


def _user_profile_prefix(user_major: str | None) -> str:
    """로그인 사용자의 소속 학과를 컨텍스트 상단에 명시해, 학과를 안 밝힌 질문도
    본인 소속 기준으로 답하도록 유도한다(개인 맞춤형). 학과 미상이면 빈 문자열."""
    major = user_major if user_major and user_major not in _NO_MAJOR_SENTINELS else None
    if not major:
        return ""
    label = user_scope_label(major)
    college = None
    if RAG_COLLEGE_SCOPE_ENABLED:
        try:
            college = college_of(major)
        except Exception:
            college = None
    if college:
        return (
            f"[질문자 정보] 소속 학과: {label}. "
            "질문에 학과·단과대를 따로 밝히지 않았다면 이 소속을 기준으로 답하세요. "
            f"이 학생은 {college}에 속하므로, 학과 자료가 없거나 부족하면 같은 {college}(단과대학) "
            "공통 공지·규정·일정·행정 정보도 본인에게 해당되는 것으로 보고 답하라; "
            "단, 명백히 다른 학과/단과대 전용 정보는 그쪽 기준을 따르라.\n\n"
        )
    return (
        f"[질문자 정보] 소속 학과: {label}. "
        "질문에 학과·단과대를 따로 밝히지 않았다면 이 소속을 기준으로 답하세요. "
        "단, 질문에 다른 학과·단과대가 명시되어 있으면 그쪽 기준을 따르세요.\n\n"
    )


def _build_retrieval_queries(
    raw_query: str, expanded_query: str, analysis: QueryAnalysisMeta, user_major: str | None = None
) -> List[str]:
    # 단일 쿼리 모드: 서브쿼리/확장/단과대 보강 없이 사용자 질문 1개로만 검색한다.
    if RAG_SINGLE_QUERY_RETRIEVAL:
        return [raw_query.strip()]

    queries: List[str] = []

    # 1. 원문은 항상 포함
    queries.append(raw_query.strip())

    # 2. 분석 결과가 있으면 분석된 쿼리 추가 (원문과 다를 경우만)
    if analysis.result is not None:
        norm = analysis.result.normalized_question.strip()
        if norm and norm not in queries:
            queries.append(norm)

        for sq in analysis.result.search_queries[:QUERY_ANALYSIS_MAX_QUERIES]:
            cleaned = sq.strip()
            if cleaned and cleaned not in queries:
                queries.append(cleaned)

    # 2-1. 복합 질문이면 측면별 분해 서브쿼리를 추가한다. 각 서브쿼리는 route의 데이터셋들과
    #      교차 검색되며(merge가 점수로 정리), 단순 질문은 이 경로를 타지 않아 영향이 없다.
    compound = _is_compound_analysis(analysis)
    if compound:
        for sub in analysis.result.sub_queries:
            cleaned = sub.query.strip()
            if cleaned and cleaned not in queries:
                queries.append(cleaned)

    # 3. 확장 쿼리는 분석 결과가 없을 때나 쿼리가 너무 적을 때만 보조적으로 추가한다.
    if len(queries) < 3:
        expanded = expanded_query.strip()
        if expanded and expanded not in queries:
            queries.append(expanded)

    # 단순 질문은 종전대로 최대 3개, 복합 질문은 분해 서브쿼리를 담을 수 있게 상한을 넓힌다.
    cap = (2 + RAG_MAX_SUBQUERIES) if compound else 3
    result = queries[:cap]

    # 학과명으로 졸업 요건을 물으면 소속 단과대 기준 검색어를 보강한다(예: 통계학과 → 이과대학).
    # 졸업기준 자료가 단과대 단위라 학과명만으로는 매칭이 약하기 때문. cap과 무관하게 항상 포함.
    extra_queries = list(college_grad_queries(raw_query))
    # 로그인 사용자가 학과를 안 밝히고 졸업요건을 물어도 본인 학과 기준 자료가 잡히도록 보강.
    valid_major = user_major if user_major and user_major not in _NO_MAJOR_SENTINELS else None
    extra_queries.extend(personalized_grad_queries(raw_query, valid_major))
    if RAG_COLLEGE_SCOPE_ENABLED:
        extra_queries.extend(college_scope_queries(raw_query, valid_major))
    for cq in extra_queries:
        if cq not in result:
            result.append(cq)
    return result


def _merge_routes(analysis: QueryAnalysisMeta, routed: List[str]) -> List[str]:
    merged: List[str] = []
    if analysis.result is not None and analysis.result.intent in {"notices", "rules", "schedule", "staff", "courses", "meals"}:
        merged.append(analysis.result.intent)
    for route_name in routed:
        if route_name not in merged:
            merged.append(route_name)
    # 복합 질문이면 분해 서브쿼리가 가리키는 데이터셋을 합집합으로 더해, 요건·과목·일정·연락처가
    # 한 답변에 융합되도록 한다(예: 졸업 준비 → rules+courses+schedule+staff).
    if _is_compound_analysis(analysis):
        for dataset in analysis.result.decomposed_datasets:
            if dataset not in merged:
                merged.append(dataset)
    return merged or ["notices"]


def _resolve_retrieval_policy(query: str, route: List[str]) -> RetrievalPolicy:
    staff_lookup_min_score = min(
        MIN_RETRIEVAL_SCORE,
        max(MIN_RETRIEVAL_SCORE - 0.03, 0.08),
    )
    courses_min_score = min(
        MIN_RETRIEVAL_SCORE,
        max(MIN_RETRIEVAL_SCORE - 0.07, 0.05),
    )
    recent_notice_query = _is_recent_notice_query(query, route)
    if recent_notice_query:
        return RetrievalPolicy(
            name="recent_notices",
            min_score=max(MIN_RETRIEVAL_SCORE - 0.04, 0.08),
            allow_recency_override=True,
            prefer_notices_with_dates=True,
        )
    if _is_staff_lookup_query(query, route):
        return RetrievalPolicy(name="staff_lookup", min_score=staff_lookup_min_score)
    if "courses" in route and "notices" not in route and set(route).issubset({"courses", "rules"}):
        return RetrievalPolicy(name="courses", min_score=courses_min_score)
    if any(dataset in route for dataset in ("rules", "schedule")) and "notices" not in route:
        return RetrievalPolicy(name="rules_schedule", min_score=MIN_RETRIEVAL_SCORE)
    if "notices" in route:
        return RetrievalPolicy(name="general_notices", min_score=MIN_RETRIEVAL_SCORE)
    return RetrievalPolicy(name="default", min_score=MIN_RETRIEVAL_SCORE)


def _resolve_notice_retrieval_controls(
    raw_query: str,
    semantic_query: str,
    route: List[str],
) -> tuple[bool, str | None, RetrievalPolicy]:
    """Resolve the notice-specific controls shared by both ask paths.

    The retrieval route is currently the full corpus, so query-analysis output
    can omit words such as "최근" or a board name. Check both the user's
    original wording and its normalized form before deciding whether to bypass
    semantic retrieval for a newest-notice lookup.
    """
    query_variants = [query for query in (raw_query, semantic_query) if query]
    recent_notice_query = any(
        _is_recent_notice_query(query, route) for query in query_variants
    )
    notice_board_filter = next(
        (
            board
            for query in query_variants
            if (board := _extract_notice_board_filter(query, route)) is not None
        ),
        None,
    )

    # Keep existing all-dataset scoring unchanged for ordinary questions. The
    # dedicated policy is needed only when the newest-notice path is active.
    retrieval_policy = (
        _resolve_retrieval_policy(
            next(
                query
                for query in query_variants
                if _is_recent_notice_query(query, route)
            ),
            route,
        )
        if recent_notice_query
        else RetrievalPolicy(name="all_datasets", min_score=MIN_RETRIEVAL_SCORE)
    )
    return recent_notice_query, notice_board_filter, retrieval_policy


def _extract_notice_focus_terms(query: str) -> List[str]:
    return [term for term in NOTICE_FOCUS_TERMS if term in query]


def _row_matches_notice_focus_terms(row: pd.Series, focus_terms: List[str]) -> bool:
    if not focus_terms:
        return True

    haystack = " ".join(
        filter(
            None,
            [
                _clean_response_str(row.get("title")) or "",
                _clean_response_str(row.get("topics")) or "",
                _clean_response_str(row.get("snippet")) or "",
                _clean_response_str(row.get("chunk_text")) or "",
            ],
        )
    )
    return any(term in haystack for term in focus_terms)


def _has_notice_topic_alignment(merged: pd.DataFrame, query: str) -> bool:
    if merged.empty:
        return False
    focus_terms = _extract_notice_focus_terms(query)
    if not focus_terms:
        return True

    candidates = merged[merged.get("dataset") == "notices"].head(5)
    if candidates.empty:
        return False

    for _, row in candidates.iterrows():
        if _row_matches_notice_focus_terms(row, focus_terms):
            return True
    return False


def _apply_date_filter(hits: pd.DataFrame, dataset: str, date_filter: QueryDateFilter | None) -> tuple[pd.DataFrame, bool]:
    if date_filter is None or hits.empty or dataset not in ["notices", "schedule", "rules", "meals"]:
        return hits, False

    if dataset == "schedule":
        filtered, was_eliminated = _filter_schedule_date_range(hits, date_filter)
        filtered.drop(columns=["_schedule_start_ts", "_schedule_end_ts"], inplace=True, errors="ignore")
        return filtered, was_eliminated

    date_column = "published_at"
    if dataset == "notices" and getattr(date_filter, "kind", "published") == "deadline":
        date_column = "apply_deadline"

    if date_column not in hits.columns:
        if date_column == "apply_deadline":
            return hits.iloc[:0].copy(), len(hits) > 0
        return hits, False

    filtered = hits.copy()
    filtered["_temp_date"] = pd.to_datetime(filtered[date_column], errors="coerce")
    dated_mask = filtered["_temp_date"].notna()
    in_range_mask = (
        (filtered["_temp_date"] >= pd.Timestamp(date_filter.start))
        & (filtered["_temp_date"] <= pd.Timestamp(date_filter.end))
    )
    filtered["date_unknown_auxiliary"] = 0

    in_range = filtered[in_range_mask].copy()
    unknown_dates = filtered[~dated_mask].copy()
    was_eliminated = len(hits) > 0 and in_range.empty

    target_count = min(DEFAULT_TOP_K, len(hits))
    if not unknown_dates.empty and len(in_range) < target_count:
        unknown_dates["date_unknown_auxiliary"] = 1
        filtered = pd.concat([in_range, unknown_dates.head(target_count - len(in_range))], ignore_index=True)
    else:
        filtered = in_range

    filtered.drop(columns=["_temp_date"], inplace=True, errors="ignore")
    return filtered, was_eliminated


def _apply_schedule_calendar_alignment(hits: pd.DataFrame, query: str) -> pd.DataFrame:
    """Use schedule dates to honor an explicit academic year/semester."""
    if hits.empty or "schedule_start" not in hits.columns:
        return hits

    year_match = re.search(r"\b(20\d{2})\s*(?:학년도|년도|년)?", query)
    semester_match = re.search(r"([12])\s*학기", query)
    if year_match is None and semester_match is None:
        return hits

    aligned = hits.copy()
    starts = pd.to_datetime(aligned["schedule_start"], errors="coerce")
    adjustment = pd.Series(0.0, index=aligned.index)
    if year_match is not None:
        requested_year = int(year_match.group(1))
        has_date = starts.notna()
        adjustment += pd.Series(
            np.where(~has_date, 0.0, np.where(starts.dt.year == requested_year, 0.05, -0.05)),
            index=aligned.index,
        )
    if semester_match is not None:
        requested_semester = int(semester_match.group(1))
        is_requested_semester = (
            starts.dt.month.between(1, 6)
            if requested_semester == 1
            else starts.dt.month.between(7, 12)
        )
        has_date = starts.notna()
        adjustment += pd.Series(
            np.where(~has_date, 0.0, np.where(is_requested_semester, 0.12, -0.12)),
            index=aligned.index,
        )

    aligned["hybrid_score"] = (
        pd.to_numeric(aligned.get("hybrid_score"), errors="coerce").fillna(0.0)
        + adjustment
    )
    return aligned.sort_values("hybrid_score", ascending=False, kind="stable").reset_index(drop=True)


def _matches_where_filter(row: pd.Series, where_filter: Dict | None) -> bool:
    if not where_filter:
        return True

    for key, condition in where_filter.items():
        if key == "$and":
            if not all(_matches_where_filter(row, sub) for sub in condition):
                return False
            continue
        if key == "$or":
            if not any(_matches_where_filter(row, sub) for sub in condition):
                return False
            continue

        value = row.get(key) if key in row.index else None
        if isinstance(condition, dict):
            for op, expected in condition.items():
                if op == "$eq" and str(value) != str(expected):
                    return False
                if op == "$ne" and str(value) == str(expected):
                    return False
                if op == "$in" and str(value) not in {str(item) for item in expected}:
                    return False
                if op not in {"$eq", "$ne", "$in"}:
                    return False
        elif str(value) != str(condition):
            return False
    return True


def _latest_notice_hits(
    *,
    chunks_df: pd.DataFrame,
    top_k: int,
    where_filter: Dict | None = None,
    date_filter: QueryDateFilter | None = None,
    title_terms: List[str] | None = None,
) -> pd.DataFrame:
    """최신 공지 질의는 유사도 검색 없이 게시일 역순으로 직접 조회한다.

    ``recent``는 고정된 최근 N일 범위가 아니라 "현재 색인에서 가장 최신"이라는
    정렬 의도다. 다만 오늘/이번 달/특정 월처럼 명시적인 날짜 범위가 함께 있으면
    그 범위 안에서 최신순으로 반환한다.
    """
    if chunks_df.empty or "published_at" not in chunks_df.columns:
        return chunks_df.iloc[:0].copy()

    eligible = chunks_df.copy()
    if where_filter:
        eligible = eligible[eligible.apply(lambda row: _matches_where_filter(row, where_filter), axis=1)].copy()
    if eligible.empty:
        return eligible

    if title_terms:
        normalized_terms = [re.sub(r"\s+", "", str(term).lower()) for term in title_terms if str(term).strip()]
        if normalized_terms:
            title_series = (
                eligible.get("title", pd.Series("", index=eligible.index))
                .fillna("")
                .astype(str)
                .str.replace(r"\s+", "", regex=True)
                .str.lower()
            )
            eligible = eligible[title_series.apply(lambda title: any(term in title for term in normalized_terms))].copy()
            if eligible.empty:
                return eligible
            # Every notice chunk repeats its title prefix.  For a detailed
            # operational question, prefer the first chunk whose *body* has
            # the requested term; parent-context expansion can then include
            # the nearby date/time rather than stopping at the title chunk.
            def body_term_match(value: object) -> int:
                text = str(value or "")
                if text.startswith("[") and "]\n\n" in text:
                    text = text.split("]\n\n", 1)[1]
                compact_body = re.sub(r"\s+", "", text).lower()
                return int(any(term in compact_body for term in normalized_terms))

            eligible["_title_term_body_match"] = eligible.get(
                "chunk_text", pd.Series("", index=eligible.index)
            ).apply(body_term_match)
        else:
            eligible["_title_term_body_match"] = 0
    else:
        eligible["_title_term_body_match"] = 0

    eligible["_published_ts"] = pd.to_datetime(eligible["published_at"], errors="coerce")
    eligible = eligible[eligible["_published_ts"].notna()].copy()
    if eligible.empty:
        return eligible.drop(columns=["_published_ts"], errors="ignore")

    if date_filter is not None and date_filter.label != "recent":
        start = pd.Timestamp(date_filter.start)
        end = pd.Timestamp(date_filter.end)
        eligible = eligible[
            (eligible["_published_ts"] >= start) & (eligible["_published_ts"] <= end)
        ].copy()
        if eligible.empty:
            return eligible.drop(columns=["_published_ts"], errors="ignore")

    # 한 공지가 여러 청크로 나뉘어도 목록에는 한 번만 노출한다. 첫 청크(position=0)를
    # 우선 선택하면 이후 parent-context 확장도 같은 문서의 전체 내용을 복원할 수 있다.
    if "position" in eligible.columns:
        eligible["_position"] = pd.to_numeric(eligible["position"], errors="coerce").fillna(0)
    else:
        eligible["_position"] = 0
    if "notice_id" in eligible.columns:
        eligible["_notice_order"] = pd.to_numeric(eligible["notice_id"], errors="coerce").fillna(-1)
    else:
        eligible["_notice_order"] = -1

    def notice_key(row: pd.Series) -> str:
        for column in ("notice_id", "doc_id", "document_key", "url"):
            value = _clean_response_value(row.get(column))
            if value is not None and str(value).strip():
                return f"{column}:{value}"
        return "fallback:" + "|".join(
            str(row.get(column, "")) for column in ("topics", "published_at", "title")
        )

    eligible["_notice_key"] = eligible.apply(notice_key, axis=1)
    eligible.sort_values(
        by=["_published_ts", "_notice_order", "_title_term_body_match", "_position"],
        ascending=[False, False, False, True],
        kind="stable",
        inplace=True,
    )
    eligible.drop_duplicates(subset=["_notice_key"], keep="first", inplace=True)
    eligible = eligible.head(top_k).copy()

    if "title" not in eligible.columns:
        eligible["title"] = eligible["chunk_text"].apply(_extract_chunk_title)
    else:
        missing_title = eligible["title"].isna() | eligible["title"].astype(str).str.strip().eq("")
        if missing_title.any() and "chunk_text" in eligible.columns:
            eligible.loc[missing_title, "title"] = eligible.loc[missing_title, "chunk_text"].apply(_extract_chunk_title)

    for column in ("topics", "category", "published_at", "apply_deadline", "url", "source", "notice_id"):
        if column not in eligible.columns:
            eligible[column] = ""
        else:
            eligible[column] = eligible[column].fillna("")

    # 최신 목록 조회는 의미 유사도 점수가 적용되지 않는다. 검증/폴백 파이프라인과의
    # 호환성을 위해 결정적 조회 결과임을 나타내는 중립 점수를 부여한다.
    eligible["hybrid_score"] = 1.0
    eligible["vector_score"] = 0.0
    eligible["sparse_score"] = 0.0
    eligible.drop(
        columns=["_published_ts", "_notice_order", "_title_term_body_match", "_position", "_notice_key"],
        inplace=True,
        errors="ignore",
    )
    return eligible.reset_index(drop=True)


def _filter_schedule_date_range(
    schedule_df: pd.DataFrame,
    date_filter: QueryDateFilter,
) -> tuple[pd.DataFrame, bool]:
    """Keep schedule rows whose effective period overlaps the requested range."""
    if schedule_df.empty or "schedule_start" not in schedule_df.columns:
        return schedule_df.iloc[:0].copy(), not schedule_df.empty

    filtered = schedule_df.copy()
    filtered["_schedule_start_ts"] = pd.to_datetime(filtered["schedule_start"], errors="coerce")
    if "schedule_end" in filtered.columns:
        filtered["_schedule_end_ts"] = pd.to_datetime(filtered["schedule_end"], errors="coerce")
    else:
        filtered["_schedule_end_ts"] = pd.NaT
    filtered["_schedule_end_ts"] = filtered["_schedule_end_ts"].fillna(filtered["_schedule_start_ts"])

    requested_start = pd.Timestamp(date_filter.start)
    requested_end = pd.Timestamp(date_filter.end)
    overlaps = (
        filtered["_schedule_start_ts"].notna()
        & (filtered["_schedule_start_ts"] <= requested_end)
        & (filtered["_schedule_end_ts"] >= requested_start)
    )
    in_range = filtered[overlaps].copy()
    return in_range, len(schedule_df) > 0 and in_range.empty


def _schedule_date_hits(
    *,
    chunks_df: pd.DataFrame,
    top_k: int,
    date_filter: QueryDateFilter,
) -> pd.DataFrame:
    """Return a chronological schedule list before semantic top-k truncation."""
    eligible, _ = _filter_schedule_date_range(chunks_df, date_filter)
    if eligible.empty:
        return eligible.drop(columns=["_schedule_start_ts", "_schedule_end_ts"], errors="ignore")

    eligible.sort_values(
        by=["_schedule_start_ts", "_schedule_end_ts"],
        ascending=[True, True],
        kind="stable",
        inplace=True,
    )
    if "chunk_id" in eligible.columns:
        eligible.drop_duplicates(subset=["chunk_id"], keep="first", inplace=True)
    eligible = eligible.head(top_k).copy()
    eligible["hybrid_score"] = 1.0
    eligible["vector_score"] = 0.0
    eligible["sparse_score"] = 0.0
    eligible.drop(columns=["_schedule_start_ts", "_schedule_end_ts"], inplace=True, errors="ignore")
    return eligible.reset_index(drop=True)


def _extract_chunk_title(text: object) -> str:
    if not isinstance(text, str) or not text.strip():
        return ""
    if text.startswith("[") and "]" in text:
        return text[1:text.index("]")].strip()
    return text.split("\n", 1)[0].strip()[:120]


def _empty_deadline_notice_hits() -> pd.DataFrame:
    return pd.DataFrame(columns=DEADLINE_NOTICE_HIT_COLUMNS)


def _deadline_filter_rank_notices(
    *,
    chunks_df: pd.DataFrame,
    vectorizer: object,
    matrix: object,
    tfidf_chunk_ids: list | None,
    query: str,
    date_filter: QueryDateFilter,
    top_k: int,
    where_filter: Dict | None = None,
) -> pd.DataFrame:
    """공지 마감일 질의는 먼저 deadline 범위로 recall을 확보한 뒤 TF-IDF로 정렬한다."""
    if chunks_df.empty or "apply_deadline" not in chunks_df.columns or "chunk_id" not in chunks_df.columns:
        return _empty_deadline_notice_hits()

    deadlines = pd.to_datetime(chunks_df["apply_deadline"], errors="coerce")
    start = pd.Timestamp(date_filter.start)
    end = pd.Timestamp(date_filter.end)
    mask = deadlines.notna() & (deadlines >= start) & (deadlines <= end)

    eligible = chunks_df.loc[mask].copy()
    if eligible.empty:
        return _empty_deadline_notice_hits()

    eligible["_deadline_ts"] = deadlines.loc[eligible.index]
    if where_filter:
        eligible = eligible[eligible.apply(lambda row: _matches_where_filter(row, where_filter), axis=1)].copy()
        if eligible.empty:
            return _empty_deadline_notice_hits()

    eligible["chunk_id"] = eligible["chunk_id"].astype(str)
    eligible["sparse_score"] = 0.0

    row_ids: list[str] | None = None
    matrix_rows = getattr(matrix, "shape", (0,))[0]
    if tfidf_chunk_ids is not None and len(tfidf_chunk_ids) == matrix_rows:
        row_ids = [str(chunk_id) for chunk_id in tfidf_chunk_ids]
    elif matrix_rows == len(chunks_df):
        row_ids = chunks_df["chunk_id"].astype(str).tolist()

    if row_ids is not None and matrix_rows:
        id_to_matrix_row: Dict[str, int] = {}
        for row_idx, chunk_id in enumerate(row_ids):
            id_to_matrix_row.setdefault(str(chunk_id), row_idx)

        matrix_positions: list[int] = []
        scored_ids: list[str] = []
        for chunk_id in eligible["chunk_id"].tolist():
            matrix_row = id_to_matrix_row.get(chunk_id)
            if matrix_row is None:
                continue
            matrix_positions.append(matrix_row)
            scored_ids.append(chunk_id)

        if matrix_positions:
            query_vector = vectorizer.transform([query])
            subset_matrix = matrix[matrix_positions]
            sparse_scores = cosine_similarity(query_vector, subset_matrix).ravel()
            sparse_by_id = {chunk_id: float(score) for chunk_id, score in zip(scored_ids, sparse_scores)}
            eligible["sparse_score"] = eligible["chunk_id"].map(sparse_by_id).fillna(0.0)

    eligible["vector_score"] = 0.0
    if end > start:
        span_seconds = max((end - start).total_seconds(), 1.0)
        deadline_position = (eligible["_deadline_ts"] - start).dt.total_seconds().clip(lower=0.0)
        eligible["_deadline_score"] = 1.0 - (deadline_position / span_seconds).clip(upper=1.0)
    else:
        eligible["_deadline_score"] = 1.0

    rank_score = (0.85 * eligible["sparse_score"]) + (0.15 * eligible["_deadline_score"])
    eligible["hybrid_score"] = (MIN_RETRIEVAL_SCORE + rank_score).clip(upper=1.0)
    eligible.sort_values(
        by=["hybrid_score", "sparse_score", "_deadline_ts"],
        ascending=[False, False, True],
        inplace=True,
    )

    if len(eligible) > 50:
        eligible = eligible.head(max(top_k, 50)).copy()

    eligible["title"] = eligible["chunk_text"].apply(_extract_chunk_title) if "chunk_text" in eligible.columns else ""
    for column in ("topics", "category", "published_at", "apply_deadline", "url", "source", "notice_id"):
        if column not in eligible.columns:
            eligible[column] = ""
        else:
            eligible[column] = eligible[column].fillna("")

    existing = [column for column in DEADLINE_NOTICE_HIT_COLUMNS if column in eligible.columns]
    return eligible[existing].reset_index(drop=True)


def _expand_chunk_with_neighbors(row: pd.Series) -> str:
    """검색된 청크에 같은 문서의 앞뒤 이웃 청크를 결합해 반환합니다 (parent-document 확장).

    검색은 작은 청크(정밀)로 하되 생성 근거는 더 넓게 제공해, 절차/기간 안내가
    청크 경계에서 잘려 LLM이 불완전한 근거로 답하는 문제를 줄인다.
    캐시된 chunks_df를 사용하므로 추가 I/O 비용이 없다. 실패 시 원본 청크 그대로.
    """
    chunk_text = str(row.get("chunk_text", ""))
    if not PARENT_CONTEXT_ENABLED:
        return chunk_text
    dataset = row.get("dataset")
    doc_id = row.get("doc_id")
    position = row.get("position")
    if not dataset or doc_id is None or position is None or pd.isna(position):
        return chunk_text

    cache = _datasets.get(str(dataset))
    if cache is None or cache.chunks.empty:
        return chunk_text
    df = cache.chunks
    if "doc_id" not in df.columns or "position" not in df.columns:
        return chunk_text

    try:
        pos = int(float(position))
        siblings = df[df["doc_id"].astype(str) == str(doc_id)]
        if len(siblings) <= 1:
            return chunk_text
        positions = siblings["position"].astype(float).astype(int)
        # Official tables often split a label, a category row, and its numeric
        # threshold across three consecutive chunks. Two siblings on either
        # side recover that value while the downstream context limit still
        # bounds total prompt size.
        window = siblings[positions.between(pos - 2, pos + 2)].copy()
        allow_wise_value = row.get("campus_allow_wise", False)
        allow_wise = allow_wise_value is True or str(allow_wise_value).strip().lower() in {"1", "true", "yes"}
        # Parent expansion reads from the unfiltered dataset cache, so every
        # sibling must cross the exact same campus boundary as the original hit.
        window, _ = apply_campus_safety_boundary(window, allow_wise=allow_wise)
        if window.empty:
            return ""
        window["_pos"] = window["position"].astype(float).astype(int)
        window.sort_values("_pos", inplace=True)

        parts: List[str] = []
        for _, sib in window.iterrows():
            text = str(sib.get("chunk_text", "")).strip()
            # 이웃 청크의 "[제목]" prefix는 중복이므로 제거(본문만 이어 붙임)
            if sib["_pos"] != pos and text.startswith("[") and "]\n\n" in text:
                text = text.split("]\n\n", 1)[1].strip()
            if text:
                parts.append(text)
        return "\n".join(parts) if parts else chunk_text
    except Exception:  # noqa: BLE001 — 확장 실패는 원본 청크로 무해하게 폴백
        return chunk_text


def _build_context_text(context_parts: List[str], limit: int, prefix: str = "") -> str:
    """문서 경계를 존중하며 컨텍스트를 limit 이내로 구성합니다.

    기존의 단순 슬라이싱(`text[:limit]`)은 마지막 문서를 중간에서 잘라
    LLM에 불완전한 근거를 제공했음. 한도를 넘기는 문서는 통째로 제외하되,
    첫 문서만은 (그것만으로 한도를 넘더라도) 포함 후 절단한다.
    """
    sep = "\n\n---\n\n"
    included: List[str] = []
    used = len(prefix)
    for part in context_parts:
        extra = len(part) + (len(sep) if included else 0)
        if included and used + extra > limit:
            break
        included.append(part)
        used += extra
    return (prefix + sep.join(included))[:limit]


def _build_document_context_part(row: pd.Series, citation_number: int) -> str:
    source = _clean_response_str(row.get("source")) or _clean_response_str(row.get("dataset")) or "알 수 없음"
    part = f"문서 {citation_number} [출처: {source}]:\n"
    title = _clean_response_str(row.get("title"))
    published_at = _clean_response_str(row.get("published_at"))
    url = _clean_response_str(row.get("url"))
    if title:
        part += f"제목: {title}\n"
    if published_at:
        part += f"게시일: {published_at}\n"
    if url:
        part += f"URL: {url}\n"
    campus_scope = _clean_response_str(row.get("campus_scope"))
    if campus_scope:
        part += f"캠퍼스 범위: {campus_scope}\n"
    part += f"내용:\n{_expand_chunk_with_neighbors(row)}\n"

    attachments_str = row.get("attachments")
    if isinstance(attachments_str, str) and attachments_str.strip():
        try:
            attachments = json.loads(attachments_str)
            if isinstance(attachments, list):
                links = [
                    f"- [{attachment['name']}]({attachment['url']})"
                    for attachment in attachments
                    if isinstance(attachment, dict) and "name" in attachment and "url" in attachment
                ]
                if links:
                    part += "\n첨부파일:\n" + "\n".join(links) + "\n"
        except (json.JSONDecodeError, TypeError) as exc:
            _log_event(logging.WARNING, "attachment_parse_failed", error=str(exc))
    return part


def _build_selected_evidence_context(selected: pd.DataFrame, prefix: str = "") -> str:
    if selected.empty:
        return prefix[:MAX_CONTEXT_LENGTH]
    group_count = int(pd.to_numeric(selected["evidence_group"], errors="coerce").max())
    if group_count <= 1:
        parts = [
            _build_document_context_part(row, int(row.get("citation_number") or index))
            for index, (_, row) in enumerate(selected.iterrows(), start=1)
        ]
        if "selector_fallback" in selected.columns and selected["selector_fallback"].astype(bool).any():
            remaining = max(0, MAX_CONTEXT_LENGTH - len(prefix))
            per_document_budget = max(1, remaining // len(parts))
            return (prefix + "\n\n---\n\n".join(part[:per_document_budget] for part in parts))[:MAX_CONTEXT_LENGTH]
        return _build_context_text(parts, MAX_CONTEXT_LENGTH, prefix=prefix)

    remaining = max(0, MAX_CONTEXT_LENGTH - len(prefix))
    per_group_budget = max(1, remaining // group_count)
    blocks: list[str] = []
    for group_number in range(1, group_count + 1):
        group_frame = selected[selected["evidence_group"] == group_number]
        parts = [
            _build_document_context_part(row, int(row.get("citation_number") or index))
            for index, (_, row) in enumerate(group_frame.iterrows(), start=1)
        ]
        blocks.append(
            _build_context_text(
                parts,
                per_group_budget,
                prefix=f"[근거 그룹 {group_number}]\n",
            )
        )
    return (prefix + "\n\n".join(blocks))[:MAX_CONTEXT_LENGTH]


def _build_grounding_confirmation_answer(result, sources: List[SourceChunk]) -> str:
    reason = getattr(result, "reason", None)
    score = getattr(result, "score", None)
    score_text = f" 근거 일치도는 약 {round(score * 100)}%입니다." if isinstance(score, (int, float)) else ""
    reason_text = f"\n\n검토 사유: {reason}" if isinstance(reason, str) and reason.strip() else ""

    source_lines: List[str] = []
    for index, source in enumerate(sources[:3], start=1):
        citation = source.citation_number or index
        title = source.title or source.metadata.get("title") or source.source
        if source.url:
            source_lines.append(f"- [문서{citation}] {title}: {source.url}")
        else:
            source_lines.append(f"- [문서{citation}] {title}")

    source_text = "\n\n확인할 공식 출처:\n" + "\n".join(source_lines) if source_lines else ""
    return (
        "확인 필요: 검색된 공식 자료만으로는 방금 생성한 답변을 충분히 뒷받침하기 어렵습니다."
        f"{score_text} 아래 출처에서 원문을 확인한 뒤 판단해 주세요."
        f"{reason_text}"
        f"{source_text}"
    )


async def _retrieve_frames(
    *,
    route: List[str],
    query: str,
    final_where_filter: Dict,
    notice_board_filter: str | None,
    date_filter: QueryDateFilter | None,
    entry_year: int | None,
    request_id: str,
    recent_notice_query: bool = False,
    current_operational_notice_terms: List[str] | None = None,
    allow_wise: bool = False,
) -> tuple[List[pd.DataFrame], bool, List[str]]:
    frames: List[pd.DataFrame] = []
    date_filter_eliminated_any = False
    unavailable_datasets: List[str] = []

    for dataset in route:
        try:
            chunks_df, vectorizer, matrix, tfidf_chunk_ids = await run_in_threadpool(_ensure_dataset, dataset)
        except (KeyError, FileNotFoundError, ValueError) as exc:
            unavailable_datasets.append(dataset)
            _log_event(
                logging.ERROR,
                "retrieval_dataset_unavailable",
                request_id=request_id,
                dataset=dataset,
                error=str(exc),
            )
            continue

        artifacts = DATASET_ARTIFACTS[dataset]
        current_dataset_filter = final_where_filter.copy()
        if dataset != "courses":
            current_dataset_filter.pop("major", None)
            current_dataset_filter.pop("$or", None)
        if dataset == "notices" and notice_board_filter:
            current_dataset_filter["topics"] = {"$eq": notice_board_filter}
        final_filter = current_dataset_filter if current_dataset_filter else None
        # 학식은 코퍼스가 작고 날짜 필터로 좁혀지므로, 해당 주의 모든 식당이 후보에
        # 남도록 top_k를 키운다(작게 잡으면 그날 운영 중인 식당이 잘려 휴무로 오인됨).
        dataset_top_k = DEFAULT_TOP_K * 2
        if dataset == "meals":
            dataset_top_k = max(dataset_top_k, 40)
        if (
            dataset == "notices"
            and (recent_notice_query or current_operational_notice_terms)
            and getattr(date_filter, "kind", "published") != "deadline"
        ):
            hits = await run_in_threadpool(
                functools.partial(
                    _latest_notice_hits,
                    chunks_df=chunks_df,
                    top_k=dataset_top_k,
                    where_filter=final_filter,
                    # "내일 수강신청"의 내일은 공지 게시일이 아니라 행사 시점이다.
                    # 운영성 조회는 최신 제목 일치 공지를 먼저 고른다.
                    date_filter=None if current_operational_notice_terms else date_filter,
                    title_terms=current_operational_notice_terms,
                )
            )
            eliminated = (
                hits.empty
                and date_filter is not None
                and getattr(date_filter, "label", None) != "recent"
            )
        elif dataset == "schedule" and date_filter is not None:
            hits = await run_in_threadpool(
                functools.partial(
                    _schedule_date_hits,
                    chunks_df=chunks_df,
                    top_k=dataset_top_k,
                    date_filter=date_filter,
                )
            )
            eliminated = hits.empty
        elif dataset == "notices" and date_filter is not None and getattr(date_filter, "kind", "published") == "deadline":
            search_func = functools.partial(
                _deadline_filter_rank_notices,
                chunks_df=chunks_df,
                vectorizer=vectorizer,
                matrix=matrix,
                tfidf_chunk_ids=tfidf_chunk_ids,
                query=query,
                date_filter=date_filter,
                top_k=dataset_top_k,
                where_filter=final_filter,
            )
            hits = await run_in_threadpool(search_func)
            eliminated = False
        else:
            search_func = functools.partial(
                hybrid_search_with_meta,
                collection_name=artifacts.collection,
                chunks_df=chunks_df,
                tfidf_vectorizer=vectorizer,
                tfidf_matrix=matrix,
                query=query,
                top_k=dataset_top_k,
                alpha=HYBRID_ALPHA,
                where_filter=final_filter,
                tfidf_chunk_ids=tfidf_chunk_ids,
            )
            hits = await run_in_threadpool(search_func)
            hits, eliminated = _apply_date_filter(hits, dataset, date_filter)
            if dataset == "schedule":
                hits = _apply_schedule_calendar_alignment(hits, query)
        hits, campus_blocked = apply_campus_safety_boundary(hits, allow_wise=allow_wise)
        if campus_blocked:
            _log_event(
                logging.INFO,
                "retrieval_campus_blocked",
                request_id=request_id,
                dataset=dataset,
                blocked_count=campus_blocked,
                allow_wise=allow_wise,
            )
        date_filter_eliminated_any = date_filter_eliminated_any or eliminated

        _log_event(
            logging.INFO,
            "retrieval_dataset_completed",
            request_id=request_id,
            dataset=dataset,
            filter=final_filter,
            date_filter=None if date_filter is None else date_filter.label,
            hits=len(hits),
        )

        if not hits.empty:
            hits["dataset"] = dataset
            frames.append(hits)

    return frames, date_filter_eliminated_any, unavailable_datasets


async def _retrieve_frames_for_queries(
    *,
    route: List[str],
    queries: List[str],
    final_where_filter: Dict,
    notice_board_filter: str | None,
    date_filter: QueryDateFilter | None,
    entry_year: int | None,
    request_id: str,
    recent_notice_query: bool = False,
    current_operational_notice_terms: List[str] | None = None,
    allow_wise: bool = False,
) -> tuple[List[pd.DataFrame], bool, List[str]]:
    all_frames: List[pd.DataFrame] = []
    date_filter_eliminated_any = False
    unavailable_datasets: List[str] = []

    for query_candidate in queries:
        frames, eliminated, unavailable = await _retrieve_frames(
            route=route,
            query=query_candidate,
            final_where_filter=final_where_filter,
            notice_board_filter=notice_board_filter,
            date_filter=date_filter,
            entry_year=entry_year,
            request_id=request_id,
            recent_notice_query=recent_notice_query,
            current_operational_notice_terms=current_operational_notice_terms,
            allow_wise=allow_wise,
        )
        for frame in frames:
            if frame.empty:
                continue
            tagged = frame.copy()
            tagged["matched_query"] = query_candidate
            all_frames.append(tagged)
        date_filter_eliminated_any = date_filter_eliminated_any or eliminated
        unavailable_datasets.extend(unavailable)

    return all_frames, date_filter_eliminated_any, list(dict.fromkeys(unavailable_datasets))


def _staff_enrichment_queries(question: str, frames: List[pd.DataFrame]) -> list[str]:
    """Derive official department contact lookups from first-hop evidence."""
    if not _is_staff_lookup_query(question, SEARCHABLE_DATASETS):
        return []

    departments: list[str] = []
    for frame in frames:
        if frame.empty or "department" not in frame.columns:
            continue
        ranked = frame
        if "hybrid_score" in frame.columns:
            ranked = frame.assign(
                _department_rank=pd.to_numeric(frame["hybrid_score"], errors="coerce").fillna(-1.0)
            ).sort_values("_department_rank", ascending=False, kind="stable")
        for raw in ranked["department"].head(5).tolist():
            if not isinstance(raw, str):
                continue
            for value in re.split(r"\s*(?:/|,|·|;|\|)\s*", raw):
                cleaned = re.sub(r"\s+", " ", value).strip(" -()")
                if not (2 <= len(cleaned) <= 30):
                    continue
                if cleaned in {"각 학과별", "각 학과", "해당 학과", "관련 부서"}:
                    continue
                if cleaned not in departments:
                    departments.append(cleaned)
                if len(departments) >= 4:
                    return [f"{department} 연락처" for department in departments]
    return [f"{department} 연락처" for department in departments]


async def _enrich_staff_lookup_frames(
    *,
    question: str,
    frames: List[pd.DataFrame],
    final_where_filter: Dict,
    entry_year: int | None,
    request_id: str,
    allow_wise: bool,
) -> tuple[List[pd.DataFrame], List[str]]:
    queries = _staff_enrichment_queries(question, frames)
    if not queries:
        return frames, []
    staff_frames, _, unavailable = await _retrieve_frames_for_queries(
        route=["staff"],
        queries=queries,
        final_where_filter=final_where_filter,
        notice_board_filter=None,
        date_filter=None,
        entry_year=entry_year,
        request_id=request_id,
        recent_notice_query=False,
        allow_wise=allow_wise,
    )
    _log_event(
        logging.INFO,
        "staff_lookup_enriched",
        request_id=request_id,
        derived_query_count=len(queries),
        added_frame_count=len(staff_frames),
    )
    return [*frames, *staff_frames], unavailable


def _coalesce_series(series: pd.Series):
    for value in series:
        cleaned = _clean_response_value(value)
        if cleaned is not None:
            return cleaned
    return None


def _merge_query_hits(frames: List[pd.DataFrame]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame()

    merged = pd.concat(frames, ignore_index=True)
    if merged.empty or "chunk_id" not in merged.columns:
        return merged

    aggregated_rows = []
    for chunk_id, group in merged.groupby("chunk_id", sort=False):
        row = group.iloc[0].copy()
        matched_queries = []
        for value in group.get("matched_query", pd.Series(dtype=str)).tolist():
            if isinstance(value, str) and value and value not in matched_queries:
                matched_queries.append(value)

        for column in group.columns:
            if column in {"chunk_id", "matched_query"}:
                continue
            if column in {"hybrid_score", "vector_score", "sparse_score"}:
                row[column] = pd.to_numeric(group[column], errors="coerce").max()
            else:
                row[column] = _coalesce_series(group[column])

        row["matched_queries"] = matched_queries
        row["matched_query_count"] = len(matched_queries)
        aggregated_rows.append(row)

    return pd.DataFrame(aggregated_rows)


def _build_balanced_shortlist(
    frames: List[pd.DataFrame],
    *,
    per_dataset: int = RAG_EVIDENCE_CANDIDATES_PER_DATASET,
    max_candidates: int = RAG_EVIDENCE_MAX_CANDIDATES,
) -> pd.DataFrame:
    """Fuse query ranks within each dataset, then interleave dataset-local winners.

    Hybrid scores from different corpora are intentionally never compared directly. Each
    query result contributes reciprocal-rank evidence inside its own dataset; a fixed quota
    and round-robin ordering keep one corpus from consuming the whole OpenAI shortlist.
    """
    per_dataset = max(1, min(int(per_dataset), 10))
    max_candidates = max(1, min(int(max_candidates), 30))
    ranked_frames: list[pd.DataFrame] = []
    for frame_order, frame in enumerate(frames):
        if frame.empty or "dataset" not in frame.columns:
            continue
        ranked = frame.copy()
        if "hybrid_score" in ranked.columns:
            ranked["_numeric_hybrid"] = pd.to_numeric(ranked["hybrid_score"], errors="coerce").fillna(-1.0)
            ranked.sort_values("_numeric_hybrid", ascending=False, kind="stable", inplace=True)
        else:
            ranked["_numeric_hybrid"] = -1.0
        if "sparse_score" in ranked.columns:
            ranked["_numeric_sparse"] = pd.to_numeric(ranked["sparse_score"], errors="coerce").fillna(0.0)
        else:
            ranked["_numeric_sparse"] = 0.0
        ranked["_retrieval_rank"] = range(1, len(ranked) + 1)
        ranked["_rrf_contribution"] = 1.0 / (60.0 + ranked["_retrieval_rank"])
        ranked["_frame_order"] = frame_order
        ranked_frames.append(ranked)

    if not ranked_frames:
        return pd.DataFrame()

    combined = pd.concat(ranked_frames, ignore_index=True)
    combined["_dataset_key"] = combined["dataset"].astype(str)
    if "chunk_id" not in combined.columns:
        combined["chunk_id"] = ""
    chunk_keys = combined["chunk_id"].fillna("").astype(str).str.strip()
    missing_key = chunk_keys.eq("")
    chunk_keys.loc[missing_key] = [f"row-{index}" for index in combined.index[missing_key]]
    combined["_candidate_key"] = combined["_dataset_key"] + ":" + chunk_keys

    rows: list[pd.Series] = []
    for _, group in combined.groupby("_candidate_key", sort=False):
        best = group.sort_values(
            ["_rrf_contribution", "_numeric_hybrid"],
            ascending=[False, False],
            kind="stable",
        ).iloc[0].copy()
        best["retrieval_fusion_score"] = float(group["_rrf_contribution"].sum())
        # The same chunk can arrive through multiple query variants. Preserve
        # its strongest exact-word signal even when another variant supplied
        # the row chosen for metadata.
        best["sparse_score"] = float(group["_numeric_sparse"].max())
        best["_numeric_sparse"] = float(group["_numeric_sparse"].max())
        matched_queries: list[str] = []
        if "matched_query" in group.columns:
            for value in group["matched_query"].tolist():
                if isinstance(value, str) and value and value not in matched_queries:
                    matched_queries.append(value)
        best["matched_queries"] = matched_queries
        best["matched_query_count"] = len(matched_queries) or 1
        rows.append(best)

    fused = pd.DataFrame(rows)
    selected: list[pd.DataFrame] = []
    dataset_order = {name: index for index, name in enumerate(SEARCHABLE_DATASETS)}
    for dataset, group in fused.groupby("dataset", sort=False):
        ranked_local = group.sort_values(
            ["retrieval_fusion_score", "_numeric_hybrid"],
            ascending=[False, False],
            kind="stable",
        )
        selected_indices: list[int] = []
        if not ranked_local.empty:
            selected_indices.append(int(ranked_local.index[0]))

        # Keep the strongest exact-word candidate in every corpus alongside
        # the fusion winner. This prevents a semantically related result from
        # consuming all three slots when an exact title/date match exists.
        lexical_ranked = group[group["_numeric_sparse"] > 0].sort_values(
            ["_numeric_sparse", "retrieval_fusion_score", "_numeric_hybrid"],
            ascending=[False, False, False],
            kind="stable",
        )
        if not lexical_ranked.empty:
            lexical_index = int(lexical_ranked.index[0])
            if lexical_index not in selected_indices:
                selected_indices.append(lexical_index)

        for index in ranked_local.index:
            numeric_index = int(index)
            if numeric_index not in selected_indices:
                selected_indices.append(numeric_index)
            if len(selected_indices) >= per_dataset:
                break
        local = group.loc[selected_indices[:per_dataset]].copy()
        local["dataset_rank"] = range(1, len(local) + 1)
        local["_dataset_order"] = dataset_order.get(str(dataset), len(dataset_order))
        selected.append(local)

    shortlist = pd.concat(selected, ignore_index=True)
    shortlist.sort_values(
        ["dataset_rank", "_dataset_order", "retrieval_fusion_score"],
        ascending=[True, True, False],
        kind="stable",
        inplace=True,
    )
    shortlist = shortlist.head(max(1, max_candidates)).reset_index(drop=True)
    shortlist["candidate_id"] = [f"c{index}" for index in range(1, len(shortlist) + 1)]
    return shortlist.drop(
        columns=[
            "_numeric_hybrid",
            "_numeric_sparse",
            "_retrieval_rank",
            "_rrf_contribution",
            "_frame_order",
            "_dataset_key",
            "_candidate_key",
            "_dataset_order",
        ],
        errors="ignore",
    )


_INTER_INSTITUTION_QUERY_RE = re.compile(
    r"(?:학점\s*교류|교류\s*(?:수학|대학)|타\s*대학|다른\s*대학|교환\s*학생|교환학생)"
)
_EXTERNAL_INSTITUTION_TITLE_RE = re.compile(
    r"(?P<name>[가-힣A-Za-z]{2,20}?)(?:대학교|대)(?!학)\s*(?:수학|학점\s*교류|교류)"
)
_DONGGUK_INSTITUTION_NAMES = {"동국", "동국서울", "동국WISE", "동국wise"}
_ENTRY_YEAR_SCOPE_QUERY_RE = re.compile(
    r"(?:\d{2,4}\s*학번|입학\s*년도|졸업\s*(?:요건|기준|학점)|이수\s*(?:기준|요건)|교과\s*과정)"
)
_GRADUATE_SCOPE_RE = re.compile(r"(?:대학원|대학원생|석사|박사)\s*")
_FOREIGN_STUDENT_AUDIENCE_RE = re.compile(r"(?:외국인|유학생|국제\s*학생|교환\s*학생)")
_CONTACT_QUERY_RE = re.compile(r"(?:전화(?:번호)?|연락처|문의처|대표번호)")
_PHONE_NUMBER_RE = re.compile(r"(?<!\d)0\d{1,2}\)?[-\s]?\d{3,4}[-\s]?\d{4}(?!\d)")
_LITERAL_SCOPE_ANCHORS = (
    "장바구니",
    "수강정정",
    "수강취소",
    "계절학기",
    "학점교류",
    "졸업유예",
    "조기졸업",
    "추천채용",
    "중앙동아리",
)


def _query_academic_period(question: str) -> tuple[int | None, str | None]:
    year_match = re.search(r"\b(20\d{2})(?:\s*학년도|\s*년도|\s*년)?", question)
    compact_semester = re.search(r"\b20\d{2}\s*[-./]\s*([12])\b", question)
    semester_match = compact_semester or re.search(r"([12])\s*학기", question)
    return (
        int(year_match.group(1)) if year_match else None,
        semester_match.group(1) if semester_match else None,
    )


def _candidate_academic_period(row: pd.Series) -> tuple[set[int], set[str]]:
    """Extract declared applicability, not merely a document publication year."""
    title = _clean_response_str(row.get("title")) or ""
    text_head = (_clean_response_str(row.get("chunk_text")) or "")[:320]

    def declared_period(text: str) -> tuple[set[int], set[str]]:
        years = {int(value) for value in re.findall(r"\b(20\d{2})\s*(?:학년도|학번|년)", text)}
        semesters = set(re.findall(r"([12])\s*학기", text))
        if re.search(r"(?:여름|겨울)\s*계절학기|계절\s*학기", text):
            semesters.add("seasonal")
        return years, semesters

    title_years, title_semesters = declared_period(title)
    body_years, body_semesters = declared_period(text_head)
    # The title is the document's declared applicability. Years mentioned in
    # body examples or comparison tables must not broaden a title such as
    # "2024학번 ..." into evidence for a 2022-cohort question.
    years = title_years or body_years
    semesters = title_semesters or body_semesters

    # Schedule rows have an explicit effective date even when their title omits
    # the year/semester. Publication dates are intentionally not used here:
    # an older regulation can remain effective unless it declares an old scope.
    if str(row.get("dataset") or "") == "schedule":
        start = pd.to_datetime(row.get("schedule_start"), errors="coerce")
        if pd.notna(start):
            if not years:
                years.add(int(start.year))
            if not semesters:
                semesters.add("1" if int(start.month) <= 6 else "2")
    return years, semesters


def _period_bound_response_instruction(question: str, selected: pd.DataFrame) -> str | None:
    """Prevent a historical notice from being phrased as the current procedure."""
    requested_year, requested_semester = _query_academic_period(question)
    if selected.empty or requested_year is not None or requested_semester is not None:
        return None

    current_year = kst_now().year
    historical_sources: list[str] = []
    for _, row in selected.iterrows():
        if str(row.get("dataset") or "") != "notices":
            continue
        years, semesters = _candidate_academic_period(row)
        if not years or max(years) >= current_year:
            continue
        citation = int(row.get("citation_number") or len(historical_sources) + 1)
        scope = ", ".join(f"{year}학년도" for year in sorted(years))
        if "seasonal" in semesters:
            scope += " 계절학기"
        historical_sources.append(f"문서{citation}({scope})")

    if not historical_sources:
        return None
    return (
        "기간 안전 제약: 사용자는 적용 학기를 지정하지 않았고, "
        f"{', '.join(historical_sources)}는 현재 연도보다 이전 기간의 안내입니다. "
        "답변 첫 문장에서 이 자료의 적용 기간을 먼저 밝히고, 현재 학기에도 같은 절차가 적용되는지는 "
        "제공된 자료만으로 확인되지 않는다고 명시하세요. 이후 내용을 설명할 때는 반드시 '해당 기간에는'처럼 "
        "과거 자료의 범위로 한정하고, 현재 사용자가 그대로 따라야 한다는 명령형 절차로 쓰지 마세요."
    )


def _external_institutions(row: pd.Series) -> set[str]:
    title = _clean_response_str(row.get("title")) or ""
    names = {match.group("name") for match in _EXTERNAL_INSTITUTION_TITLE_RE.finditer(title)}
    return {name for name in names if name not in _DONGGUK_INSTITUTION_NAMES and not name.startswith("동국")}


def _candidate_scope_text(row: pd.Series) -> str:
    title = _clean_response_str(row.get("title")) or ""
    text = _clean_response_str(row.get("chunk_text")) or ""
    return f"{title}\n{text[:1600]}".replace(" ", "")


def _candidate_restricted_audiences(row: pd.Series) -> set[str]:
    """Return audiences explicitly declared by the source title.

    Titles are used deliberately: a broad document may mention foreign
    students in one table row without being foreign-student-only evidence.
    """
    title = _clean_response_str(row.get("title")) or ""
    audiences: set[str] = set()
    if _FOREIGN_STUDENT_AUDIENCE_RE.search(title):
        audiences.add("foreign_student")
    return audiences


def _query_audiences(question: str) -> set[str]:
    audiences: set[str] = set()
    if _FOREIGN_STUDENT_AUDIENCE_RE.search(question):
        audiences.add("foreign_student")
    return audiences


def _candidate_has_phone_number(row: pd.Series) -> bool:
    return bool(_PHONE_NUMBER_RE.search(_candidate_scope_text(row)))


def _refine_final_candidate_scope(question: str, shortlist: pd.DataFrame) -> pd.DataFrame:
    """Remove deterministically wrong period/institution scope before selection.

    Undated/timeless evidence is retained. A conflicting declared year or
    semester is removed only when at least one candidate explicitly matches the
    requested period, so sparse corpora do not become empty merely because the
    newest known source is older.
    """
    if shortlist.empty:
        return shortlist
    refined = shortlist.copy()
    compact_question = question.replace(" ", "")
    requested_anchors = [
        value for value in _LITERAL_SCOPE_ANCHORS if value in compact_question
    ]

    # Entry-year guides answer cohort-specific graduation/curriculum questions,
    # not a generic current-semester registration question. A guide that is
    # the only exact source for a narrow operation named by the user remains
    # eligible; its cohort scope must then be explained in the answer.
    if not _ENTRY_YEAR_SCOPE_QUERY_RE.search(question):
        entry_year_mask = refined.apply(
            lambda row: (
                bool(re.search(r"\b20\d{2}\s*학번", _clean_response_str(row.get("title")) or ""))
                and not any(anchor in _candidate_scope_text(row) for anchor in requested_anchors)
            ),
            axis=1,
        )
        refined = refined[~entry_year_mask].copy()

    # A named partner-university exchange notice is not evidence for ordinary
    # Dongguk registration unless the user explicitly asks about inter-school
    # study/credit exchange.
    if not _INTER_INSTITUTION_QUERY_RE.search(question):
        external_mask = refined.apply(lambda row: bool(_external_institutions(row)), axis=1)
        refined = refined[~external_mask].copy()

    # A source whose title declares a restricted audience cannot answer an
    # unqualified question for the default student audience. If no general
    # source exists, returning no_results is safer than projecting the special
    # audience's dates or requirements onto everyone.
    requested_audiences = _query_audiences(question)
    audience_mismatch = refined.apply(
        lambda row: bool(_candidate_restricted_audiences(row) - requested_audiences),
        axis=1,
    )
    refined = refined[~audience_mismatch].copy()

    # When a user explicitly asks to call or contact a department, a staff row
    # without a number is incomplete evidence. Keep non-staff context, but
    # remove numberless staff rows whenever a phone-bearing staff candidate is
    # available in the same shortlist.
    if _CONTACT_QUERY_RE.search(question) and not refined.empty:
        staff_mask = (
            refined["dataset"].astype(str).eq("staff")
            if "dataset" in refined.columns
            else pd.Series(False, index=refined.index)
        )
        phone_mask = refined.apply(_candidate_has_phone_number, axis=1)
        if (staff_mask & phone_mask).any():
            refined = refined[~staff_mask | phone_mask].copy()

    # The default audience is an enrolled undergraduate. Graduate-only
    # schedules must not answer an unqualified student question when other
    # candidate evidence exists.
    if not _GRADUATE_SCOPE_RE.search(question):
        graduate_mask = refined.apply(
            lambda row: bool(_GRADUATE_SCOPE_RE.search(_candidate_scope_text(row))),
            axis=1,
        )
        if (~graduate_mask).any():
            refined = refined[~graduate_mask].copy()

    # Preserve exact operational scope. If the user names a narrow feature
    # and at least one document names it too, generic parent-topic documents
    # cannot replace that evidence merely because their date is newer.
    for anchor in requested_anchors:
        anchor_mask = refined.apply(lambda row: anchor in _candidate_scope_text(row), axis=1)
        if anchor_mask.any():
            refined = refined[anchor_mask].copy()

    requested_year, requested_semester = _query_academic_period(question)
    periods = {index: _candidate_academic_period(row) for index, row in refined.iterrows()}
    if requested_year is not None and any(requested_year in years for years, _ in periods.values()):
        year_keep = pd.Series(
            [not periods[index][0] or requested_year in periods[index][0] for index in refined.index],
            index=refined.index,
        )
        refined = refined[year_keep].copy()
    if requested_semester is not None:
        periods = {index: _candidate_academic_period(row) for index, row in refined.iterrows()}
        if any(requested_semester in semesters for _, semesters in periods.values()):
            semester_keep = pd.Series(
                [
                    not periods[index][1] or requested_semester in periods[index][1]
                    for index in refined.index
                ],
                index=refined.index,
            )
            refined = refined[semester_keep].copy()
    return refined.reset_index(drop=True)


def _normalize_evidence_groups(
    decision: EvidenceSelectionDecision,
    allowed_ids: set[str],
) -> list[list[str]]:
    groups: list[list[str]] = []
    globally_used: set[str] = set()
    for group in decision.groups[:5]:
        document_ids: list[str] = []
        for candidate_id in group.document_ids:
            clean_id = str(candidate_id).strip()
            if clean_id not in allowed_ids or clean_id in globally_used or clean_id in document_ids:
                continue
            document_ids.append(clean_id)
            globally_used.add(clean_id)
            if len(document_ids) >= 3:
                break
        if document_ids:
            groups.append(document_ids)
    return groups


def _materialize_evidence_groups(shortlist: pd.DataFrame, groups: list[list[str]]) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for group_number, candidate_ids in enumerate(groups, start=1):
        by_id = shortlist.set_index("candidate_id", drop=False)
        valid_ids = [candidate_id for candidate_id in candidate_ids if candidate_id in by_id.index]
        if not valid_ids:
            continue
        group_frame = by_id.loc[valid_ids].copy()
        if isinstance(group_frame, pd.Series):
            group_frame = group_frame.to_frame().T
        group_frame["evidence_group"] = group_number
        rows.append(group_frame.reset_index(drop=True))
    if not rows:
        return pd.DataFrame(columns=list(shortlist.columns) + ["evidence_group"])
    selected = pd.concat(rows, ignore_index=True)
    selected["citation_number"] = range(1, len(selected) + 1)
    return selected


def _fallback_query_terms(question: str) -> list[str]:
    """Extract conservative content terms for a safe selector fallback."""
    terms: list[str] = []
    for raw_term in re.findall(r"[가-힣A-Za-z]{2,}", question.lower()):
        term = raw_term
        for particle in ("으로", "에서", "에게", "에는", "은", "는", "을", "를", "이", "가", "의", "에", "와", "과", "도", "만", "로"):
            if term.endswith(particle) and len(term) > len(particle) + 1:
                term = term[: -len(particle)]
                break
        if term and term not in EVIDENCE_FALLBACK_STOP_TERMS and term not in terms:
            terms.append(term)
    return terms


def _deterministic_evidence_fallback(question: str, shortlist: pd.DataFrame) -> pd.DataFrame:
    """Retain only lexically supported evidence when selection is unsafe."""
    if shortlist.empty or "candidate_id" not in shortlist.columns:
        return shortlist

    terms = _fallback_query_terms(question)
    if not terms:
        return shortlist.iloc[:0].copy()

    candidate_text = shortlist.apply(
        lambda row: re.sub(
            r"\s+",
            "",
            " ".join(
                str(row.get(column) or "")
                for column in ("title", "chunk_text", "topics", "category")
            ).lower(),
        ),
        axis=1,
    )
    matched = shortlist[candidate_text.apply(lambda text: any(term in text for term in terms))].copy()
    if matched.empty:
        return shortlist.iloc[:0].copy()

    if "dataset_rank" in matched.columns:
        matched.sort_values("dataset_rank", ascending=True, kind="stable", inplace=True)
        fallback_ids = matched.groupby("dataset", sort=False)["candidate_id"].first().astype(str).tolist()
    else:
        fallback_ids = [str(matched.iloc[0]["candidate_id"])]
    if not fallback_ids:
        return shortlist.iloc[:0].copy()
    fallback = _materialize_evidence_groups(shortlist, [fallback_ids])
    fallback["selector_fallback"] = 1
    return fallback


async def _select_evidence_for_answer(
    question: str,
    shortlist: pd.DataFrame,
    usage_collector: list[dict],
) -> tuple[pd.DataFrame, bool]:
    """Return selected evidence and whether OpenAI selection fell back deterministically."""
    if shortlist.empty:
        return shortlist, False
    shortlist = _refine_final_candidate_scope(question, shortlist)
    if shortlist.empty:
        return shortlist, False
    candidates = [
        {
            "candidate_id": row.get("candidate_id"),
            "dataset": row.get("dataset"),
            "title": _clean_response_str(row.get("title")) or "",
            "published_at": _clean_response_str(row.get("published_at")) or "",
            "schedule_start": _clean_response_str(row.get("schedule_start")) or "",
            "campus_scope": _clean_response_str(row.get("campus_scope")) or "",
            "source_type": _clean_response_str(row.get("source_type")) or "",
            "text": _expand_chunk_with_neighbors(row),
        }
        for _, row in shortlist.iterrows()
    ]
    decision = await select_evidence_groups(question, candidates, usage_collector)
    if decision is None:
        return _deterministic_evidence_fallback(question, shortlist), True

    groups = _normalize_evidence_groups(decision, set(shortlist["candidate_id"].astype(str)))
    if not groups:
        # A syntactically valid empty decision can still be a false negative.
        # Retrieval and deterministic scope checks already passed, so preserve
        # bounded, cited evidence instead of converting it into no-results.
        return _deterministic_evidence_fallback(question, shortlist), True
    selected = _materialize_evidence_groups(shortlist, groups)
    selected["selector_fallback"] = 0
    return selected, False


def _select_latest_notice_evidence(shortlist: pd.DataFrame) -> pd.DataFrame:
    """Keep the newest notice rows in date order for a latest-notices query.

    A latest-notice request is a chronological list request, not a relevance
    comparison. Passing it to the general LLM selector can silently omit the
    newest row, so retain the first three board-filtered notice candidates
    deterministically.
    """
    if shortlist.empty or "dataset" not in shortlist.columns:
        return shortlist

    notices = shortlist[shortlist["dataset"] == "notices"].copy()
    if notices.empty or "candidate_id" not in notices.columns:
        return notices

    if "published_at" in notices.columns:
        notices["_published_ts"] = pd.to_datetime(notices["published_at"], errors="coerce")
        notices.sort_values("_published_ts", ascending=False, kind="stable", inplace=True)
    notices["_notice_key"] = notices["candidate_id"].astype(str)
    for column in ("notice_id", "doc_id", "url"):
        if column not in notices.columns:
            continue
        values = notices[column].fillna("").astype(str).str.strip()
        valid = values.ne("")
        notices.loc[valid, "_notice_key"] = f"{column}:" + values[valid]
        # Use the first stable document identity that is available.
        if valid.any():
            break
    notices.drop_duplicates(subset=["_notice_key"], keep="first", inplace=True)
    notices = notices.head(3)
    selected = _materialize_evidence_groups(
        shortlist,
        [notices["candidate_id"].astype(str).tolist()],
    )
    selected["selector_fallback"] = 0
    return selected


def _select_date_bound_schedule_evidence(shortlist: pd.DataFrame) -> pd.DataFrame:
    """Keep every chronological schedule item for an explicit date-range query."""
    if shortlist.empty or "dataset" not in shortlist.columns:
        return shortlist

    schedules = shortlist[shortlist["dataset"] == "schedule"].copy()
    if schedules.empty or "candidate_id" not in schedules.columns:
        return schedules

    if "schedule_start" in schedules.columns:
        schedules["_schedule_start_ts"] = pd.to_datetime(schedules["schedule_start"], errors="coerce")
        schedules.sort_values("_schedule_start_ts", ascending=True, kind="stable", inplace=True)
    if "chunk_id" in schedules.columns:
        schedules.drop_duplicates(subset=["chunk_id"], keep="first", inplace=True)
    selected = _materialize_evidence_groups(
        shortlist,
        [schedules["candidate_id"].astype(str).tolist()],
    )
    selected["selector_fallback"] = 0
    return selected


async def _select_answer_evidence(
    question: str,
    shortlist: pd.DataFrame,
    usage_collector: list[dict],
    *,
    recent_notice_query: bool,
    date_bound_schedule_query: bool = False,
) -> tuple[pd.DataFrame, bool]:
    """Select answer evidence while preserving chronological latest notices."""
    if recent_notice_query:
        return _select_latest_notice_evidence(shortlist), False
    if date_bound_schedule_query:
        return _select_date_bound_schedule_evidence(shortlist), False
    return await _select_evidence_for_answer(question, shortlist, usage_collector)


def _multiple_evidence_response_instructions(group_count: int) -> str | None:
    if group_count <= 1:
        return None
    return (
        f"검색된 근거가 {group_count}개의 독립적인 그룹으로 구분되었습니다. "
        f"정확히 {group_count}개 섹션을 '## 확인된 정보 1'부터 순서대로 작성하세요. "
        "모든 섹션은 동등한 위상이며 순위나 선호를 부여하지 마세요. "
        "각 섹션에서는 같은 번호의 [근거 그룹]에 속한 문서만 사용하고, 핵심 사실마다 [문서N] 출처를 표시하세요. "
        "그룹 간 내용이 충돌하면 그 사실을 숨기지 말고 객관적으로 밝혀 주세요."
    )


def _collect_matched_queries(merged: pd.DataFrame) -> List[str]:
    if merged.empty or "matched_queries" not in merged.columns:
        return []

    collected: List[str] = []
    for queries in merged["matched_queries"].tolist():
        if not isinstance(queries, list):
            continue
        for query in queries:
            if isinstance(query, str) and query and query not in collected:
                collected.append(query)
    return collected


def _prepare_merged_results(
    merged: pd.DataFrame,
    recent_notice_query: bool,
    policy: RetrievalPolicy,
    query: str,
    entry_year: int | None = None,
    user_major: str | None = None,
) -> pd.DataFrame:
    if merged.empty or "hybrid_score" not in merged.columns:
        return merged

    if "published_at" in merged.columns and "updated_at" in merged.columns:
        merged["sort_date"] = pd.to_datetime(merged["published_at"].fillna(merged["updated_at"]), errors="coerce")
    elif "published_at" in merged.columns:
        merged["sort_date"] = pd.to_datetime(merged["published_at"], errors="coerce")
    elif "updated_at" in merged.columns:
        merged["sort_date"] = pd.to_datetime(merged["updated_at"], errors="coerce")
    else:
        merged["sort_date"] = pd.NaT

    merged.dropna(subset=["hybrid_score"], inplace=True)
    if merged.empty:
        return merged

    min_hybrid = merged["hybrid_score"].min()
    max_hybrid = merged["hybrid_score"].max()
    if max_hybrid > min_hybrid:
        merged["norm_hybrid"] = (merged["hybrid_score"] - min_hybrid) / (max_hybrid - min_hybrid)
    else:
        merged["norm_hybrid"] = merged["hybrid_score"].clip(lower=0.0, upper=1.0)

    now = pd.Timestamp.now(tz="Asia/Seoul").tz_localize(None)
    merged["norm_recency"] = merged.apply(
        lambda row: _calculate_recency_score(row.get("sort_date"), row.get("dataset", ""), now),
        axis=1,
    )
    merged["sort_timestamp"] = merged["sort_date"].apply(
        lambda value: value.timestamp() if pd.notna(value) else float("-inf")
    )
    merged["final_score"] = (1 - RECENCY_WEIGHT) * merged["norm_hybrid"] + RECENCY_WEIGHT * merged["norm_recency"]
    if "date_unknown_auxiliary" in merged.columns:
        unknown_date_mask = pd.to_numeric(merged["date_unknown_auxiliary"], errors="coerce").fillna(0).astype(int).eq(1)
        if unknown_date_mask.any():
            merged.loc[unknown_date_mask, "final_score"] = merged.loc[unknown_date_mask, "final_score"] - 0.20
    if "matched_query_count" not in merged.columns:
        merged["matched_query_count"] = 1
    merged["query_match_bonus"] = (merged["matched_query_count"].clip(lower=1) - 1) * 0.03
    merged["final_score"] = merged["final_score"] + merged["query_match_bonus"]
    # 학식 휴무 청크 패널티: 메뉴/가격 질의에서 휴무일이 운영일을 밀어내지 않도록 약하게 강등한다.
    # (인덱스에는 남아 있어 '오늘 휴무?'·날짜필터된 질의에는 여전히 노출됨)
    if "is_closed" in merged.columns:
        closed_mask = merged["is_closed"].astype(str).isin({"1", "True", "true"})
        if closed_mask.any():
            merged.loc[closed_mask, "final_score"] = merged.loc[closed_mask, "final_score"] - 0.25
    if "source_type" in merged.columns:
        guide_mask = merged["source_type"].astype(str).eq(ENTRY_YEAR_GUIDE_SOURCE_TYPE)
        if guide_mask.any() and _has_entry_year_guide_intent(query):
            if entry_year is not None and "entry_year" in merged.columns:
                matched_year_mask = guide_mask & merged["entry_year"].astype(str).eq(str(entry_year))
                merged.loc[matched_year_mask, "final_score"] = merged.loc[matched_year_mask, "final_score"] + 0.12
            elif "entry_year" in merged.columns:
                latest_year = _latest_entry_year_in_frame(merged[guide_mask])
                if latest_year is not None:
                    latest_mask = guide_mask & merged["entry_year"].astype(str).eq(str(latest_year))
                    merged.loc[latest_mask, "final_score"] = merged.loc[latest_mask, "final_score"] + 0.06

    # 개인 학과 우선(courses 한정): 단과대 확장($or)으로 같은 단과대 형제학과 과목이 후보에
    # 섞이므로, 본인 학과(major==user_major) 과목 청크에 약한 가산점을 줘 형제학과 과목보다
    # 위에 오게 한다. courses에만 적용해 다른 데이터셋 순위에는 영향을 주지 않는다.
    if (
        RAG_COLLEGE_SCOPE_ENABLED
        and user_major
        and user_major not in _NO_MAJOR_SENTINELS
        and "major" in merged.columns
        and "dataset" in merged.columns
    ):
        own_major_mask = merged["dataset"].astype(str).eq("courses") & merged["major"].astype(str).eq(str(user_major))
        if own_major_mask.any():
            merged.loc[own_major_mask, "final_score"] = merged.loc[own_major_mask, "final_score"] + 0.10

    if recent_notice_query and policy.prefer_notices_with_dates:
        focus_terms = _extract_notice_focus_terms(query)
        if focus_terms:
            merged["notice_topic_match"] = merged.apply(
                lambda row: int(
                    row.get("dataset") == "notices"
                    and _row_matches_notice_focus_terms(row, focus_terms)
                ),
                axis=1,
            )
        else:
            merged["notice_topic_match"] = 0
        merged["recent_notice_priority"] = (
            (merged["dataset"] == "notices") & merged["sort_date"].notna()
        ).astype(int)
        merged.sort_values(
            by=["notice_topic_match", "matched_query_count", "recent_notice_priority", "sort_timestamp", "final_score", "hybrid_score"],
            ascending=[False, False, False, False, False, False],
            inplace=True,
        )
    else:
        merged.sort_values(by=["matched_query_count", "final_score", "hybrid_score"], ascending=[False, False, False], inplace=True)
        # 날짜 우선 정렬(recent_notice) 분기는 의도된 시간순 배치라 리랭커를 적용하지 않는다.
        merged = _apply_cross_encoder_rerank(merged, query)

    return merged


def _apply_cross_encoder_rerank(merged: pd.DataFrame, query: str) -> pd.DataFrame:
    """상위 후보를 cross-encoder로 정밀 재정렬합니다(RERANKER_ENABLED=1일 때만).

    hybrid_score는 변경하지 않으므로 폴백 임계(MIN_RETRIEVAL_SCORE) 판정에는 영향 없음.
    recency 가중은 유지: rerank 점수와 norm_recency를 기존 비율로 재혼합한다.
    """
    from src.services.reranker import is_reranker_enabled, rerank_scores

    if not is_reranker_enabled() or merged.empty or len(merged) < 2:
        return merged

    head_n = min(RERANKER_CANDIDATES, len(merged))
    top = merged.head(head_n).copy()
    scores = rerank_scores(query, top["chunk_text"].astype(str).tolist())
    if scores is None or len(scores) != len(top):
        return merged

    top["rerank_raw"] = scores
    lo, hi = min(scores), max(scores)
    top["rerank_norm"] = (top["rerank_raw"] - lo) / (hi - lo) if hi > lo else 0.5
    recency = top["norm_recency"] if "norm_recency" in top.columns else 0.0
    top["final_score"] = (1 - RECENCY_WEIGHT) * top["rerank_norm"] + RECENCY_WEIGHT * recency
    top.sort_values(by=["final_score", "hybrid_score"], ascending=[False, False], inplace=True)

    rest = merged.iloc[head_n:]
    return pd.concat([top, rest], ignore_index=True)


def _get_latest_document_published_at(cache: DatasetCache | None) -> str | None:
    if cache is None or cache.chunks.empty or "published_at" not in cache.chunks.columns:
        return None

    dates = pd.to_datetime(cache.chunks["published_at"], errors="coerce").dropna()
    if dates.empty:
        return None
    return dates.max().date().isoformat()


def _save_rag_evaluation_log(
    request_id: str,
    session_id: str,
    question: str,
    expanded_question: str,
    route: List[str],
    answer: str,
    fallback_triggered: bool,
    fallback_reason: str | None,
    date_filter_applied: bool,
    date_filter_relaxed: bool,
    analysis_intent: str | None,
    analysis_entities_json: str | None,
    analysis_time_focus: str | None,
    analysis_search_queries_json: str | None,
    analysis_needs_clarification: bool,
    analysis_clarification_reason: str | None,
    analysis_used: bool,
    analysis_failed: bool,
    matched_queries_json: str | None,
    top_hybrid_score: float | None,
    sources: List[SourceChunk],
    stage_timings: dict[str, float] | None = None,
    llm_usage: list[dict] | None = None,
) -> None:
    session = SessionLocal()
    try:
        query_log = RagQueryLog(
            request_id=request_id,
            session_id=session_id,
            question=question,
            expanded_question=expanded_question,
            route=json.dumps(route, ensure_ascii=False),
            answer=answer,
            fallback_triggered=fallback_triggered,
            fallback_reason=fallback_reason,
            date_filter_applied=date_filter_applied,
            date_filter_relaxed=date_filter_relaxed,
            analysis_intent=analysis_intent,
            analysis_entities_json=analysis_entities_json,
            analysis_time_focus=analysis_time_focus,
            analysis_search_queries_json=analysis_search_queries_json,
            analysis_needs_clarification=analysis_needs_clarification,
            analysis_clarification_reason=analysis_clarification_reason,
            analysis_used=analysis_used,
            analysis_failed=analysis_failed,
            matched_queries_json=matched_queries_json,
            top_hybrid_score=top_hybrid_score,
            source_count=len(sources),
            stage_timings_json=_json_or_none(stage_timings),
            llm_usage_json=_json_or_none(llm_usage),
            estimated_llm_cost_usd=_sum_estimated_llm_cost(llm_usage),
        )
        session.add(query_log)
        session.flush()

        for rank, source in enumerate(sources, start=1):
            session.add(
                RagRetrievalLog(
                    query_log_id=query_log.id,
                    rank=rank,
                    dataset=source.source,
                    chunk_id=source.chunk_id,
                    title=source.title,
                    url=source.url,
                    published_at=source.published_at,
                    vector_score=source.vector_score,
                    sparse_score=source.sparse_score,
                    hybrid_score=source.hybrid_score,
                    recency_score=source.recency_score,
                    final_score=source.final_score,
                    sort_date=source.sort_date or source.published_at,
                    snippet=source.snippet[:2000],
                )
            )

        session.commit()
    except Exception:
        session.rollback()
        _log_event(logging.ERROR, "rag_evaluation_log_failed", exc_info=True, request_id=request_id)
    finally:
        session.close()


def _update_grounding_log(request_id: str, result) -> None:
    session = SessionLocal()
    try:
        query_log = (
            session.query(RagQueryLog)
            .filter(RagQueryLog.request_id == request_id)
            .order_by(RagQueryLog.created_at.desc(), RagQueryLog.id.desc())
            .first()
        )
        if query_log is None:
            return
        query_log.grounding_checked = bool(result.checked)
        query_log.grounding_grounded = bool(result.grounded)
        query_log.grounding_score = result.score
        session.commit()
    except Exception:
        session.rollback()
        _log_event(logging.WARNING, "grounding_log_update_failed", exc_info=True, request_id=request_id)
    finally:
        session.close()


def _update_observability_log(
    request_id: str,
    stage_timings: dict[str, float] | None,
    llm_usage: list[dict] | None,
) -> None:
    session = SessionLocal()
    try:
        query_log = (
            session.query(RagQueryLog)
            .filter(RagQueryLog.request_id == request_id)
            .order_by(RagQueryLog.created_at.desc(), RagQueryLog.id.desc())
            .first()
        )
        if query_log is None:
            return
        query_log.stage_timings_json = _json_or_none(stage_timings)
        query_log.llm_usage_json = _json_or_none(llm_usage)
        query_log.estimated_llm_cost_usd = _sum_estimated_llm_cost(llm_usage)
        session.commit()
    except Exception:
        session.rollback()
        _log_event(logging.WARNING, "observability_log_update_failed", exc_info=True, request_id=request_id)
    finally:
        session.close()


def _save_feedback(feedback: FeedbackRequest) -> None:
    session = SessionLocal()
    try:
        session.add(
            RagFeedback(
                request_id=feedback.request_id,
                session_id=feedback.session_id,
                rating=feedback.rating,
                reason=feedback.reason,
                comment=None if feedback.comment is None else feedback.comment[:2000],
                major=feedback.major,
            )
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _format_mtime(path: Path) -> str | None:
    if not path.exists():
        return None
    from datetime import timezone, timedelta
    kst = timezone(timedelta(hours=9))
    return datetime.fromtimestamp(path.stat().st_mtime, tz=kst).isoformat()


def _build_notices_ingestion_status(session) -> dict:
    latest_run = (
        session.query(IngestionRun)
        .filter(IngestionRun.dataset == "notices")
        .order_by(IngestionRun.started_at.desc())
        .first()
    )
    latest_collection = (
        session.query(SourceDocument)
        .filter(SourceDocument.dataset == "notices")
        .order_by(SourceDocument.collected_at.desc())
        .first()
    )
    quality_rows = (
        session.query(DocumentQualityCheck.severity, func.count(DocumentQualityCheck.id))
        .join(
            SourceDocument,
            DocumentQualityCheck.document_key == SourceDocument.document_key,
        )
        .filter(SourceDocument.dataset == "notices")
        .group_by(DocumentQualityCheck.severity)
        .all()
    )
    latest_quality_checks = (
        session.query(DocumentQualityCheck)
        .join(
            SourceDocument,
            DocumentQualityCheck.document_key == SourceDocument.document_key,
        )
        .filter(SourceDocument.dataset == "notices")
        .order_by(DocumentQualityCheck.created_at.desc())
        .limit(5)
        .all()
    )
    parse_failed_count = (
        session.query(SourceDocument)
        .filter(
            SourceDocument.dataset == "notices",
            SourceDocument.status == "parse_failed",
        )
        .count()
    )
    raw_count = (
        session.query(SourceDocument)
        .filter(SourceDocument.dataset == "notices", SourceDocument.raw_payload_json.isnot(None))
        .count()
    )
    normalized_count = (
        session.query(SourceDocument)
        .filter(SourceDocument.dataset == "notices", SourceDocument.normalized_payload_json.isnot(None))
        .count()
    )
    linkage = build_notice_linkage_summary(session)

    return {
        "last_collection_at": None if latest_collection is None else latest_collection.collected_at.isoformat(),
        "last_successful_ingestion_at": (
            None
            if latest_run is None or latest_run.status not in {"success", "partial_success"}
            else latest_run.finished_at.isoformat() if latest_run.finished_at else None
        ),
        "ingestion_summary": {
            "status": None if latest_run is None else latest_run.status,
            "documents_seen": 0 if latest_run is None else latest_run.documents_seen,
            "documents_new": 0 if latest_run is None else latest_run.documents_new,
            "documents_updated": 0 if latest_run is None else latest_run.documents_updated,
            "documents_deleted": 0 if latest_run is None else latest_run.documents_deleted,
            "documents_failed": 0 if latest_run is None else latest_run.documents_failed,
        },
        "stage_summary": {
            "raw_documents": raw_count,
            "normalized_documents": normalized_count,
            "indexed_documents": linkage["indexable_linked_documents"],
            "active_documents": linkage["active_documents"],
            "updated_documents": linkage["updated_documents"],
            "index_mismatch": linkage["index_mismatch"],
        },
        "quality_summary": {
            "parse_failed": parse_failed_count,
            "severities": {severity: count for severity, count in quality_rows},
            "recent_checks": [
                {
                    "document_key": row.document_key,
                    "check_type": row.check_type,
                    "severity": row.severity,
                    "message": row.message,
                    "created_at": row.created_at.isoformat(),
                }
                for row in latest_quality_checks
            ],
        },
    }


def _ensure_dataset(key: str) -> Tuple[pd.DataFrame, object, object, list | None]:
    with _datasets_lock:
        return _ensure_dataset_locked(key)


def _ensure_dataset_locked(key: str) -> Tuple[pd.DataFrame, object, object, list | None]:
    artifacts = DATASET_ARTIFACTS.get(key)
    if artifacts is None:
        raise KeyError(f"Unsupported dataset '{key}'")
    
    chunk_path = artifacts.chunk_path
    csv_path = artifacts.csv_path
    vectorizer_path = VECTORIZER_DIR / f"{key}_tfidf.pkl"

    if not chunk_path.exists() and csv_path.exists():
        artifacts.chunk_path = csv_path
        chunk_path = csv_path

    chunk_mtime = chunk_path.stat().st_mtime if chunk_path.exists() else -1.0
    vectorizer_mtime = vectorizer_path.stat().st_mtime if vectorizer_path.exists() else -1.0

    cache = _datasets.get(key)
    if cache and cache.chunk_path == chunk_path and cache.chunk_mtime == chunk_mtime and cache.tfidf_mtime == vectorizer_mtime:
        return cache.chunks, cache.vectorizer, cache.matrix, cache.tfidf_chunk_ids

    tfidf_chunk_ids: list | None = None
    try:
        if chunk_path.exists() and vectorizer_path.exists():
            if chunk_path.suffix == ".csv":
                chunks_df = pd.read_csv(chunk_path)
            else:
                chunks_df = pd.read_parquet(chunk_path)
            tfidf_metadata = read_tfidf_metadata(key)
            artifact_version = tfidf_metadata.get("sklearn_version")
            if artifact_version and artifact_version != sklearn_version:
                _log_event(
                    logging.WARNING,
                    "tfidf_version_mismatch",
                    dataset=key,
                    artifact_version=artifact_version,
                    runtime_version=sklearn_version,
                )
            elif tfidf_metadata.get("is_legacy"):
                _log_event(logging.INFO, "tfidf_legacy_artifact_loaded", dataset=key)
            vectorizer, matrix, tfidf_chunk_ids = load_tfidf_with_ids(key)
        else:
            chunks_df, vectorizer, matrix = _DATASET_LOADERS[key]()
            # 방금 학습된 TF-IDF는 chunks_df 순서와 동일
            tfidf_chunk_ids = chunks_df["chunk_id"].astype(str).tolist() if not chunks_df.empty else None
            chunk_path = DATASET_ARTIFACTS[key].chunk_path
            chunk_mtime = chunk_path.stat().st_mtime if chunk_path.exists() else -1.0
            vectorizer_mtime = (VECTORIZER_DIR / f"{key}_tfidf.pkl").stat().st_mtime if (VECTORIZER_DIR / f"{key}_tfidf.pkl").exists() else -1.0
    except FileNotFoundError:
        chunks_df, vectorizer, matrix = _DATASET_LOADERS[key]()
        tfidf_chunk_ids = chunks_df["chunk_id"].astype(str).tolist() if not chunks_df.empty else None
        chunk_path = DATASET_ARTIFACTS[key].chunk_path
        chunk_mtime = chunk_path.stat().st_mtime if chunk_path.exists() else -1.0
        vectorizer_path = VECTORIZER_DIR / f"{key}_tfidf.pkl"
        vectorizer_mtime = vectorizer_path.stat().st_mtime if vectorizer_path.exists() else -1.0

    _datasets[key] = DatasetCache(
        chunks=chunks_df,
        vectorizer=vectorizer,
        matrix=matrix,
        chunk_path=chunk_path,
        chunk_mtime=chunk_mtime,
        tfidf_mtime=vectorizer_mtime,
        tfidf_chunk_ids=tfidf_chunk_ids,
    )
    return chunks_df, vectorizer, matrix, tfidf_chunk_ids


def refresh_runtime_dataset_state(targets: List[str] | None = None) -> dict[str, object]:
    """Reload refreshed artifacts and publish the same snapshot through `/ready`.

    Ingestion changes parquet/TF-IDF/Chroma on disk, while retrieval and
    readiness each retain process-local state.  Refreshing them together keeps
    the served corpus and the health report from drifting apart.
    """
    requested = list(dict.fromkeys(targets or list(_REQUIRED_DATASETS)))
    invalid = [key for key in requested if key not in _REQUIRED_DATASETS]
    if invalid:
        raise ValueError(f"Unsupported runtime refresh dataset(s): {invalid}")

    counts: dict[str, int] = {}
    dense_counts: dict[str, int] = {}
    dense_errors: dict[str, dict[str, str]] = {}
    errors: dict[str, dict[str, str]] = {}
    with _datasets_lock:
        for key in requested:
            _datasets.pop(key, None)

        for key in _REQUIRED_DATASETS:
            try:
                chunks, _, _, _ = _ensure_dataset_locked(key)
                count = len(chunks)
                if count <= 0:
                    raise ValueError(f"required dataset '{key}' contains no chunks")
                counts[key] = count
                try:
                    dense_count = count_items(DATASET_ARTIFACTS[key].collection)
                    dense_counts[key] = dense_count
                    if dense_count != count:
                        dense_errors[key] = {
                            "code": "dense_index_count_mismatch",
                            "type": "IndexAlignmentError",
                            "message": f"cached chunks={count}, dense vectors={dense_count}",
                        }
                except Exception as exc:  # noqa: BLE001 - keep serving sparse retrieval where possible
                    dense_errors[key] = _readiness_error("dense_index_probe_failed", exc)
            except Exception as exc:  # noqa: BLE001 - surface a per-dataset refresh error in readiness
                errors[key] = _readiness_error("runtime_dataset_reload_failed", exc)

    _set_readiness_check(
        "datasets",
        ready=not errors and len(counts) == len(_REQUIRED_DATASETS),
        detail=(
            "refresh_failed"
            if errors
            else "refreshed_degraded"
            if dense_errors
            else "refreshed"
        ),
        counts=counts,
        dense_counts=dense_counts,
        dense_errors=dense_errors,
        errors=errors,
    )
    quality_snapshot = _refresh_data_quality_readiness()
    _log_event(
        logging.INFO if not errors else logging.ERROR,
        "runtime_dataset_state_refreshed",
        targets=requested,
        counts=counts,
        dense_counts=dense_counts,
        errors=errors,
        data_quality_gate_passed=quality_snapshot.get("gate_passed"),
    )
    return {
        "targets": requested,
        "counts": counts,
        "dense_counts": dense_counts,
        "dense_errors": dense_errors,
        "errors": errors,
        "data_quality": quality_snapshot,
    }

def _validate_required_configuration() -> str:
    """명시적으로 필수 설정된 구성만 startup을 중단한다."""
    from src.config import OPENAI_API_KEY, RAG_REQUIRE_OPENAI_API_KEY

    if not OPENAI_API_KEY:
        message = (
            "OPENAI_API_KEY가 설정되지 않았습니다. 라우터/질의분석/기본 생성 프로바이더가 "
            "정상 동작하지 않을 수 있습니다."
        )
        if RAG_REQUIRE_OPENAI_API_KEY:
            raise RuntimeError(message)
        logging.warning("⚠️ %s", message)
        return "openai_key_optional_missing"
    return "ok"


def _refresh_data_quality_readiness() -> dict[str, object]:
    """Recompute the source-document quality check after a data mutation.

    The quality report is database-derived, unlike the loaded search caches.
    Keeping it only at startup makes ``/ready`` report stale warnings after a
    successful collection, repair, or reindex operation.
    """
    try:
        mode = data_quality_mode()
        session = SessionLocal()
        try:
            quality_report = build_source_document_quality_report(session, retry_limit=0)
        finally:
            session.close()
        gate_passed = bool(quality_report["gate_passed"])
        _set_readiness_check(
            "data_quality",
            required=mode == "strict",
            ready=gate_passed,
            detail="passed" if gate_passed else f"{mode}_threshold_exceeded",
            mode=mode,
            counts=quality_report["counts"],
            ratios=quality_report["ratios"],
            violations=quality_report["violations"],
            error=None,
        )
        return quality_report
    except Exception as exc:
        mode = os.getenv("RAG_DATA_QUALITY_MODE", "observe").strip().lower()
        if mode not in {"observe", "strict"}:
            mode = "observe"
        error = _readiness_error("data_quality_report_failed", exc)
        _set_readiness_check(
            "data_quality",
            required=mode == "strict",
            ready=False,
            detail="failed",
            mode=mode,
            error=error,
        )
        _log_event(
            logging.ERROR if mode == "strict" else logging.WARNING,
            "runtime_data_quality_refresh_failed",
            error=error,
            exc_info=True,
        )
        return {"gate_passed": False, "error": error}


def _run_required_startup_checks() -> None:
    """필수 컴포넌트를 준비하고 실패를 readiness 상태에 누적한다.

    DB·인덱스·임베딩 오류는 프로세스를 종료하지 않는다. liveness를 유지해야 `/ready`의
    구조화된 원인을 확인할 수 있고, 오케스트레이터는 503으로 트래픽을 차단할 수 있다.
    """
    try:
        config_detail = _validate_required_configuration()
        _set_readiness_check("configuration", ready=True, detail=config_detail, error=None)
    except Exception as exc:
        error = _readiness_error("required_configuration_invalid", exc)
        _set_readiness_check("configuration", ready=False, detail="failed", error=error)
        _log_event(logging.ERROR, "startup_component_failed", component="configuration", error=error)
        raise

    try:
        init_db()
        verify_database_writable()
        _set_readiness_check("database", ready=True, detail="initialized_and_writable", error=None)
        logging.info("✅ Database tables initialized and write lock verified.")
    except Exception as exc:
        error = _readiness_error("database_initialization_failed", exc)
        _set_readiness_check("database", ready=False, detail="failed", error=error)
        _log_event(logging.ERROR, "startup_component_failed", component="database", error=error, exc_info=True)

    _refresh_data_quality_readiness()

    dataset_counts: dict[str, int] = {}
    dense_counts: dict[str, int] = {}
    dense_errors: dict[str, dict[str, str]] = {}
    dataset_errors: dict[str, dict[str, str]] = {}
    for key in _REQUIRED_DATASETS:
        try:
            chunks, _, _, _ = _ensure_dataset(key)
            count = len(chunks)
            if count <= 0:
                raise ValueError(f"required dataset '{key}' contains no chunks")
            dataset_counts[key] = count
            try:
                dense_count = count_items(DATASET_ARTIFACTS[key].collection)
                dense_counts[key] = dense_count
                if dense_count != count:
                    dense_errors[key] = {
                        "code": "dense_index_count_mismatch",
                        "type": "IndexAlignmentError",
                        "message": f"cached chunks={count}, dense vectors={dense_count}",
                    }
            except Exception as exc:
                dense_errors[key] = _readiness_error("dense_index_probe_failed", exc)
            logging.info(f"✅ Dataset '{key}' successfully loaded.")
        except Exception as exc:
            error = _readiness_error("required_dataset_warmup_failed", exc)
            dataset_errors[key] = error
            _log_event(
                logging.ERROR,
                "startup_component_failed",
                component="dataset",
                dataset=key,
                error=error,
                exc_info=True,
            )
    _set_readiness_check(
        "datasets",
        ready=not dataset_errors and len(dataset_counts) == len(_REQUIRED_DATASETS),
        detail=(
            "failed"
            if dataset_errors
            else "loaded_degraded"
            if dense_errors
            else "loaded"
        ),
        counts=dataset_counts,
        dense_counts=dense_counts,
        dense_errors=dense_errors,
        errors=dataset_errors,
    )

    try:
        logging.info("⏳ Warming up embedding model...")
        embedder = get_embedder()
        if embedder is None:
            raise RuntimeError("embedding model loader returned no model")
        _set_readiness_check("embedder", ready=True, detail="loaded", error=None)
        logging.info("✅ Embedding model warmup completed.")
    except Exception as exc:
        error = _readiness_error("embedder_warmup_failed", exc)
        _set_readiness_check("embedder", ready=False, detail="failed", error=error)
        _log_event(logging.ERROR, "startup_component_failed", component="embedder", error=error, exc_info=True)

    _set_startup_complete()
    snapshot = _readiness_snapshot()
    _log_event(
        logging.INFO if snapshot["ready"] else logging.ERROR,
        "startup_readiness_completed",
        ready=snapshot["ready"],
        failures=snapshot["failures"],
    )


@app.on_event("startup")
def bootstrap_artifacts() -> None:
    """애플리케이션 시작 시 데이터셋과 분류기 등 주요 아티팩트를 미리 로드합니다."""
    logging.basicConfig(level=logging.INFO)
    _reset_startup_readiness()

    _log_event(
        logging.INFO,
        "runtime_versions",
        torch_version=_safe_package_version("torch"),
        transformers_version=_safe_package_version("transformers"),
        sentence_transformers_version=_safe_package_version("sentence-transformers"),
        sklearn_version=_safe_package_version("scikit-learn"),
    )

    _run_required_startup_checks()

    # 공지/학식 데이터 주기적 자동 갱신(RAG_SCHEDULER_ENABLED=1일 때만).
    try:
        from src.services.scheduler import start_scheduler
        if rag_config.RAG_SCHEDULER_ENABLED:
            start_scheduler()
            _set_readiness_check("scheduler", ready=True, detail="started", error=None)
        else:
            _set_readiness_check("scheduler", ready=True, detail="disabled", error=None)
    except Exception as exc:  # noqa: BLE001 — 스케줄러 실패가 서빙 부팅을 막지 않도록
        error = _readiness_error("scheduler_start_failed", exc)
        _set_readiness_check("scheduler", ready=False, detail="failed", error=error)
        _log_event(logging.WARNING, "startup_component_failed", component="scheduler", error=error, exc_info=True)


@app.on_event("shutdown")
def shutdown_scheduler_event() -> None:
    try:
        from src.services.scheduler import shutdown_scheduler
        shutdown_scheduler()
    except Exception:  # noqa: BLE001
        pass



# 제출 가능한 source_type과 각 타입의 필수(비공백) 필드
_SUBMIT_REQUIRED_FIELDS: Dict[str, List[str]] = {
    "custom_knowledge": ["question", "answer"],
    "event": ["title", "start_date"],
    "announcement": ["title", "content"],
}


def _extract_submitter_department(source_type: str, data: dict) -> str:
    """제출 payload에서 학과명을 일관되게 추출한다.

    지식(custom_knowledge)은 `category`, 행사/공지는 `department`에 학과명이 담겨 온다
    (프론트 DepartmentAdminPage 및 C# A9 확인). 키가 비어 있으면 다른 키로 폴백한다.
    """
    if source_type == "custom_knowledge":
        candidates = [data.get("category"), data.get("department")]
    else:
        candidates = [data.get("department"), data.get("category")]
    for value in candidates:
        if value and str(value).strip():
            return str(value).strip()
    return ""


@app.post("/admin/submit")
async def submit_pending(req: SubmitRequest):
    # K8: source_type 화이트리스트 + data JSON/필수 필드 검증 (fail-fast)
    source_type = (req.source_type or "").strip()
    if source_type not in _SUBMIT_REQUIRED_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"지원하지 않는 source_type입니다: '{source_type}'",
        )

    try:
        parsed = json.loads(req.data)
    except (json.JSONDecodeError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"data가 유효한 JSON이 아닙니다: {exc}")
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="data는 JSON 객체여야 합니다.")

    missing = [
        field
        for field in _SUBMIT_REQUIRED_FIELDS[source_type]
        if not str(parsed.get(field, "")).strip()
    ]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"필수 항목이 비어 있습니다: {', '.join(missing)}",
        )

    session = SessionLocal()
    try:
        item = PendingItem(
            source_type=source_type,
            data=req.data,
            status="pending"
        )
        session.add(item)
        session.commit()
        return {"status": "ok", "id": item.id}
    finally:
        session.close()


@app.get("/admin/pending")
async def list_pending():
    session = SessionLocal()
    try:
        items = session.query(PendingItem).filter(PendingItem.status == "pending").all()
        return items
    finally:
        session.close()


@app.get("/admin/items")
async def list_all_items():
    session = SessionLocal()
    try:
        items = session.query(PendingItem).order_by(PendingItem.created_at.desc()).all()
        logging.info(f"📋 [Admin] Listed {len(items)} items.")
        return items
    except Exception as e:
        logging.error(f"❌ [Admin] Failed to list items: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@app.get("/admin/rag-logs/export")
async def export_rag_logs(limit: int = 1000):
    safe_limit = min(max(limit, 1), 10000)
    session = SessionLocal()
    output = io.StringIO()
    output.write("\ufeff")

    fieldnames = [
        "query_log_id",
        "created_at",
        "request_id",
        "session_id",
        "question",
        "expanded_question",
        "route",
        "answer",
        "fallback_triggered",
        "fallback_reason",
        "date_filter_applied",
        "date_filter_relaxed",
        "top_hybrid_score",
        "source_count",
        "stage_timings_json",
        "llm_usage_json",
        "estimated_llm_cost_usd",
        "rank",
        "dataset",
        "chunk_id",
        "title",
        "url",
        "published_at",
        "vector_score",
        "sparse_score",
        "hybrid_score",
        "recency_score",
        "final_score",
        "sort_date",
        "snippet",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    try:
        query_logs = (
            session.query(RagQueryLog)
            .order_by(RagQueryLog.created_at.desc())
            .limit(safe_limit)
            .all()
        )

        for query_log in query_logs:
            base_row = {
                "query_log_id": query_log.id,
                "created_at": query_log.created_at,
                "request_id": query_log.request_id,
                "session_id": query_log.session_id,
                "question": query_log.question,
                "expanded_question": query_log.expanded_question,
                "route": query_log.route,
                "answer": query_log.answer,
                "fallback_triggered": query_log.fallback_triggered,
                "fallback_reason": query_log.fallback_reason,
                "date_filter_applied": query_log.date_filter_applied,
                "date_filter_relaxed": query_log.date_filter_relaxed,
                "top_hybrid_score": query_log.top_hybrid_score,
                "source_count": query_log.source_count,
                "stage_timings_json": query_log.stage_timings_json,
                "llm_usage_json": query_log.llm_usage_json,
                "estimated_llm_cost_usd": query_log.estimated_llm_cost_usd,
            }
            retrievals = sorted(query_log.retrievals, key=lambda item: item.rank or 0)
            if not retrievals:
                writer.writerow(base_row)
                continue

            for retrieval in retrievals:
                writer.writerow(
                    {
                        **base_row,
                        "rank": retrieval.rank,
                        "dataset": retrieval.dataset,
                        "chunk_id": retrieval.chunk_id,
                        "title": retrieval.title,
                        "url": retrieval.url,
                        "published_at": retrieval.published_at,
                        "vector_score": retrieval.vector_score,
                        "sparse_score": retrieval.sparse_score,
                        "hybrid_score": retrieval.hybrid_score,
                        "recency_score": retrieval.recency_score,
                        "final_score": retrieval.final_score,
                        "sort_date": retrieval.sort_date,
                        "snippet": retrieval.snippet,
                    }
                )
    finally:
        session.close()

    filename = f"rag_evaluation_logs_{kst_now().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/admin/rag/logs")
async def get_rag_logs(limit: int = 100):
    safe_limit = min(max(limit, 1), 1000)
    session = SessionLocal()
    try:
        logs = (
            session.query(RagQueryLog)
            .order_by(RagQueryLog.created_at.desc())
            .limit(safe_limit)
            .all()
        )
        result = [
            {
                "id": log.id,
                "question": log.question,
                "answer": log.answer,
                "fallback_triggered": log.fallback_triggered,
                "fallback_reason": log.fallback_reason,
                "session_id": log.session_id,
                "created_at": log.created_at.isoformat() if log.created_at else None,
                "route": log.route,
                "source_count": log.source_count,
                "stage_timings": None if not log.stage_timings_json else json.loads(log.stage_timings_json),
                "llm_usage": None if not log.llm_usage_json else json.loads(log.llm_usage_json),
                "estimated_llm_cost_usd": log.estimated_llm_cost_usd,
            }
            for log in logs
        ]
        logging.info(f"📋 [Admin] Returning {len(logs)} RAG logs.")
        return result
    except Exception as e:
        logging.error(f"❌ [Admin] Failed to fetch RAG logs: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@app.get("/admin/feedback")
async def get_admin_feedback(limit: int = 100, rating: int | None = None):
    if rating is not None and rating not in (1, -1):
        raise HTTPException(status_code=400, detail="rating must be 1 or -1")
    safe_limit = min(max(limit, 1), 1000)
    session = SessionLocal()
    try:
        query = session.query(RagFeedback)
        if rating is not None:
            query = query.filter(RagFeedback.rating == rating)
        feedback_items = (
            query.order_by(RagFeedback.created_at.desc(), RagFeedback.id.desc())
            .limit(safe_limit)
            .all()
        )
        result = []
        for item in feedback_items:
            query_log = (
                session.query(RagQueryLog)
                .filter(RagQueryLog.request_id == item.request_id)
                .order_by(RagQueryLog.created_at.desc(), RagQueryLog.id.desc())
                .first()
            )
            answer = None if query_log is None else query_log.answer
            if answer is not None and len(answer) > 300:
                answer = f"{answer[:300]}..."
            result.append(
                {
                    "id": item.id,
                    "rating": item.rating,
                    "reason": item.reason,
                    "comment": item.comment,
                    "major": item.major,
                    "created_at": item.created_at.isoformat() if item.created_at else None,
                    "question": None if query_log is None else query_log.question,
                    "answer": answer,
                }
            )
        return result
    except Exception:
        _log_event(logging.ERROR, "admin_feedback_fetch_failed", exc_info=True)
        raise HTTPException(status_code=500, detail="피드백 조회 중 오류가 발생했습니다.")
    finally:
        session.close()


@app.get("/admin/rag/status")
async def rag_admin_status():
    generated_at = kst_now().isoformat()
    session = SessionLocal()
    try:
        datasets = []
        has_degraded_dataset = False

        for key, artifacts in DATASET_ARTIFACTS.items():
            chunk_path = artifacts.chunk_path
            if not chunk_path.exists() and artifacts.csv_path.exists():
                chunk_path = artifacts.csv_path

            vectorizer_path = VECTORIZER_DIR / f"{key}_tfidf.pkl"
            cache = _datasets.get(key)
            dataset_status = "ok"
            chroma_count = None
            error_message = None
            degraded_reason = None
            tfidf_metadata = {}

            try:
                chroma_count = count_items(artifacts.collection)
            except Exception as exc:
                dataset_status = "degraded"
                error_message = str(exc)

            if vectorizer_path.exists():
                try:
                    tfidf_metadata = read_tfidf_metadata(key)
                    artifact_version = tfidf_metadata.get("sklearn_version")
                    if artifact_version and artifact_version != sklearn_version:
                        dataset_status = "degraded"
                        degraded_reason = degraded_reason or DATASET_REASON_VERSION_MISMATCH
                        error_message = error_message or _dataset_status_message(
                            DATASET_REASON_VERSION_MISMATCH,
                            artifact_version=artifact_version,
                            runtime_version=sklearn_version,
                        )
                except Exception as exc:
                    dataset_status = "degraded"
                    error_message = error_message or f"Failed to read TF-IDF metadata: {exc}"

            chunk_artifact_exists = chunk_path.exists()
            vectorizer_exists = vectorizer_path.exists()
            if not chunk_artifact_exists:
                dataset_status = "degraded"
                degraded_reason = degraded_reason or DATASET_REASON_ARTIFACT_MISSING
                error_message = error_message or _dataset_status_message(DATASET_REASON_ARTIFACT_MISSING)
            if not vectorizer_exists:
                dataset_status = "degraded"
                degraded_reason = degraded_reason or DATASET_REASON_VECTORIZER_MISSING
                error_message = error_message or _dataset_status_message(DATASET_REASON_VECTORIZER_MISSING)
            if chroma_count == 0 and cache is not None and len(cache.chunks) > 0:
                dataset_status = "degraded"
                degraded_reason = DATASET_REASON_EMPTY_COLLECTION
                error_message = _dataset_status_message(DATASET_REASON_EMPTY_COLLECTION)

            if dataset_status != "ok":
                has_degraded_dataset = True
                _log_event(
                    logging.WARNING,
                    "dataset_status_degraded",
                    dataset=key,
                    reason=degraded_reason,
                    error=error_message,
                )

            datasets.append(
                {
                    "key": key,
                    "collection": artifacts.collection,
                    "chroma_count": chroma_count,
                    "cached_chunk_count": 0 if cache is None else len(cache.chunks),
                    "chunk_artifact_exists": chunk_artifact_exists,
                    "chunk_artifact_mtime": _format_mtime(chunk_path),
                    "latest_document_published_at": _get_latest_document_published_at(cache),
                    "vectorizer_exists": vectorizer_exists,
                    "vectorizer_mtime": _format_mtime(vectorizer_path),
                    "last_successful_indexed_at": tfidf_metadata.get("created_at") or _format_mtime(vectorizer_path),
                    "vectorizer_sklearn_version": tfidf_metadata.get("sklearn_version"),
                    "status": dataset_status,
                    "error": error_message,
                }
            )

        pending_items = {
            "pending": session.query(PendingItem).filter(PendingItem.status == "pending").count(),
            "approved": session.query(PendingItem).filter(PendingItem.status.in_(["approved", "approved_manually"])).count(),
            "rejected": session.query(PendingItem).filter(PendingItem.status == "rejected").count(),
        }

        latest_query = session.query(RagQueryLog).order_by(RagQueryLog.created_at.desc()).first()
        fallback_reason_counts = {
            (reason or "unknown"): count
            for reason, count in (
                session.query(RagQueryLog.fallback_reason, func.count(RagQueryLog.id))
                .filter(RagQueryLog.fallback_triggered.is_(True))
                .group_by(RagQueryLog.fallback_reason)
                .all()
            )
        }
        rag_logs = {
            "total_queries": session.query(RagQueryLog).count(),
            "fallback_count": session.query(RagQueryLog).filter(RagQueryLog.fallback_triggered.is_(True)).count(),
            "latest_query_at": None if latest_query is None else latest_query.created_at.isoformat(),
            "fallback_reasons": fallback_reason_counts,
        }
        grounding = {
            "checked": 0,
            "ungrounded": 0,
            "ungrounded_rate": None,
        }
        try:
            grounding_checked = session.query(RagQueryLog).filter(RagQueryLog.grounding_checked.is_(True)).count()
            grounding_ungrounded = session.query(RagQueryLog).filter(RagQueryLog.grounding_grounded.is_(False)).count()
            grounding = {
                "checked": grounding_checked,
                "ungrounded": grounding_ungrounded,
                "ungrounded_rate": None if grounding_checked == 0 else grounding_ungrounded / grounding_checked,
            }
        except Exception:
            _log_event(logging.WARNING, "rag_grounding_status_failed", exc_info=True)
        feedback = {
            "total": 0,
            "up": 0,
            "down": 0,
            "satisfaction": None,
            "down_reasons": {},
        }
        try:
            up_count = session.query(RagFeedback).filter(RagFeedback.rating == 1).count()
            down_count = session.query(RagFeedback).filter(RagFeedback.rating == -1).count()
            vote_count = up_count + down_count
            feedback = {
                "total": session.query(RagFeedback).count(),
                "up": up_count,
                "down": down_count,
                "satisfaction": None if vote_count == 0 else up_count / vote_count,
                "down_reasons": {
                    (reason or "unknown"): count
                    for reason, count in (
                        session.query(RagFeedback.reason, func.count(RagFeedback.id))
                        .filter(RagFeedback.rating == -1)
                        .group_by(RagFeedback.reason)
                        .all()
                    )
                },
            }
        except Exception:
            _log_event(logging.WARNING, "rag_feedback_status_failed", exc_info=True)
        notices_ingestion = _build_notices_ingestion_status(session)
        try:
            today_str = kst_now().strftime('%Y-%m-%d')
            today_visitors = (
                session.query(func.count(func.distinct(RagQueryLog.session_id)))
                .filter(func.strftime('%Y-%m-%d', RagQueryLog.created_at) == today_str)
                .scalar()
            ) or 0
            total_visitors = (
                session.query(func.count(func.distinct(RagQueryLog.session_id)))
                .scalar()
            ) or 0
            visitor_stats = {"today": today_visitors, "total": total_visitors}
        except Exception:
            _log_event(logging.WARNING, "visitor_stats_query_failed", exc_info=True)
            visitor_stats = {"today": None, "total": None}

        status_dict = {
            "status": "degraded" if has_degraded_dataset else "ok",
            "generated_at": generated_at,
            "datasets": datasets,
            "pending_items": pending_items,
            "rag_logs": rag_logs,
            "visitor_stats": visitor_stats,
            "grounding": grounding,
            "feedback": feedback,
            "notices_ingestion": notices_ingestion,
        }
        try:
            status_dict["semantic_cache"] = {
                "enabled": RAG_SEMANTIC_CACHE_ENABLED,
                **semantic_cache.stats(),
            }
        except Exception:
            pass
        return status_dict
    except Exception as exc:
        _log_event(logging.ERROR, "rag_admin_status_failed", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "generated_at": generated_at,
                "datasets": [],
                "pending_items": {"pending": 0, "approved": 0, "rejected": 0},
                "rag_logs": {"total_queries": 0, "fallback_count": 0, "latest_query_at": None},
                "grounding": {"checked": 0, "ungrounded": 0, "ungrounded_rate": None},
                "feedback": {
                    "total": 0,
                    "up": 0,
                    "down": 0,
                    "satisfaction": None,
                    "down_reasons": {},
                },
                "notices_ingestion": {
                    "last_collection_at": None,
                    "last_successful_ingestion_at": None,
                    "ingestion_summary": {
                        "status": None,
                        "documents_seen": 0,
                        "documents_new": 0,
                        "documents_updated": 0,
                        "documents_deleted": 0,
                        "documents_failed": 0,
                    },
                    "stage_summary": {
                        "raw_documents": 0,
                        "normalized_documents": 0,
                        "indexed_documents": 0,
                    },
                    "quality_summary": {"parse_failed": 0, "severities": {}, "recent_checks": []},
                },
                "error": str(exc),
            },
        )
    finally:
        session.close()



def _build_notice_from_pending(source_type: str, data: dict) -> Notice | None:
    """제출 payload를 크롤 공지와 동일한 한글 컬럼 의미의 Notice로 변환한다.

    board는 source_type별 고정값으로 통일하고, 학과명은 별도로 보존하지 않고
    content/title에 이미 포함되도록 한다(K5). detail_url은 호출자가 doc_id 확정 후 채운다(K7).
    """
    department = _extract_submitter_department(source_type, data)

    if source_type == "custom_knowledge":
        content = data.get("answer") or ""
        if department:
            content = f"{content}\n\n주관: {department}".strip()
        return Notice(
            board="학과지식",
            title=data.get("question"),
            category="FAQ",
            published_date=kst_now().strftime("%Y-%m-%d"),
            content=content,
            is_manual=1,
        )

    if source_type == "event":
        content_parts = []
        if data.get("description"):
            content_parts.append(data.get("description"))
        date_str = f"일시: {data.get('start_date')}"
        if data.get("end_date") and data.get("end_date") != data.get("start_date"):
            date_str += f" ~ {data.get('end_date')}"
        content_parts.append(date_str)
        if data.get("location"):
            content_parts.append(f"장소: {data.get('location')}")
        if department:
            content_parts.append(f"주관: {department}")
        return Notice(
            board="학과행사",
            title=data.get("title"),
            category="행사",
            published_date=data.get("start_date"),
            content="\n\n".join(content_parts),
            is_manual=1,
        )

    if source_type == "announcement":
        content = data.get("content") or ""
        if department:
            content = f"{content}\n\n주관: {department}".strip()
        return Notice(
            board="학과공지",
            title=data.get("title"),
            category=data.get("category") or "일반",
            published_date=data.get("date"),
            content=content,
            is_manual=1,
        )

    return None


def _notice_to_ingest_frame(notice: Notice) -> pd.DataFrame:
    """단일 Notice를 ingest의 build_notice_chunks가 기대하는 한글 컬럼 프레임으로 만든다.

    이렇게 해야 크롤 공지와 동일한 doc_id/chunk_id/prefix/clean 규칙을 그대로 탄다(K1/K6).
    """
    return pd.DataFrame([
        {
            "게시판": notice.board or "",
            "제목": notice.title or "",
            "카테고리": notice.category or "",
            "게시일": notice.published_date or "",
            "상단고정": notice.is_fixed or "",
            "상세URL": notice.detail_url or "",
            "본문": notice.content or "",
            "첨부파일": notice.attachments or "[]",
            "db_id": notice.id,
        }
    ])


@app.post("/admin/approve/{item_id}")
async def approve_pending(item_id: int):
    session = SessionLocal()
    chroma_committed_ids: List[str] = []
    target_collection = DATASET_ARTIFACTS["notices"].collection
    try:
        logging.info(f"👉 [Admin] Approving item ID: {item_id}")
        item = session.query(PendingItem).filter(PendingItem.id == item_id).first()
        if not item:
            logging.error(f"❌ [Admin] Item not found: {item_id}")
            raise HTTPException(status_code=404, detail="Item not found")

        if item.status in ("approved", "approved_manually"):
            return {"status": item.status, "message": "이미 승인된 항목입니다."}

        data = json.loads(item.data)
        notice = _build_notice_from_pending(item.source_type, data)

        if notice is None:
            item.status = "approved_manually"
            session.commit()
            return {"status": "approved_manually"}

        # 1. Notice 저장 (id 확보) — 색인 부작용 성공 후 commit하기 위해 flush만 먼저
        session.add(notice)
        session.flush()  # notice.id 확보, 아직 commit 아님
        # K7: 수동 공지에 합성 고유 url 부여 (UNIQUE NULL/"" 충돌 방지 + url 필드 일관 채움)
        notice.detail_url = f"manual://notice/{item.source_type}/{notice.id}"
        session.flush()

        # 2. ingest 공식 경로로 청크 생성 (크롤 공지와 동일 규칙) — K1/K6
        chunks_df = build_notice_chunks(_notice_to_ingest_frame(notice))
        if chunks_df.empty:
            raise HTTPException(status_code=400, detail="청크를 생성할 수 없습니다(본문이 비어 있음).")

        chunk_ids = chunks_df["chunk_id"].astype(str).tolist()
        texts = chunks_df["chunk_text"].astype(str).tolist()

        # 3. 색인 부작용을 DB commit 전에 먼저 수행 (실패 시 롤백 가능) — K3
        embeddings = encode_texts(texts)
        metadatas = chunks_df.drop(columns=["chunk_text"]).to_dict(orient="records")
        metadatas = [{k: (v if v is not None else "") for k, v in m.items()} for m in metadatas]
        upsert_items(
            name=target_collection,
            ids=chunk_ids,
            documents=texts,
            metadatas=metadatas,
            embeddings=embeddings,
        )
        chroma_committed_ids = chunk_ids
        logging.info(f"✅ [Admin] Upserted {len(chunk_ids)} chunk(s) to ChromaDB")

        # 4. DB Chunk 적재 (동일 chunk_id 사용)
        for cid, text in zip(chunk_ids, texts):
            session.add(Chunk(chunk_id=cid, chunk_text=text, notice_id=notice.id))

        # 5. 모든 색인 성공 → 한 번에 commit (Notice + Chunk + status)
        item.status = "approved"
        session.commit()
        logging.info(f"✅ [Admin] Notice {notice.id} approved & committed.")

        # 6. 캐시 리로드 (best-effort, 실패해도 승인은 유효 — DB/Chroma엔 이미 반영됨)
        try:
            with _datasets_lock:
                if "notices" in _datasets:
                    del _datasets["notices"]
                _ensure_dataset_locked("notices")
        except Exception as e:
            logging.error(f"❌ [Admin] Failed to reload notices cache: {e}")

        return {"status": "approved", "chunk_ids": chunk_ids}

    except HTTPException:
        session.rollback()
        # 이미 Chroma에 넣었다면 되돌린다 (부분 적용 방지) — K3
        if chroma_committed_ids:
            try:
                delete_items(target_collection, chroma_committed_ids)
            except Exception:
                logging.error("⚠️ [Admin] Failed to rollback Chroma upsert after error.", exc_info=True)
        raise
    except Exception as e:
        session.rollback()
        if chroma_committed_ids:
            try:
                delete_items(target_collection, chroma_committed_ids)
            except Exception:
                logging.error("⚠️ [Admin] Failed to rollback Chroma upsert after error.", exc_info=True)
        logging.error(f"🔥 [Admin] Critical Error in approve_pending: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@app.post("/admin/reject/{item_id}")
async def reject_pending(item_id: int):
    session = SessionLocal()
    target_collection = DATASET_ARTIFACTS["notices"].collection
    try:
        item = session.query(PendingItem).filter(PendingItem.id == item_id).first()
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")

        was_approved = item.status == "approved"
        removed_chunk_ids: List[str] = []

        # K4: 이미 승인·색인된 항목을 반려하면 색인을 되돌린다.
        if was_approved:
            data = json.loads(item.data)
            notice_obj = _build_notice_from_pending(item.source_type, data)
            # 승인 시 생성된 Notice를 title+source 기준으로 찾는다(수동 공지만 대상).
            if notice_obj is not None and notice_obj.title:
                matched_notices = (
                    session.query(Notice)
                    .filter(
                        Notice.is_manual == 1,
                        Notice.title == notice_obj.title,
                        Notice.board == notice_obj.board,
                    )
                    .all()
                )
                for n in matched_notices:
                    chunks = session.query(Chunk).filter(Chunk.notice_id == n.id).all()
                    removed_chunk_ids.extend([c.chunk_id for c in chunks if c.chunk_id])
                    for c in chunks:
                        session.delete(c)
                    session.delete(n)

            if removed_chunk_ids:
                try:
                    delete_items(target_collection, removed_chunk_ids)
                except Exception:
                    logging.error("⚠️ [Admin] Failed to delete chunks from Chroma on reject.", exc_info=True)

        item.status = "rejected"
        session.commit()

        if removed_chunk_ids:
            try:
                with _datasets_lock:
                    if "notices" in _datasets:
                        del _datasets["notices"]
                    _ensure_dataset_locked("notices")
            except Exception as e:
                logging.error(f"❌ [Admin] Failed to reload notices cache on reject: {e}")

        return {"status": "rejected"}
    except HTTPException:
        session.rollback()
        raise
    except Exception as e:
        session.rollback()
        logging.error(f"🔥 [Admin] Error in reject_pending: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


async def _stream_with_terminal_event(
    body: AsyncIterator[str],
    request_id: str,
) -> AsyncIterator[str]:
    """Emit exactly one ``done`` event after a successful stream body.

    Plain EOF is not a success signal: an upstream connection can close after
    only part of an answer. Exceptions therefore emit ``error`` and never
    ``done``, while client cancellation remains a cancellation.
    """
    completion_seen = False
    try:
        async for event in body:
            event_type = None
            try:
                data_line = next(
                    line for line in event.splitlines() if line.startswith("data: ")
                )
                payload = json.loads(data_line.removeprefix("data: "))
                event_type = payload.get("type") if isinstance(payload, dict) else None
            except (StopIteration, json.JSONDecodeError, AttributeError):
                event_type = None

            if completion_seen:
                _log_event(
                    logging.ERROR,
                    "stream_protocol_violation",
                    request_id=request_id,
                    reason="event_after_completion",
                    event_type=event_type,
                )
                fail_msg = "답변 완료 신호가 올바르지 않습니다. 다시 시도해 주세요."
                yield f"data: {json.dumps({'type': 'error', 'content': fail_msg}, ensure_ascii=False)}\n\n"
                return
            if event_type == "done":
                _log_event(
                    logging.ERROR,
                    "stream_protocol_violation",
                    request_id=request_id,
                    reason="body_emitted_done",
                )
                fail_msg = "답변 완료 신호가 올바르지 않습니다. 다시 시도해 주세요."
                yield f"data: {json.dumps({'type': 'error', 'content': fail_msg}, ensure_ascii=False)}\n\n"
                return
            if event_type == "completion":
                completion_seen = True
            yield event
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        _log_event(
            logging.ERROR, "ask_stream_failed",
            request_id=request_id, error=str(exc),
        )
        fail_msg = "답변 생성 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
        yield f"data: {json.dumps({'type': 'error', 'content': fail_msg}, ensure_ascii=False)}\n\n"
        return

    if completion_seen:
        yield f"data: {json.dumps({'type': 'done', 'request_id': request_id}, ensure_ascii=False)}\n\n"
    else:
        _log_event(
            logging.WARNING,
            "stream_ended_without_completion",
            request_id=request_id,
        )


def _completion_stream_event(
    *,
    request_id: str,
    grounded: bool | None,
    grounding_score: float | None,
    suggested_questions: list[str],
    fallback_reason: str | None,
    sources: list[SourceChunk] | list[dict],
    suggested_question_details: list[SuggestedQuestionDetail] | list[dict] | None = None,
    resolved_intents: list[str] | None = None,
) -> str:
    """Build final persistence metadata; callers emit it immediately before ``done``."""
    serialized_sources = [
        source.dict() if hasattr(source, "dict") else source
        for source in sources
    ]
    payload = {
        "type": "completion",
        "request_id": request_id,
        "grounded": grounded,
        "grounding_score": grounding_score,
        "suggested_questions": suggested_questions,
        "suggested_question_details": [
            detail.model_dump() if hasattr(detail, "model_dump") else detail
            for detail in (suggested_question_details or [])
        ],
        "resolved_intents": resolved_intents or [],
        "fallback_reason": fallback_reason,
        "sources": serialized_sources,
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.post("/ask/stream")
async def ask_stream(req: AskRequest, request: Request):
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    raw_query = req.question.strip()
    if not raw_query:
        raise HTTPException(status_code=400, detail="질문이 비어 있습니다.")

    session_id = req.session_id or str(uuid.uuid4())
    stage_timings: dict[str, float] = {}
    llm_usage: list[dict] = []
    request_started_at = time.perf_counter()

    async def _stream_body():
        user_major = req.major
        allow_wise = query_explicitly_requests_wise(raw_query)
        semantic_cache_ns = _semantic_cache_namespace(user_major, allow_wise=allow_wise)
        # 후속질문은 대화 이력으로 해소되므로(맥락 의존) 이력이 있으면 시맨틱 캐시를 건너뛴다.
        # raw_query만으로는 직전 맥락이 달라 잘못된 캐시 답을 줄 수 있기 때문(첫 턴만 캐시 대상).
        history_text = ""
        if USE_QUERY_ANALYSIS or RAG_SEMANTIC_CACHE_ENABLED:
            stage_started_at = time.perf_counter()
            history_text = await run_in_threadpool(get_recent_history_text, session_id)
            _mark_stage(stage_timings, "history_load", stage_started_at)
        if RAG_SEMANTIC_CACHE_ENABLED and not (history_text or "").strip():
            stage_started_at = time.perf_counter()
            hit = await run_in_threadpool(semantic_cache.get, raw_query, semantic_cache_ns)
            _mark_stage(stage_timings, "semantic_cache_lookup", stage_started_at)
            if hit is not None:
                _mark_stage(stage_timings, "total", request_started_at)
                yield "data: " + json.dumps({"type": "metadata", "request_id": request_id, "sources": hit.get("sources", []), "citations": hit.get("citations", ""), "route": hit.get("route", []), "fallback_triggered": False}, ensure_ascii=False) + "\n\n"
                yield "data: " + json.dumps({"type": "text", "content": hit["answer"]}, ensure_ascii=False) + "\n\n"
                if hit.get("suggested_questions"):
                    yield "data: " + json.dumps({"type": "suggestions", "questions": hit["suggested_questions"]}, ensure_ascii=False) + "\n\n"
                if hit.get("grounded") is False:
                    yield "data: " + json.dumps({"type": "grounding", "grounded": False, "score": hit.get("grounding_score"), "reason": None}, ensure_ascii=False) + "\n\n"
                await run_in_threadpool(append_manual_history, session_id, raw_query, hit["answer"])
                _log_event(logging.INFO, "semantic_cache_hit", request_id=request_id, namespace=semantic_cache_ns)
                yield _completion_stream_event(
                    request_id=request_id,
                    grounded=hit.get("grounded"),
                    grounding_score=hit.get("grounding_score"),
                    suggested_questions=hit.get("suggested_questions", []),
                    fallback_reason=None,
                    sources=hit.get("sources", []),
                    suggested_question_details=hit.get("suggested_question_details", []),
                    resolved_intents=hit.get("resolved_intents", hit.get("route", [])),
                )
                return

        analysis_meta = QueryAnalysisMeta(result=None, used=False, failed=False)
        if USE_QUERY_ANALYSIS:
            # 후속 질문("그럼 신청 기간은?")의 대명사/생략을 해소하기 위해
            # 위에서 받아둔 최근 대화 이력을 함께 전달해 독립형 질문으로 재작성하게 한다.
            stage_started_at = time.perf_counter()
            analysis_result = await analyze_query(raw_query, history_text)
            _mark_stage(stage_timings, "query_analysis", stage_started_at)
            analysis_meta = _analysis_to_meta(analysis_result, failed=analysis_result is None)

        clarification_fields = _first_turn_clarification_fields(
            raw_query,
            analysis_meta,
            history_text,
        )
        if clarification_fields:
            clarification_answer = _build_clarification_answer(clarification_fields)
            _mark_stage(stage_timings, "total", request_started_at)
            yield f"data: {json.dumps({'type': 'metadata', 'request_id': request_id, 'sources': [], 'citations': '', 'route': ['unknown'], 'fallback_triggered': False}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'text', 'content': clarification_answer}, ensure_ascii=False)}\n\n"
            await run_in_threadpool(
                _save_rag_evaluation_log,
                request_id, session_id, raw_query, raw_query, ["unknown"], clarification_answer,
                False, None, False, False,
                None if analysis_meta.result is None else analysis_meta.result.intent,
                None if analysis_meta.result is None else json.dumps(analysis_meta.result.entities, ensure_ascii=False),
                None if analysis_meta.result is None else analysis_meta.result.time_focus,
                None if analysis_meta.result is None else json.dumps(analysis_meta.result.search_queries, ensure_ascii=False),
                True,
                ", ".join(clarification_fields),
                analysis_meta.used, analysis_meta.failed, None, None, [], stage_timings, llm_usage,
            )
            await run_in_threadpool(append_manual_history, session_id, raw_query, clarification_answer)
            yield _completion_stream_event(
                request_id=request_id,
                grounded=None,
                grounding_score=None,
                suggested_questions=[],
                fallback_reason=None,
                sources=[],
                resolved_intents=["unknown"],
            )
            return

        # 1. 일반 대화 처리 (검색 불필요한 경우)
        if (
            analysis_meta.result is not None
            and analysis_meta.result.intent == "unknown"
            and not _has_school_info_terms(raw_query)
        ):
            current_date = _get_current_kst_string()
            context_text = "일반 대화입니다. 학교 자료 검색이 필요한 질문이 아니면 자연스럽고 짧게 답하세요."
            
            # 메타데이터 전송 (소스 없음)
            yield f"data: {json.dumps({'type': 'metadata', 'request_id': request_id, 'sources': [], 'citations': '', 'route': ['unknown'], 'fallback_triggered': False}, ensure_ascii=False)}\n\n"
            
            full_answer = []
            stage_started_at = time.perf_counter()
            async for chunk in generate_langchain_answer_stream(
                question=raw_query,
                context=context_text,
                session_id=session_id,
                current_date=current_date,
                usage_collector=llm_usage,
            ):
                full_answer.append(chunk)
                yield f"data: {json.dumps({'type': 'text', 'content': chunk}, ensure_ascii=False)}\n\n"
            _mark_stage(stage_timings, "generation_stream", stage_started_at)
            _mark_stage(stage_timings, "total", request_started_at)
            
            # 로깅은 스트림 종료 후 수행 (별도 태스크로 처리하거나 여기서 대략 수행)
            await run_in_threadpool(
                _save_rag_evaluation_log,
                request_id, session_id, raw_query, raw_query, ["unknown"], "".join(full_answer),
                False, None, False, False, analysis_meta.result.intent,
                json.dumps(analysis_meta.result.entities, ensure_ascii=False),
                analysis_meta.result.time_focus,
                json.dumps(analysis_meta.result.search_queries, ensure_ascii=False),
                analysis_meta.result.needs_clarification, analysis_meta.result.clarification_reason,
                analysis_meta.used, analysis_meta.failed, None, None, [],
                stage_timings, llm_usage,
            )
            yield _completion_stream_event(
                request_id=request_id,
                grounded=None,
                grounding_score=None,
                suggested_questions=[],
                fallback_reason=None,
                sources=[],
                resolved_intents=["unknown"],
            )
            return

        # 2. RAG 검색 프로세스
        stage_started_at = time.perf_counter()
        query_for_retrieval = raw_query
        expanded_query = expand_query(query_for_retrieval)
        retrieval_queries = _build_retrieval_queries(query_for_retrieval, expanded_query, analysis_meta, req.major)
        if raw_query not in retrieval_queries:
            retrieval_queries.insert(0, raw_query)
        semantic_query = analysis_meta.result.normalized_question if analysis_meta.result is not None else expanded_query
        _mark_stage(stage_timings, "query_expansion", stage_started_at)

        final_where_filter = {}
        # 백엔드는 학과 미지정 시 null을 보낸다("Unknown"/"Default"는 보내지 않지만 방어적으로 함께 제외).
        if user_major and user_major not in _NO_MAJOR_SENTINELS:
            college = None
            if RAG_COLLEGE_SCOPE_ENABLED:
                try:
                    college = college_of(user_major)
                except Exception:
                    college = None
            if college:
                final_where_filter["$or"] = [{"major": {"$eq": user_major}}, {"college_name": {"$eq": college}}]
            else:
                final_where_filter["major"] = {"$eq": user_major}

        stage_started_at = time.perf_counter()
        route = _resolve_retrieval_route(raw_query, analysis_meta)
        _mark_stage(stage_timings, "routing", stage_started_at)

        entry_year = _extract_entry_year_from_query(semantic_query) or _extract_entry_year_from_query(raw_query)
        stage_started_at = time.perf_counter()
        date_filter = await run_in_threadpool(extract_date_filter_from_query, semantic_query)
        _mark_stage(stage_timings, "date_filter_parse", stage_started_at)
        date_filter_applied = date_filter is not None
        date_filter_relaxed = False
        recent_notice_query, notice_board_filter, retrieval_policy = _resolve_notice_retrieval_controls(
            raw_query,
            semantic_query,
            route,
        )
        current_operational_notice_terms = _current_operational_notice_terms(raw_query, route)

        stage_started_at = time.perf_counter()
        frames, date_filter_eliminated_any, unavailable_datasets = await _retrieve_frames_for_queries(
            route=route, queries=retrieval_queries, final_where_filter=final_where_filter,
            notice_board_filter=notice_board_filter, date_filter=date_filter, entry_year=entry_year,
            request_id=request_id, recent_notice_query=recent_notice_query,
            current_operational_notice_terms=current_operational_notice_terms,
            allow_wise=allow_wise,
        )

        if not frames and date_filter is not None and date_filter.relaxed_start and date_filter.relaxed_end:
            date_filter_relaxed = True
            relaxed_filter = QueryDateFilter(
                start=date_filter.relaxed_start, end=date_filter.relaxed_end,
                label=f"{date_filter.label}_relaxed", is_relative=date_filter.is_relative,
                kind=getattr(date_filter, "kind", "published"),
            )
            relaxed_frames, _, relaxed_unavailable = await _retrieve_frames_for_queries(
                route=route, queries=retrieval_queries, final_where_filter=final_where_filter,
                notice_board_filter=notice_board_filter, date_filter=relaxed_filter, entry_year=entry_year,
                request_id=request_id, recent_notice_query=recent_notice_query,
                current_operational_notice_terms=current_operational_notice_terms,
                allow_wise=allow_wise,
            )
            if relaxed_frames:
                frames = relaxed_frames
            unavailable_datasets = list(dict.fromkeys(unavailable_datasets + relaxed_unavailable))

        frames, staff_unavailable = await _enrich_staff_lookup_frames(
            question=raw_query,
            frames=frames,
            final_where_filter=final_where_filter,
            entry_year=entry_year,
            request_id=request_id,
            allow_wise=allow_wise,
        )
        unavailable_datasets = list(dict.fromkeys(unavailable_datasets + staff_unavailable))

        merged = _build_balanced_shortlist(
            frames,
            per_dataset=(DEFAULT_TOP_K * 2 if date_filter is not None and "schedule" in route else RAG_EVIDENCE_CANDIDATES_PER_DATASET),
        )
        _mark_stage(stage_timings, "retrieval_and_fusion", stage_started_at)

        # 3. Fallback 체크
        top_hybrid_score = None
        if not merged.empty and "hybrid_score" in merged.columns:
            top_hybrid_score = _clean_response_float(merged["hybrid_score"].max())

        topic_aligned = False
        min_score = retrieval_policy.min_score

        fallback_reason = None
        if merged.empty:
            if unavailable_datasets and len(unavailable_datasets) == len(route):
                fallback_reason = FALLBACK_REASON_DATASET_UNAVAILABLE
            elif date_filter_eliminated_any:
                fallback_reason = FALLBACK_REASON_DATE_FILTER_ELIMINATED_ALL
            else:
                fallback_reason = FALLBACK_REASON_NO_RESULTS
        # 결과가 있어도 최고 점수가 임계 미만이면 환각 방지를 위해 폴백 처리한다(비스트리밍 경로와 동일).
        elif top_hybrid_score is not None and top_hybrid_score < min_score:
            fallback_reason = FALLBACK_REASON_SCORE_BELOW_THRESHOLD

        selector_fallback = False
        if fallback_reason is None:
            stage_started_at = time.perf_counter()
            merged, selector_fallback = await _select_answer_evidence(
                semantic_query,
                merged,
                llm_usage,
                recent_notice_query=recent_notice_query,
                date_bound_schedule_query=(date_filter is not None and "schedule" in route),
            )
            _mark_stage(stage_timings, "evidence_selection", stage_started_at)
            _log_event(
                logging.WARNING if selector_fallback else logging.INFO,
                "evidence_selection_completed",
                request_id=request_id,
                fallback=selector_fallback,
                group_count=0 if merged.empty else int(merged["evidence_group"].nunique()),
                document_count=len(merged),
            )
            if merged.empty:
                fallback_reason = FALLBACK_REASON_NO_RESULTS

        if fallback_reason is not None:
            fallback_answer = _build_retrieval_fallback_answer(
                route=route, reason=fallback_reason, date_filter_relaxed=date_filter_relaxed,
                policy_name=retrieval_policy.name, clarification_reason=(
                    analysis_meta.result.clarification_reason if analysis_meta.result and analysis_meta.result.needs_clarification else None
                )
            )
            yield f"data: {json.dumps({'type': 'metadata', 'request_id': request_id, 'sources': [], 'citations': '', 'route': route, 'fallback_triggered': True, 'fallback_reason': fallback_reason}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'text', 'content': fallback_answer}, ensure_ascii=False)}\n\n"
            
            await run_in_threadpool(
                _save_rag_evaluation_log,
                request_id, session_id, raw_query, expanded_query, route, fallback_answer,
                True, fallback_reason, date_filter_applied, date_filter_relaxed,
                None if analysis_meta.result is None else analysis_meta.result.intent,
                None if analysis_meta.result is None else json.dumps(analysis_meta.result.entities, ensure_ascii=False),
                None if analysis_meta.result is None else analysis_meta.result.time_focus,
                None if analysis_meta.result is None else json.dumps(analysis_meta.result.search_queries, ensure_ascii=False),
                False if analysis_meta.result is None else analysis_meta.result.needs_clarification,
                None if analysis_meta.result is None else analysis_meta.result.clarification_reason,
                analysis_meta.used, analysis_meta.failed, None, top_hybrid_score, [],
                {**stage_timings, "total": round((time.perf_counter() - request_started_at) * 1000, 2)},
                llm_usage,
            )
            await run_in_threadpool(append_manual_history, session_id, raw_query, fallback_answer)
            yield _completion_stream_event(
                request_id=request_id,
                grounded=None,
                grounding_score=None,
                suggested_questions=[],
                fallback_reason=fallback_reason,
                sources=[],
                resolved_intents=(
                    [analysis_meta.result.intent]
                    if analysis_meta.result is not None
                    else []
                ),
            )
            return

        # 4. 컨텍스트 구성 및 스트리밍 시작
        stage_started_at = time.perf_counter()
        group_count = int(pd.to_numeric(merged["evidence_group"], errors="coerce").max())
        context_text = _build_selected_evidence_context(merged, prefix=_user_profile_prefix(req.major))
        response_instructions = "\n".join(
            instruction
            for instruction in (
                _multiple_evidence_response_instructions(group_count),
                _period_bound_response_instruction(raw_query, merged),
            )
            if instruction
        ) or None
        selected_route = list(dict.fromkeys(merged["dataset"].astype(str).tolist()))
        _mark_stage(stage_timings, "context_build", stage_started_at)
        current_date = _get_current_kst_string()

        # 소스 데이터 정리
        sources = [_source_chunk_from_row(row).model_dump() for _, row in merged.iterrows()]
        
        citations_raw = await run_in_threadpool(format_citations, merged)
        citations = re.sub(r'<[^>]+>', '', citations_raw)

        # 메타데이터 먼저 전송
        yield f"data: {json.dumps({'type': 'metadata', 'request_id': request_id, 'sources': sources, 'citations': citations, 'route': route, 'fallback_triggered': False}, ensure_ascii=False)}\n\n"

        # 답변 스트리밍 시작
        full_answer = []
        stage_started_at = time.perf_counter()
        async for chunk in generate_langchain_answer_stream(
            question=semantic_query,
            context=context_text,
            session_id=session_id,
            current_date=current_date,
            usage_collector=llm_usage,
            response_instructions=response_instructions,
        ):
            full_answer.append(chunk)
            yield f"data: {json.dumps({'type': 'text', 'content': chunk}, ensure_ascii=False)}\n\n"
        _mark_stage(stage_timings, "generation_stream", stage_started_at)

        # 최종 로깅
        final_answer = "".join(full_answer)
        suggested_questions: list[str] = []
        suggested_question_details: list[dict[str, Any]] = []
        grounding_result = None
        grounded_flag: bool | None = None
        grounding_score: float | None = None
        if RAG_GROUNDING_CHECK_ENABLED and len(sources) > 0:
            try:
                stage_started_at = time.perf_counter()
                grounding_result = await check_answer_grounding(
                    raw_query,
                    final_answer,
                    context_text,
                    min_score=RAG_GROUNDING_MIN_SCORE,
                    usage_collector=llm_usage,
                )
                _mark_stage(stage_timings, "grounding_check", stage_started_at)
                if grounding_result.checked:
                    grounded_flag = grounding_result.grounded
                    grounding_score = grounding_result.score
                if grounding_result.checked and not grounding_result.grounded:
                    yield f"data: {json.dumps({'type': 'grounding', 'grounded': False, 'score': grounding_result.score, 'reason': grounding_result.reason}, ensure_ascii=False)}\n\n"
                    guard_text = "\n\n" + _build_grounding_confirmation_answer(
                        grounding_result,
                        [SourceChunk(**s) for s in sources],
                    )
                    full_answer.append(guard_text)
                    suggested_questions = []
                    suggested_question_details = []
                    yield f"data: {json.dumps({'type': 'text', 'content': guard_text}, ensure_ascii=False)}\n\n"
            except Exception as exc:  # noqa: BLE001
                _log_event(
                    logging.WARNING,
                    "grounding_check_failed",
                    request_id=request_id,
                    error=str(exc),
                )
        final_answer = "".join(full_answer)
        grounding_allows_followups = _grounding_allows_followups(
            RAG_GROUNDING_CHECK_ENABLED,
            grounding_result,
        )
        if RAG_SUGGEST_FOLLOWUPS and grounding_allows_followups:
            try:
                stage_started_at = time.perf_counter()
                suggested_questions = await generate_followup_questions(
                    raw_query,
                    final_answer,
                    RAG_SUGGEST_FOLLOWUPS_COUNT,
                    usage_collector=llm_usage,
                    source_context=sources,
                    campus_scope="wise" if allow_wise else "seoul_bmc",
                    supported_domains=selected_route,
                )
                suggested_question_details = build_followup_question_details(
                    suggested_questions,
                    sources,
                )
                suggested_questions = [detail["question"] for detail in suggested_question_details]
                _mark_stage(stage_timings, "followup_generation", stage_started_at)
                if suggested_questions:
                    yield f"data: {json.dumps({'type': 'suggestions', 'questions': suggested_questions}, ensure_ascii=False)}\n\n"
            except Exception as exc:  # noqa: BLE001
                _log_event(
                    logging.WARNING,
                    "followup_suggestions_failed",
                    request_id=request_id,
                    error=str(exc),
                )
        resolved_intents = list(
            dict.fromkeys(
                ([analysis_meta.result.intent] if analysis_meta.result is not None else [])
                + selected_route
            )
        )
        _mark_stage(stage_timings, "total", request_started_at)
        await run_in_threadpool(
            _save_rag_evaluation_log,
            request_id, session_id, raw_query, expanded_query, route, final_answer,
            False, None, date_filter_applied, date_filter_relaxed,
            None if analysis_meta.result is None else analysis_meta.result.intent,
            None if analysis_meta.result is None else json.dumps(analysis_meta.result.entities, ensure_ascii=False),
            None if analysis_meta.result is None else analysis_meta.result.time_focus,
            None if analysis_meta.result is None else json.dumps(analysis_meta.result.search_queries, ensure_ascii=False),
            False if analysis_meta.result is None else analysis_meta.result.needs_clarification,
            None if analysis_meta.result is None else analysis_meta.result.clarification_reason,
            analysis_meta.used, analysis_meta.failed, 
            json.dumps(_collect_matched_queries(merged), ensure_ascii=False),
            top_hybrid_score, 
            [SourceChunk(**s) for s in sources],
            stage_timings,
            llm_usage,
        )
        if grounding_result is not None and grounding_result.checked:
            await run_in_threadpool(_update_grounding_log, request_id, grounding_result)
        if RAG_SEMANTIC_CACHE_ENABLED and _should_cache_answer(
            selected_route,
            False,
            date_filter_applied,
            grounded_flag,
            final_answer,
            recent_notice_query=recent_notice_query,
        ):
            await run_in_threadpool(
                semantic_cache.put,
                raw_query,
                semantic_cache_ns,
                {
                    "answer": final_answer,
                    "citations": citations,
                    "route": route,
                    "sources": sources,
                    "suggested_questions": suggested_questions,
                    "suggested_question_details": suggested_question_details,
                    "resolved_intents": resolved_intents,
                    "grounded": grounded_flag,
                    "grounding_score": grounding_score,
                },
            )
        yield _completion_stream_event(
            request_id=request_id,
            grounded=grounded_flag,
            grounding_score=grounding_score,
            suggested_questions=suggested_questions,
            fallback_reason=None,
            sources=sources,
            suggested_question_details=suggested_question_details,
            resolved_intents=resolved_intents,
        )

    return StreamingResponse(
        _stream_with_terminal_event(_stream_body(), request_id),
        media_type="text/event-stream",
    )

@app.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest, request: Request) -> AskResponse:
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    raw_query = req.question.strip()
    if not raw_query:
        raise HTTPException(status_code=400, detail="질문이 비어 있습니다.")

    session_id = req.session_id or str(uuid.uuid4())
    stage_timings: dict[str, float] = {}
    llm_usage: list[dict] = []
    request_started_at = time.perf_counter()
    user_major = req.major
    allow_wise = query_explicitly_requests_wise(raw_query)
    semantic_cache_ns = _semantic_cache_namespace(user_major, allow_wise=allow_wise)
    # 후속질문은 대화 이력으로 해소되므로(맥락 의존) 이력이 있으면 시맨틱 캐시를 건너뛴다(첫 턴만 대상).
    history_text = ""
    if USE_QUERY_ANALYSIS or RAG_SEMANTIC_CACHE_ENABLED:
        stage_started_at = time.perf_counter()
        history_text = await run_in_threadpool(get_recent_history_text, session_id)
        _mark_stage(stage_timings, "history_load", stage_started_at)
    if RAG_SEMANTIC_CACHE_ENABLED and not (history_text or "").strip():
        stage_started_at = time.perf_counter()
        hit = await run_in_threadpool(semantic_cache.get, raw_query, semantic_cache_ns)
        _mark_stage(stage_timings, "semantic_cache_lookup", stage_started_at)
        if hit is not None:
            _mark_stage(stage_timings, "total", request_started_at)
            _log_event(logging.INFO, "semantic_cache_hit", request_id=request_id, namespace=semantic_cache_ns)
            await run_in_threadpool(append_manual_history, session_id, raw_query, hit["answer"])
            return AskResponse(
                request_id=request_id,
                answer=hit["answer"],
                citations=hit.get("citations", ""),
                route=hit.get("route", []),
                resolved_intents=hit.get("resolved_intents", hit.get("route", [])),
                sources=[SourceChunk(**s) for s in hit.get("sources", [])],
                suggested_questions=hit.get("suggested_questions", []),
                suggested_question_details=[
                    SuggestedQuestionDetail(**detail)
                    for detail in hit.get("suggested_question_details", [])
                ],
                grounded=hit.get("grounded"),
                grounding_score=hit.get("grounding_score"),
                fallback_triggered=False,
                fallback_reason=None,
            )

    analysis_meta = QueryAnalysisMeta(result=None, used=False, failed=False)
    if USE_QUERY_ANALYSIS:
        # 후속 질문의 대명사/생략 해소를 위해 위에서 받아둔 최근 대화 이력을 함께 전달(스트리밍 경로와 동일)
        stage_started_at = time.perf_counter()
        analysis_result = await analyze_query(raw_query, history_text)
        _mark_stage(stage_timings, "query_analysis", stage_started_at)
        analysis_meta = _analysis_to_meta(analysis_result, failed=analysis_result is None)

    clarification_fields = _first_turn_clarification_fields(
        raw_query,
        analysis_meta,
        history_text,
    )
    if clarification_fields:
        clarification_answer = _build_clarification_answer(clarification_fields)
        _mark_stage(stage_timings, "total", request_started_at)
        await run_in_threadpool(
            _save_rag_evaluation_log,
            request_id,
            session_id,
            raw_query,
            raw_query,
            ["unknown"],
            clarification_answer,
            False,
            None,
            False,
            False,
            None if analysis_meta.result is None else analysis_meta.result.intent,
            None if analysis_meta.result is None else json.dumps(analysis_meta.result.entities, ensure_ascii=False),
            None if analysis_meta.result is None else analysis_meta.result.time_focus,
            None if analysis_meta.result is None else json.dumps(analysis_meta.result.search_queries, ensure_ascii=False),
            True,
            ", ".join(clarification_fields),
            analysis_meta.used,
            analysis_meta.failed,
            None,
            None,
            [],
            stage_timings,
            llm_usage,
        )
        await run_in_threadpool(append_manual_history, session_id, raw_query, clarification_answer)
        return AskResponse(
            request_id=request_id,
            answer=clarification_answer,
            citations="",
            route=["unknown"],
            sources=[],
            resolved_intents=["unknown"],
            fallback_triggered=False,
            fallback_reason=None,
        )

    if (
        analysis_meta.result is not None
        and analysis_meta.result.intent == "unknown"
        and not _has_school_info_terms(raw_query)
    ):
        current_date = _get_current_kst_string()
        context_text = "일반 대화입니다. 학교 자료 검색이 필요한 질문이 아니면 자연스럽고 짧게 답하세요."
        try:
            stage_started_at = time.perf_counter()
            answer = await generate_langchain_answer(
                question=raw_query,
                context=context_text,
                session_id=session_id,
                current_date=current_date,
                usage_collector=llm_usage,
            )
            _mark_stage(stage_timings, "generation", stage_started_at)
        except Exception:
            _log_event(logging.ERROR, "llm_generation_failed", exc_info=True, request_id=request_id)
            answer = "저는 동똑이에요. 지금 응답이 잠깐 매끄럽지 않았지만, 계속 편하게 물어보셔도 됩니다."
        _mark_stage(stage_timings, "total", request_started_at)

        await run_in_threadpool(
            _save_rag_evaluation_log,
            request_id,
            session_id,
            raw_query,
            raw_query,
            ["unknown"],
            answer,
            False,
            None,
            False,
            False,
            analysis_meta.result.intent,
            json.dumps(analysis_meta.result.entities, ensure_ascii=False),
            analysis_meta.result.time_focus,
            json.dumps(analysis_meta.result.search_queries, ensure_ascii=False),
            analysis_meta.result.needs_clarification,
            analysis_meta.result.clarification_reason,
            analysis_meta.used,
            analysis_meta.failed,
            None,
            None,
            [],
            stage_timings,
            llm_usage,
        )
        return AskResponse(
            request_id=request_id,
            answer=answer,
            citations="",
            route=["unknown"],
            sources=[],
            resolved_intents=["unknown"],
            fallback_triggered=False,
            fallback_reason=None,
        )

    stage_started_at = time.perf_counter()
    query_for_retrieval = raw_query
    expanded_query = expand_query(query_for_retrieval)
    retrieval_queries = _build_retrieval_queries(query_for_retrieval, expanded_query, analysis_meta, req.major)
    if raw_query not in retrieval_queries:
        retrieval_queries.insert(0, raw_query)
    semantic_query = analysis_meta.result.normalized_question if analysis_meta.result is not None else expanded_query
    _mark_stage(stage_timings, "query_expansion", stage_started_at)
    _log_event(
        logging.INFO,
        "ask_started",
        request_id=request_id,
        raw_query=raw_query,
        query_for_retrieval=query_for_retrieval,
        expanded_query=expanded_query,
        retrieval_queries=retrieval_queries,
        analysis_intent=None if analysis_meta.result is None else analysis_meta.result.intent,
    )

    # 로그에 처리된 질문과 세션 ID를 출력하여 디버깅을 돕습니다.
    _log_event(logging.INFO, "ask_session", request_id=request_id, session_id=session_id)

    final_where_filter: Dict = {}
    # 백엔드는 학과 미지정 시 null을 보낸다("Unknown"/"Default"는 보내지 않지만 방어적으로 함께 제외).
    if user_major and user_major not in _NO_MAJOR_SENTINELS:
        college = None
        if RAG_COLLEGE_SCOPE_ENABLED:
            try:
                college = college_of(user_major)
            except Exception:
                college = None
        if college:
            final_where_filter["$or"] = [{"major": {"$eq": user_major}}, {"college_name": {"$eq": college}}]
        else:
            final_where_filter["major"] = {"$eq": user_major}

    _log_event(logging.INFO, "ask_filters", request_id=request_id, filters=final_where_filter)
    stage_started_at = time.perf_counter()
    route = _resolve_retrieval_route(raw_query, analysis_meta)
    _mark_stage(stage_timings, "routing", stage_started_at)
    entry_year = _extract_entry_year_from_query(semantic_query) or _extract_entry_year_from_query(raw_query)
    stage_started_at = time.perf_counter()
    date_filter = await run_in_threadpool(
        extract_date_filter_from_query,
        semantic_query,
    )
    _mark_stage(stage_timings, "date_filter_parse", stage_started_at)
    date_filter_applied = date_filter is not None
    date_filter_relaxed = False
    recent_notice_query, notice_board_filter, retrieval_policy = _resolve_notice_retrieval_controls(
        raw_query,
        semantic_query,
        route,
    )
    current_operational_notice_terms = _current_operational_notice_terms(raw_query, route)

    stage_started_at = time.perf_counter()
    frames, date_filter_eliminated_any, unavailable_datasets = await _retrieve_frames_for_queries(
        route=route,
        queries=retrieval_queries,
        final_where_filter=final_where_filter,
        notice_board_filter=notice_board_filter,
        date_filter=date_filter,
        entry_year=entry_year,
        request_id=request_id,
        recent_notice_query=recent_notice_query,
        current_operational_notice_terms=current_operational_notice_terms,
        allow_wise=allow_wise,
    )

    if not frames and date_filter is not None and date_filter.relaxed_start and date_filter.relaxed_end:
        date_filter_relaxed = True
        relaxed_filter = QueryDateFilter(
            start=date_filter.relaxed_start,
            end=date_filter.relaxed_end,
            label=f"{date_filter.label}_relaxed",
            is_relative=date_filter.is_relative,
            kind=getattr(date_filter, "kind", "published"),
        )
        relaxed_frames, _, relaxed_unavailable = await _retrieve_frames_for_queries(
            route=route,
            queries=retrieval_queries,
            final_where_filter=final_where_filter,
            notice_board_filter=notice_board_filter,
            date_filter=relaxed_filter,
            entry_year=entry_year,
            request_id=request_id,
            recent_notice_query=recent_notice_query,
            current_operational_notice_terms=current_operational_notice_terms,
            allow_wise=allow_wise,
        )
        if relaxed_frames:
            frames = relaxed_frames
        unavailable_datasets = list(dict.fromkeys(unavailable_datasets + relaxed_unavailable))

    frames, staff_unavailable = await _enrich_staff_lookup_frames(
        question=raw_query,
        frames=frames,
        final_where_filter=final_where_filter,
        entry_year=entry_year,
        request_id=request_id,
        allow_wise=allow_wise,
    )
    unavailable_datasets = list(dict.fromkeys(unavailable_datasets + staff_unavailable))

    merged = _build_balanced_shortlist(
        frames,
        per_dataset=(DEFAULT_TOP_K * 2 if date_filter is not None and "schedule" in route else RAG_EVIDENCE_CANDIDATES_PER_DATASET),
    )
    if merged.empty:
        _log_event(logging.INFO, "retrieval_no_results", request_id=request_id, route=route)

    def _evaluate_fallback(current_merged: pd.DataFrame) -> tuple[float | None, bool, float, str | None]:
        top_score = None
        if not current_merged.empty and "hybrid_score" in current_merged.columns:
            top_score = _clean_response_float(current_merged["hybrid_score"].max())

        topic_aligned = False
        min_score = retrieval_policy.min_score

        reason = None
        if current_merged.empty:
            if unavailable_datasets and len(unavailable_datasets) == len(route):
                reason = FALLBACK_REASON_DATASET_UNAVAILABLE
            elif date_filter_eliminated_any:
                reason = FALLBACK_REASON_DATE_FILTER_ELIMINATED_ALL
            else:
                reason = FALLBACK_REASON_NO_RESULTS
        # 결과가 있어도 최고 점수가 임계 미만이면 환각 방지를 위해 폴백 처리한다.
        # 단, 최신 공지 질의에서 recency override가 허용되고 주제가 일치하면 낮은 점수도 통과시킨다.
        elif top_score is not None and top_score < min_score:
            reason = FALLBACK_REASON_SCORE_BELOW_THRESHOLD
        return top_score, topic_aligned, min_score, reason

    top_hybrid_score, notice_topic_aligned, effective_min_score, fallback_reason = _evaluate_fallback(merged)

    _mark_stage(stage_timings, "retrieval_and_fusion", stage_started_at)

    selector_fallback = False
    if fallback_reason is None:
        stage_started_at = time.perf_counter()
        merged, selector_fallback = await _select_answer_evidence(
            semantic_query,
            merged,
            llm_usage,
            recent_notice_query=recent_notice_query,
            date_bound_schedule_query=(date_filter is not None and "schedule" in route),
        )
        _mark_stage(stage_timings, "evidence_selection", stage_started_at)
        _log_event(
            logging.WARNING if selector_fallback else logging.INFO,
            "evidence_selection_completed",
            request_id=request_id,
            fallback=selector_fallback,
            group_count=0 if merged.empty else int(merged["evidence_group"].nunique()),
            document_count=len(merged),
        )
        if merged.empty:
            fallback_reason = FALLBACK_REASON_NO_RESULTS

    matched_queries = _collect_matched_queries(merged)

    if fallback_reason is not None:
        fallback_answer = _build_retrieval_fallback_answer(
            route=route,
            reason=fallback_reason,
            date_filter_relaxed=date_filter_relaxed,
            policy_name=retrieval_policy.name,
            clarification_reason=(
                analysis_meta.result.clarification_reason
                if analysis_meta.result is not None and analysis_meta.result.needs_clarification
                else None
            ),
        )
        _log_event(
            logging.INFO,
            "retrieval_fallback_triggered",
            request_id=request_id,
            route=route,
            top_hybrid_score=top_hybrid_score,
            threshold=effective_min_score,
            retry=date_filter_relaxed,
            fallback_reason=fallback_reason,
            policy_name=retrieval_policy.name,
            effective_min_score=effective_min_score,
            recent_notice_query=recent_notice_query,
            notice_topic_aligned=notice_topic_aligned,
            date_filter_label=None if date_filter is None else date_filter.label,
            analysis_used=analysis_meta.used,
            analysis_failed=analysis_meta.failed,
            notice_board_filter=notice_board_filter,
        )
        await run_in_threadpool(
            _save_rag_evaluation_log,
            request_id,
            session_id,
            raw_query,
            expanded_query,
            route,
            fallback_answer,
            True,
            fallback_reason,
            date_filter_applied,
            date_filter_relaxed,
            None if analysis_meta.result is None else analysis_meta.result.intent,
            None if analysis_meta.result is None else json.dumps(analysis_meta.result.entities, ensure_ascii=False),
            None if analysis_meta.result is None else analysis_meta.result.time_focus,
            None if analysis_meta.result is None else json.dumps(analysis_meta.result.search_queries, ensure_ascii=False),
            False if analysis_meta.result is None else analysis_meta.result.needs_clarification,
            None if analysis_meta.result is None else analysis_meta.result.clarification_reason,
            analysis_meta.used,
            analysis_meta.failed,
            json.dumps(matched_queries, ensure_ascii=False),
            top_hybrid_score,
            [],
            {**stage_timings, "total": round((time.perf_counter() - request_started_at) * 1000, 2)},
            llm_usage,
        )
        await run_in_threadpool(append_manual_history, session_id, raw_query, fallback_answer)
        return AskResponse(
            request_id=request_id,
            answer=fallback_answer,
            citations="",
            route=route,
            sources=[],
            resolved_intents=(
                [analysis_meta.result.intent]
                if analysis_meta.result is not None
                else []
            ),
            fallback_triggered=True,
            fallback_reason=fallback_reason,
        )

    stage_started_at = time.perf_counter()
    group_count = int(pd.to_numeric(merged["evidence_group"], errors="coerce").max())
    context_text = _build_selected_evidence_context(merged, prefix=_user_profile_prefix(req.major))
    response_instructions = "\n".join(
        instruction
        for instruction in (
            _multiple_evidence_response_instructions(group_count),
            _period_bound_response_instruction(raw_query, merged),
        )
        if instruction
    ) or None
    selected_route = list(dict.fromkeys(merged["dataset"].astype(str).tolist()))
    _mark_stage(stage_timings, "context_build", stage_started_at)
    # LLM에게 현재 날짜를 전달하여 "오늘", "이번 학기" 등의 표현을 해석하도록 돕습니다.
    current_date = _get_current_kst_string()

    try:
        stage_started_at = time.perf_counter()
        answer = await generate_langchain_answer(
            question=semantic_query,
            context=context_text,
            session_id=session_id,
            current_date=current_date,
            usage_collector=llm_usage,
            response_instructions=response_instructions,
        )
        _mark_stage(stage_timings, "generation", stage_started_at)
    except Exception as e:
        _log_event(logging.ERROR, "llm_generation_failed", exc_info=True, request_id=request_id)
        answer = "죄송합니다. 답변을 생성하는 도중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."

    # 후처리: 과도한 볼드체 제거 대신 가독성 유지 (필요 시 최소화)
    # answer = answer.replace("**", "")

    citations_raw = await run_in_threadpool(format_citations, merged)
    citations = re.sub(r'<[^>]+>', '', citations_raw)

    sources = [_source_chunk_from_row(row) for _, row in merged.iterrows()]

    suggested_questions: list[str] = []
    suggested_question_details: list[dict[str, Any]] = []
    grounding_result = None
    grounded: bool | None = None
    grounding_score: float | None = None
    if RAG_GROUNDING_CHECK_ENABLED and len(sources) > 0:
        try:
            stage_started_at = time.perf_counter()
            grounding_result = await check_answer_grounding(
                raw_query,
                answer,
                context_text,
                min_score=RAG_GROUNDING_MIN_SCORE,
                usage_collector=llm_usage,
            )
            _mark_stage(stage_timings, "grounding_check", stage_started_at)
            if grounding_result.checked:
                grounded = grounding_result.grounded
                grounding_score = grounding_result.score
                if not grounding_result.grounded:
                    answer = answer + "\n\n" + _build_grounding_confirmation_answer(grounding_result, sources)
                    suggested_questions = []
                    suggested_question_details = []
        except Exception as exc:  # noqa: BLE001
            _log_event(
                logging.WARNING,
                "grounding_check_failed",
                request_id=request_id,
                error=str(exc),
            )
    grounding_allows_followups = _grounding_allows_followups(
        RAG_GROUNDING_CHECK_ENABLED,
        grounding_result,
    )
    if RAG_SUGGEST_FOLLOWUPS and grounding_allows_followups:
        try:
            stage_started_at = time.perf_counter()
            suggested_questions = await generate_followup_questions(
                raw_query,
                answer,
                RAG_SUGGEST_FOLLOWUPS_COUNT,
                usage_collector=llm_usage,
                source_context=[source.dict() for source in sources],
                campus_scope="wise" if allow_wise else "seoul_bmc",
                supported_domains=selected_route,
            )
            suggested_question_details = build_followup_question_details(
                suggested_questions,
                [source.model_dump() for source in sources],
            )
            suggested_questions = [detail["question"] for detail in suggested_question_details]
            _mark_stage(stage_timings, "followup_generation", stage_started_at)
        except Exception as exc:  # noqa: BLE001
            _log_event(
                logging.WARNING,
                "followup_suggestions_failed",
                request_id=request_id,
                error=str(exc),
            )
    resolved_intents = list(
        dict.fromkeys(
            ([analysis_meta.result.intent] if analysis_meta.result is not None else [])
            + selected_route
        )
    )
    _mark_stage(stage_timings, "total", request_started_at)
    await run_in_threadpool(
        _save_rag_evaluation_log,
        request_id,
        session_id,
        raw_query,
        expanded_query,
        route,
        answer,
        False,
        None,
        date_filter_applied,
        date_filter_relaxed,
        None if analysis_meta.result is None else analysis_meta.result.intent,
        None if analysis_meta.result is None else json.dumps(analysis_meta.result.entities, ensure_ascii=False),
        None if analysis_meta.result is None else analysis_meta.result.time_focus,
        None if analysis_meta.result is None else json.dumps(analysis_meta.result.search_queries, ensure_ascii=False),
        False if analysis_meta.result is None else analysis_meta.result.needs_clarification,
        None if analysis_meta.result is None else analysis_meta.result.clarification_reason,
        analysis_meta.used,
        analysis_meta.failed,
        json.dumps(matched_queries, ensure_ascii=False),
        top_hybrid_score,
        sources,
        stage_timings,
        llm_usage,
    )
    if grounding_result is not None and grounding_result.checked:
        await run_in_threadpool(_update_grounding_log, request_id, grounding_result)
    await run_in_threadpool(_update_observability_log, request_id, stage_timings, llm_usage)

    _log_event(
        logging.INFO,
        "ask_completed",
        request_id=request_id,
        route=route,
        source_count=len(sources),
        top_hybrid_score=top_hybrid_score,
        policy_name=retrieval_policy.name,
        effective_min_score=effective_min_score,
        recent_notice_query=recent_notice_query,
        notice_topic_aligned=notice_topic_aligned,
        date_filter_label=None if date_filter is None else date_filter.label,
        analysis_used=analysis_meta.used,
        analysis_failed=analysis_meta.failed,
        notice_board_filter=notice_board_filter,
    )

    if RAG_SEMANTIC_CACHE_ENABLED and _should_cache_answer(
        selected_route,
        False,
        date_filter_applied,
        grounded,
        answer,
        recent_notice_query=recent_notice_query,
    ):
        await run_in_threadpool(
            semantic_cache.put,
            raw_query,
            semantic_cache_ns,
            {
                "answer": answer,
                "citations": citations,
                "route": route,
                "sources": [s.dict() if hasattr(s, "dict") else s for s in sources],
                "suggested_questions": suggested_questions,
                "suggested_question_details": suggested_question_details,
                "resolved_intents": resolved_intents,
                "grounded": grounded,
                "grounding_score": grounding_score,
            },
        )

    return AskResponse(
        request_id=request_id,
        answer=answer,
        citations=citations,
        route=route,
        resolved_intents=resolved_intents,
        sources=sources,
        suggested_questions=suggested_questions,
        suggested_question_details=[SuggestedQuestionDetail(**detail) for detail in suggested_question_details],
        grounded=grounded,
        grounding_score=grounding_score,
        fallback_triggered=False,
        fallback_reason=None,
    )


@app.post("/feedback")
async def submit_feedback(feedback: FeedbackRequest):
    allowed_reasons = {"inaccurate", "outdated", "no_source", "irrelevant", "other"}
    if feedback.rating not in (1, -1):
        raise HTTPException(status_code=400, detail="rating은 1 또는 -1이어야 합니다.")
    if feedback.reason is not None and feedback.reason not in allowed_reasons:
        raise HTTPException(status_code=400, detail="지원하지 않는 피드백 사유입니다.")

    feedback.comment = None if feedback.comment is None else feedback.comment[:2000]
    await run_in_threadpool(_save_feedback, feedback)
    _log_event(logging.INFO, "feedback_received", request_id=feedback.request_id, rating=feedback.rating)
    return {"ok": True}


@app.post("/admin/reindex/{target}")
async def reindex_dataset(target: str):
    if target not in _DATASET_LOADERS and target != "all":
        raise HTTPException(status_code=400, detail=f"Invalid target: {target}")

    try:
        from src.pipelines.ingest import reindex_from_db

        target_param = None if target == "all" else target
        # run_in_threadpool because reindexing can be slow and blocking
        results = await run_in_threadpool(reindex_from_db, target_param)
        runtime_state = await run_in_threadpool(
            refresh_runtime_dataset_state,
            list(results.keys()),
        )

        return {
            "status": "ok",
            "message": f"Reindexing for '{target}' completed.",
            "details": {k: len(v[0]) for k, v in results.items()},
            "runtime_state": runtime_state,
        }
    except Exception as e:
        _log_event(logging.ERROR, "reindex_failed", exc_info=True, target=target)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
def health() -> dict:
    """프로세스가 HTTP 요청에 응답할 수 있는지만 나타내는 liveness."""
    return {"status": "ok"}


_evaluation_hash_cache: dict[str, tuple[int, int, str]] = {}
_evaluation_hash_lock = threading.Lock()


def _evaluation_file_record(path: Path) -> dict[str, object]:
    resolved = path.resolve()
    stat = resolved.stat()
    cache_key = str(resolved)
    with _evaluation_hash_lock:
        cached = _evaluation_hash_cache.get(cache_key)
    signature = (stat.st_mtime_ns, stat.st_size)
    if cached is not None and cached[:2] == signature:
        digest = cached[2]
    else:
        hasher = hashlib.sha256()
        with resolved.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(block)
        digest = hasher.hexdigest()
        with _evaluation_hash_lock:
            _evaluation_hash_cache[cache_key] = (signature[0], signature[1], digest)
    try:
        relative_path = resolved.relative_to(rag_config.BASE_DIR.resolve()).as_posix()
    except ValueError:
        relative_path = resolved.name
    return {"path": relative_path, "bytes": stat.st_size, "sha256": digest}


def _build_evaluation_fingerprint() -> dict[str, object]:
    """Attest the configuration and artifacts used by this exact process.

    The response deliberately excludes environment values, API keys, absolute
    paths, user content, and query logs.  A runner must obtain this payload from
    the candidate URL; calculating it from its own checkout is not evidence
    that it evaluated the deployed candidate.
    """
    artifact_paths: list[Path] = []
    datasets: list[dict[str, object]] = []
    dense_index_ready = True
    for key, artifacts in sorted(DATASET_ARTIFACTS.items()):
        chunk_path = artifacts.chunk_path
        if not chunk_path.exists() and artifacts.csv_path.exists():
            chunk_path = artifacts.csv_path
        vectorizer_path = VECTORIZER_DIR / f"{key}_tfidf.pkl"
        for path in (chunk_path, vectorizer_path):
            if path.exists():
                artifact_paths.append(path)
        cached_dataset = _datasets.get(key)
        cached_chunk_count = None if cached_dataset is None else len(cached_dataset.chunks)
        chroma_count = count_items(artifacts.collection)
        dataset_dense_ready = (
            cached_chunk_count is not None
            and cached_chunk_count > 0
            and chroma_count == cached_chunk_count
        )
        dense_index_ready = dense_index_ready and dataset_dense_ready
        datasets.append(
            {
                "key": key,
                "collection": artifacts.collection,
                "chroma_count": chroma_count,
                "cached_chunk_count": cached_chunk_count,
                "dense_index_ready": dataset_dense_ready,
                "retrieval_mode": "hybrid" if dataset_dense_ready else "sparse_degraded",
                "chunk_artifact": None if not chunk_path.exists() else chunk_path.name,
                "vectorizer_artifact": None if not vectorizer_path.exists() else vectorizer_path.name,
            }
        )
    manifest_path = VECTORIZER_DIR / "manifest.json"
    if manifest_path.exists():
        artifact_paths.append(manifest_path)
    artifact_records = [
        _evaluation_file_record(path)
        for path in sorted(set(artifact_paths), key=lambda item: item.as_posix())
    ]
    artifact_digest = hashlib.sha256(
        json.dumps(
            artifact_records,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    payload: dict[str, object] = {
        "schema_version": 1,
        "build_revision": os.getenv("RAG_BUILD_REVISION", "local-unversioned").strip() or "local-unversioned",
        "answer_contract_version": "ask-response-v3-source-lineage",
        "dense_index_ready": dense_index_ready,
        "runtime_config": {
            "llm_provider": rag_config.LLM_PROVIDER,
            "query_analysis_model": rag_config.OPENAI_MODEL,
            "answer_model": (
                rag_config.OPENAI_CHAT_MODEL
                if rag_config.LLM_PROVIDER == "openai"
                else rag_config.OLLAMA_CHAT_MODEL
            ),
            "embedding_model": rag_config.EMBED_MODEL_NAME,
            "embedding_revision": rag_config.EMBED_MODEL_REVISION,
            "embedding_device": rag_config.EMBED_DEVICE,
            "reranker_enabled": rag_config.RERANKER_ENABLED,
            "reranker_model": rag_config.RERANKER_MODEL if rag_config.RERANKER_ENABLED else None,
            "reranker_revision": (
                rag_config.RERANKER_MODEL_REVISION if rag_config.RERANKER_ENABLED else None
            ),
            "top_k": rag_config.DEFAULT_TOP_K,
            "routerless": rag_config.RAG_SEARCH_ALL_DATASETS,
            "scheduler_enabled": rag_config.RAG_SCHEDULER_ENABLED,
        },
        "artifact_manifest_sha256": artifact_digest,
        "artifacts": artifact_records,
        "datasets": datasets,
    }
    payload["fingerprint_sha256"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


@app.get("/evaluation/fingerprint")
async def evaluation_fingerprint() -> dict[str, object]:
    return await run_in_threadpool(_build_evaluation_fingerprint)


@app.get("/ready")
def ready():
    """필수 startup 컴포넌트가 모두 준비됐을 때만 2xx를 반환한다."""
    snapshot = _readiness_snapshot()
    if not snapshot["ready"]:
        return JSONResponse(status_code=503, content=snapshot)
    return snapshot
