import functools
import logging
import re
import sys
import uuid
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from dataclasses import dataclass, replace
from typing import Dict, List, Tuple

sys.path.append(str(Path(__file__).resolve().parents[1]))

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from src.config import (
    APP_LOG_LEVEL,
    ASK_MAX_CONTEXT_LENGTH,
    ASK_TOP_K,
    CORRECTIVE_RETRY_ENABLED,
    DETERMINISTIC_ANSWERS_ENABLED,
    DEFAULT_TOP_K,
    EMBED_WARMUP_ENABLED,
    MAX_CONTEXT_LENGTH,
    PROCESSED_DATA_DIR,
    RECENCY_WEIGHT,
    RERANK_MIN_SCORE,
    RERANK_TOP_N,
    THIRD_PARTY_LOG_LEVEL,
)
from src.database import SessionLocal, PendingItem, CustomKnowledge, Chunk, Notice, Schedule, init_db
from src.pipelines.ingest import (
    DATASET_ARTIFACTS,
    ingest_courses,
    ingest_notices,
    ingest_rules,
    ingest_schedule,
    ingest_staff, # 추가
)
from src.search.hybrid import hybrid_search_with_meta
from src.services.answer import format_citations
from src.services.langchain_chat import generate_general_chat_answer, generate_langchain_answer
from src.models.embedding import get_embedder, encode_texts
from src.ingestion.pipeline import IngestionPipeline
from src.rag.embedder import SentenceTransformerEmbedder
from src.rag.query_intent import QueryIntent, classify_query_intent
from src.rag.retriever import Retriever
from src.rag.vector_store import ChromaVectorStore
from src.schemas.search import IngestRunRequest, SearchRequest
from src.services.query_rewrite import generate_query_variants
from src.services.reranker import rerank_hits
from src.services.router import route_query
from src.utils.date_parser import extract_date_range_from_query
from src.utils.query_expansion import expand_query
from src.utils.preprocess import make_doc_id
from src.vectorstore.chroma_client import upsert_items

app = FastAPI(
    title="동똑이",
    description="25-2 오픈소스소프트웨어프로젝트 팀 Renux의 동국대학교 캠퍼스 RAG 어시스턴트 API 서비스입니다.",
)


def configure_runtime_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, APP_LOG_LEVEL, logging.INFO),
        format="%(levelname)s:%(name)s:%(message)s",
        force=True,
    )

    quiet_level = getattr(logging, THIRD_PARTY_LOG_LEVEL, logging.WARNING)
    for logger_name in (
        "httpx",
        "httpcore",
        "urllib3",
        "chromadb",
        "huggingface_hub",
        "sentence_transformers",
        "transformers",
    ):
        logging.getLogger(logger_name).setLevel(quiet_level)


configure_runtime_logging()

@app.get("/notifications")
async def notifications_dummy():
    return []

@app.options("/notifications")
async def notifications_options_dummy():
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
}

@dataclass
class DatasetCache:
    chunks: pd.DataFrame
    chunk_path: Path
    chunk_mtime: float


_datasets: Dict[str, DatasetCache] = {}
_ingestion_pipeline = IngestionPipeline()
_document_retriever = Retriever(
    embedder=SentenceTransformerEmbedder(),
    vector_store=ChromaVectorStore(),
    collection_name="dongguk_documents",
)
_SESSION_COURSE_HITS: dict[str, list[dict]] = {}


class SourceChunk(BaseModel):
    source: str
    metadata: Dict
    snippet: str


class AskResponse(BaseModel):
    answer: str
    citations: str
    route: List[str]
    sources: List[SourceChunk]


class AskRequest(BaseModel):
    question: str = Field(..., description="사용자 질문", alias="question")
    session_id: str | None = Field(None, description="대화 세션 ID (없으면 기본 세션)", alias="sessionId")
    major: str | None = Field(None, description="사용자 학과") # 새로 추가

    class Config:
        populate_by_name = True


class SubmitRequest(BaseModel):
    source_type: str
    data: str


class ContextRequest(BaseModel):
    query: str
    category: str | None = None
    campus: str | None = None
    date_range: list[str] | None = None
    top_k: int = 5


def _score_and_sort_hits(merged: pd.DataFrame) -> pd.DataFrame:
    if merged.empty or "hybrid_score" not in merged.columns:
        return merged

    ranked = merged.copy()
    if "published_at" in ranked.columns and "updated_at" in ranked.columns:
        ranked["sort_date"] = pd.to_datetime(ranked["published_at"].fillna(ranked["updated_at"]), errors='coerce')
    elif "published_at" in ranked.columns:
        ranked["sort_date"] = pd.to_datetime(ranked["published_at"], errors='coerce')
    elif "updated_at" in ranked.columns:
        ranked["sort_date"] = pd.to_datetime(ranked["updated_at"], errors='coerce')
    else:
        ranked["sort_date"] = pd.NaT

    ranked.dropna(subset=["hybrid_score"], inplace=True)

    if ranked.empty:
        return ranked

    min_hybrid = ranked["hybrid_score"].min()
    max_hybrid = ranked["hybrid_score"].max()
    if max_hybrid > min_hybrid:
        ranked["norm_hybrid"] = (ranked["hybrid_score"] - min_hybrid) / (max_hybrid - min_hybrid)
    else:
        ranked["norm_hybrid"] = 1.0

    valid_dates = ranked["sort_date"].dropna()
    if not valid_dates.empty:
        min_date = valid_dates.min().timestamp()
        max_date = valid_dates.max().timestamp()
        ranked["sort_timestamp"] = ranked["sort_date"].apply(lambda x: x.timestamp() if pd.notnull(x) else min_date)
        if max_date > min_date:
            ranked["norm_recency"] = (ranked["sort_timestamp"] - min_date) / (max_date - min_date)
        else:
            ranked["norm_recency"] = 1.0
    else:
        ranked["norm_recency"] = 0.0

    ranked["final_score"] = (1 - RECENCY_WEIGHT) * ranked["norm_hybrid"] + RECENCY_WEIGHT * ranked["norm_recency"]
    ranked.sort_values(by="final_score", ascending=False, inplace=True)
    return ranked


def _should_retry_retrieval(merged) -> bool:
    if isinstance(merged, list):
        if not merged:
            return True
        top_score = float(merged[0].get("score") or 0.0)
        return top_score < RERANK_MIN_SCORE

    if merged.empty:
        return True

    top_score = 0.0
    if "rerank_score" in merged.columns and not merged["rerank_score"].empty:
        top_score = float(merged["rerank_score"].iloc[0])
    elif "final_score" in merged.columns and not merged["final_score"].empty:
        top_score = float(merged["final_score"].iloc[0])
    elif "hybrid_score" in merged.columns and not merged["hybrid_score"].empty:
        top_score = float(merged["hybrid_score"].iloc[0])

    return top_score < RERANK_MIN_SCORE


def _fallback_answer_from_hits(query: str, hits: list[dict]) -> str:
    if not hits:
        return "검색된 관련 문서가 없어 답변을 생성하지 못했습니다. 질문을 조금 더 구체적으로 입력해 주세요."

    lines = [
        "답변 생성이 지연되어, 검색된 문서 기준으로 먼저 확인된 내용을 요약해드릴게요.",
        "",
    ]
    for idx, hit in enumerate(hits[:3], start=1):
        metadata = hit.get("metadata") or {}
        title = metadata.get("title") or "제목 없음"
        published_at = metadata.get("published_at")
        source_url = metadata.get("source_url") or metadata.get("url")
        snippet = re.sub(r"\s+", " ", hit.get("content", "")).strip()[:350]
        lines.append(f"{idx}. {title}")
        if published_at:
            lines.append(f"    - 게시일: {published_at}")
        if snippet:
            lines.append(f"    - 관련 내용: {snippet}")
        if source_url:
            lines.append(f"    - [사이트로 이동하기]({source_url})")
        lines.append("")
    return "\n".join(lines).strip()


def _no_results_answer(intent: QueryIntent) -> str:
    labels = {
        "food": "식단표",
        "library": "도서관 이용정보",
        "department": "부서/학과 연락처",
        "calendar": "학사일정",
    }
    target = labels.get(intent.name, "관련 문서")
    return f"현재 수집된 데이터에서 {target} 정보를 찾지 못했습니다. 데이터가 아직 수집되지 않았거나 질문 조건에 맞는 문서가 없을 수 있습니다."


def _build_search_intent(query: str, explicit_category: str | None = None) -> QueryIntent:
    intent = classify_query_intent(query, explicit_category=explicit_category)
    if explicit_category:
        return replace(intent, hard_filters={}, block_fallback=False)
    return intent


def _is_latest_notice_query(query: str) -> bool:
    normalized = re.sub(r"\s+", "", query.lower())
    latest_terms = ("최근", "최신", "새공지", "새로운공지", "올라온공지", "방금", "오늘공지")
    return "공지" in normalized and any(term in normalized for term in latest_terms)


def _is_date_scoped_notice_query(query: str, date_range: list[str] | None) -> bool:
    normalized = re.sub(r"\s+", "", query.lower())
    return bool(date_range) and "공지" in normalized


def _is_exchange_scholarship_query(query: str) -> bool:
    normalized = re.sub(r"\s+", "", query.lower())
    return "교환학생" in normalized and ("장학" in normalized or "장학금" in normalized)


def _is_school_info_query(query: str) -> bool:
    normalized = re.sub(r"\s+", "", query.lower())
    school_info_keywords = (
        "동국",
        "동대",
        "학교",
        "대학",
        "캠퍼스",
        "공지",
        "학사",
        "장학",
        "장학금",
        "수강",
        "수업",
        "강의",
        "교과목",
        "과목",
        "계절학기",
        "졸업",
        "졸업요건",
        "학점",
        "성적",
        "등록",
        "등록금",
        "휴학",
        "복학",
        "재입학",
        "교환학생",
        "국제",
        "기숙사",
        "학식",
        "식단",
        "밥",
        "메뉴",
        "도서관",
        "열람실",
        "캠퍼스맵",
        "시설",
        "전화번호",
        "부서",
        "학과",
        "전공",
        "교수",
        "직원",
        "규정",
        "학칙",
        "일정",
        "학생증",
        "셔틀",
        "버스",
        "주차",
        "ndrims",
        "엔드림스",
        "드림패스",
        "eclass",
        "이클래스",
    )
    return any(keyword in normalized for keyword in school_info_keywords)


def _latest_notice_hits(top_k: int, date_range: list[str] | None = None) -> list[dict]:
    index_path = PROCESSED_DATA_DIR / "document_index.jsonl"
    if not index_path.exists():
        return []

    rows: list[dict] = []
    with index_path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            try:
                document = json.loads(line)
            except json.JSONDecodeError:
                continue
            category = str(document.get("category") or "")
            document_type = str(document.get("document_type") or "")
            published_at = str(document.get("published_at") or "")
            if document_type != "notice" or "공지" not in category or not published_at:
                continue
            if date_range and not _date_string_in_range(published_at, date_range):
                continue
            rows.append(document)

    rows.sort(
        key=lambda item: (
            str(item.get("published_at") or ""),
            str(item.get("collected_at") or ""),
            str(item.get("updated_at") or ""),
        ),
        reverse=True,
    )

    hits: list[dict] = []
    seen: set[str] = set()
    for document in rows:
        dedup_key = str(document.get("url") or document.get("id") or document.get("title"))
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        metadata = {
            "document_id": document.get("id") or "",
            "source": document.get("source") or "",
            "category": document.get("category") or "",
            "sub_category": document.get("sub_category") or "",
            "title": document.get("title") or "",
            "url": document.get("url") or "",
            "source_url": document.get("url") or "",
            "published_at": document.get("published_at") or "",
            "department": document.get("department") or "",
            "campus": document.get("campus") or "",
            "document_type": document.get("document_type") or "",
            "chunk_index": 0,
        }
        hits.append(
            {
                "id": str(document.get("id") or dedup_key),
                "score": 1.0,
                "content": str(document.get("content") or "")[:1200],
                "metadata": metadata,
            }
        )
        if len(hits) >= top_k:
            break
    return hits


def _date_string_in_range(value: str, date_range: list[str]) -> bool:
    if len(date_range) != 2:
        return True
    target = value[:10]
    return date_range[0] <= target <= date_range[1]


async def _date_range_filter_from_query(query: str, explicit_date_range: list[str] | None = None) -> list[str] | None:
    if explicit_date_range:
        return explicit_date_range
    parsed_range = await run_in_threadpool(extract_date_range_from_query, query)
    if not parsed_range:
        return None
    return [
        parsed_range[0].strftime("%Y-%m-%d"),
        parsed_range[1].strftime("%Y-%m-%d"),
    ]


def _answer_latest_notices(hits: list[dict]) -> str:
    if not hits:
        return "현재 수집된 공지 데이터에서 최근 공지를 찾지 못했습니다."

    lines = ["최근 올라온 공지는 다음과 같습니다."]
    for idx, hit in enumerate(hits, start=1):
        metadata = hit.get("metadata") or {}
        title = metadata.get("title") or "제목 없음"
        category = metadata.get("category") or "공지"
        published_at = metadata.get("published_at") or "게시일 없음"
        department = metadata.get("department") or "담당 부서 미상"
        source_url = metadata.get("source_url") or metadata.get("url")
        lines.append("")
        lines.append(f"{idx}. {title}")
        lines.append(f"    - 구분: {category}")
        lines.append(f"    - 게시일: {published_at}")
        lines.append(f"    - 부서: {department}")
        if source_url:
            lines.append(f"    - [사이트로 이동하기]({source_url})")
    return "\n".join(lines)


def _answer_exchange_scholarships(hits: list[dict]) -> str:
    if not hits:
        return "현재 수집된 자료에서 교환학생 장학금 정보를 찾지 못했습니다."

    general_hit = next(
        (
            hit
            for hit in hits
            if "교환학생 장학" in hit.get("content", "") and "수업료" in hit.get("content", "")
        ),
        None,
    )
    odo_hit = next(
        (
            hit
            for hit in hits
            if "오도행장학" in ((hit.get("metadata") or {}).get("title", "") + hit.get("content", ""))
        ),
        None,
    )

    lines = ["현재 자료에서 확인되는 교환학생 관련 장학금은 다음과 같습니다."]

    if general_hit:
        metadata = general_hit.get("metadata") or {}
        source_url = metadata.get("source_url") or metadata.get("url")
        lines.extend(
            [
                "",
                "1. 교환학생 장학",
                "    - 혜택: 해외 파견학기 본교 수업료 30% 감면으로 확인됩니다.",
                "    - 요건: 파견 직전학기 15학점 이상 이수 및 평점 3.0 이상이 필요합니다.",
                "    - 예외: 7학기 이수생은 파견 직전학기 12학점 이상 이수 및 평점 3.0 이상으로 안내되어 있습니다.",
                "    - 주의: 정규 8학기 초과학기를 해외에서 수학하는 경우 해당 학기 교환학생 장학 수혜가 불가할 수 있습니다.",
            ]
        )
        if source_url:
            lines.append(f"    - [사이트로 이동하기]({source_url})")

    if odo_hit:
        metadata = odo_hit.get("metadata") or {}
        source_url = metadata.get("source_url") or metadata.get("url")
        lines.extend(
            [
                "",
                "2. 오도행장학",
                "    - 대상: 2026-2학기 해외 교환학생 파견 예정자로 안내되어 있습니다.",
                "    - 금액: 1인당 100만원 생활비성 장학금입니다.",
                "    - 선발인원: 10명으로 안내되어 있습니다.",
                "    - 신청기간: 2026년 5월 1일(금)부터 5월 10일(일)까지입니다.",
                "    - 주요 요건: 2026-1학기 기준 학부 재학생, 국가장학금 0~8분위, 직전학기 평점 3.0 이상 및 15학점 이상 이수 등이 확인됩니다.",
                "    - 신청방법: 신청서를 이메일(outbound1@dongguk.edu)로 제출합니다.",
            ]
        )
        if source_url:
            lines.append(f"    - [사이트로 이동하기]({source_url})")

    if len(lines) == 1:
        return _fallback_answer_from_hits("교환학생 장학금", hits)

    lines.extend(
        [
            "",
            "정확한 신청 가능 여부는 선발 언어권, 파견 학기, 학적 상태에 따라 달라질 수 있으니 원문 공지를 함께 확인해 주세요.",
        ]
    )
    return "\n".join(lines)


def _current_kst_date() -> date:
    return datetime.now(timezone(timedelta(hours=9))).date()


def _parse_iso_date(value: object) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _food_subcategory(intent: QueryIntent) -> str | None:
    sub_category = intent.hard_filters.get("sub_category") if intent.hard_filters else None
    return str(sub_category) if sub_category else None


def _food_date_rank(document: dict, today: date | None = None) -> tuple[int, int]:
    today = today or _current_kst_date()
    valid_from = _parse_iso_date(document.get("valid_from"))
    valid_until = _parse_iso_date(document.get("valid_until"))
    published_at = _parse_iso_date(document.get("published_at"))

    if valid_from and valid_until and valid_from <= today <= valid_until:
        return (3, 0)
    if valid_from and valid_from > today:
        return (2, -min((valid_from - today).days, 365))
    if valid_until and valid_until < today:
        return (1, -min((today - valid_until).days, 365))
    if published_at:
        return (0, -min(abs((today - published_at).days), 365))
    return (-1, -365)


def _compact_food_snippet(content: str, limit: int = 420) -> str:
    cleaned = re.sub(r"https?://\S+", "", content)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "..."


def _food_hit_from_document(document: dict, score: float) -> dict:
    source_url = document.get("url") or ""
    metadata = {
        "document_id": document.get("id") or "",
        "source": document.get("source") or "",
        "category": document.get("category") or "",
        "sub_category": document.get("sub_category") or "",
        "title": document.get("title") or "",
        "url": source_url,
        "source_url": source_url,
        "published_at": document.get("published_at") or "",
        "updated_at": document.get("updated_at") or "",
        "department": document.get("department") or "",
        "campus": document.get("campus") or "",
        "document_type": document.get("document_type") or "",
        "valid_from": document.get("valid_from") or "",
        "valid_until": document.get("valid_until") or "",
        "chunk_index": 0,
    }
    return {
        "id": str(document.get("id") or source_url or document.get("title") or "food"),
        "score": score,
        "content": _compact_food_snippet(str(document.get("content") or "")),
        "metadata": metadata,
    }


def _food_document_hits(top_k: int, sub_category: str | None = None, group_by_subcategory: bool = False) -> list[dict]:
    index_path = PROCESSED_DATA_DIR / "document_index.jsonl"
    if not index_path.exists():
        return []

    documents: list[dict] = []
    with index_path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            try:
                document = json.loads(line)
            except json.JSONDecodeError:
                continue
            if document.get("document_type") != "food":
                continue
            if sub_category and document.get("sub_category") != sub_category:
                continue
            documents.append(document)

    today = _current_kst_date()
    documents.sort(
        key=lambda item: (
            _food_date_rank(item, today),
            str(item.get("published_at") or ""),
            str(item.get("collected_at") or ""),
            str(item.get("title") or ""),
        ),
        reverse=True,
    )

    hits: list[dict] = []
    seen: set[str] = set()
    seen_subcategories: set[str] = set()
    for document in documents:
        dedup_key = str(document.get("url") or document.get("id") or document.get("title"))
        if dedup_key in seen:
            continue
        if group_by_subcategory:
            category_key = str(document.get("sub_category") or "기타")
            if category_key in seen_subcategories:
                continue
            seen_subcategories.add(category_key)
        seen.add(dedup_key)
        rank, distance = _food_date_rank(document, today)
        hits.append(_food_hit_from_document(document, score=float(rank) + max(distance, -30) / 100.0))
        if len(hits) >= top_k:
            break
    return hits


def _food_period(metadata: dict) -> str:
    valid_from = metadata.get("valid_from")
    valid_until = metadata.get("valid_until")
    if valid_from and valid_until:
        return f"{valid_from} ~ {valid_until}"
    return str(metadata.get("published_at") or "기간 정보 없음")


def _answer_food_menu(hits: list[dict], sub_category: str | None = None) -> str:
    if not hits:
        if sub_category:
            return f"현재 수집된 식단 데이터에서 {sub_category} 식단표를 찾지 못했습니다."
        return "현재 수집된 식단 데이터에서 학식 메뉴를 찾지 못했습니다."

    if not sub_category:
        order = {"상록원": 0, "D-Flex": 1, "남산학사": 2}
        unique_hits = sorted(
            hits,
            key=lambda hit: order.get(str((hit.get("metadata") or {}).get("sub_category") or ""), 99),
        )
        lines = ["식당명을 지정하지 않아, 수집된 식당별 최신 식단표를 보여드립니다."]
        for idx, hit in enumerate(unique_hits, start=1):
            metadata = hit.get("metadata") or {}
            title = metadata.get("title") or "식단표"
            cafeteria = metadata.get("sub_category") or metadata.get("department") or "식당"
            source_url = metadata.get("source_url") or metadata.get("url")
            lines.append("")
            lines.append(f"{idx}. {cafeteria}")
            lines.append(f"    - 식단표: {title}")
            lines.append(f"    - 기간: {_food_period(metadata)}")
            if source_url:
                lines.append(f"    - [원문 보기]({source_url})")
        lines.append("")
        lines.append("특정 식당 메뉴를 원하면 '상록원 학식', 'D-Flex 메뉴', '남산학사 식단'처럼 물어봐 주세요.")
        return "\n".join(lines)

    top = hits[0]
    metadata = top.get("metadata") or {}
    title = metadata.get("title") or f"{sub_category} 식단표"
    source_url = metadata.get("source_url") or metadata.get("url")
    snippet = _compact_food_snippet(str(top.get("content") or ""), limit=650)

    lines = [f"{sub_category} 최신 식단표입니다."]
    lines.append(f"- 식단표: {title}")
    lines.append(f"- 기간: {_food_period(metadata)}")
    if source_url:
        lines.append(f"- [원문 보기]({source_url})")
    if snippet:
        lines.append("")
        lines.append(f"확인된 식단표 일부: {snippet}")
    lines.append("")
    lines.append("식단표가 이미지/PDF 기반인 경우 OCR 오차가 있을 수 있으니, 최종 확인은 원문 표를 기준으로 해 주세요.")
    return "\n".join(lines)




def _format_phone_number(phone: str) -> str:
    compact = re.sub(r"[^\d]", "", phone)
    if len(compact) == 10 and compact.startswith("02"):
        return f"02-{compact[2:6]}-{compact[6:]}"
    if len(compact) == 9 and compact.startswith("02"):
        return f"02-{compact[2:5]}-{compact[5:]}"
    if len(compact) == 11:
        return f"{compact[:3]}-{compact[3:7]}-{compact[7:]}"
    return phone.strip()


def _public_source_url(metadata: dict) -> str:
    url = str(metadata.get("source_url") or metadata.get("url") or "").strip()
    return url if url.startswith(("http://", "https://")) else ""


def _metadata_for_response(metadata: dict) -> dict:
    sanitized = dict(metadata or {})
    public_url = _public_source_url(sanitized)
    if public_url:
        sanitized["source_url"] = public_url
        sanitized["url"] = public_url
        return sanitized
    if str(sanitized.get("source_url") or "").startswith("dongguk://"):
        sanitized.pop("source_url", None)
    if str(sanitized.get("url") or "").startswith("dongguk://"):
        sanitized.pop("url", None)
    return sanitized


def _extract_labeled_value(content: str, label: str) -> str:
    pattern = rf"{re.escape(label)}\s*:\s*(.+?)(?=\s+[가-힣A-Za-z/()·]+:|$)"
    match = re.search(pattern, content)
    return match.group(1).strip().strip(")") if match else ""


def _answer_department_contacts(hits: list[dict]) -> str:
    department_hits = [hit for hit in hits if (hit.get("metadata") or {}).get("document_type") == "department"]
    if not department_hits:
        return "현재 수집된 연락처 데이터에서 관련 부서/학과 전화번호를 찾지 못했습니다."

    lines = ["검색된 연락처는 다음과 같습니다."]
    seen: set[tuple[str, str, str]] = set()
    shown = 0
    for hit in department_hits:
        metadata = hit.get("metadata") or {}
        content = re.sub(r"\s+", " ", str(hit.get("content") or "")).strip()
        organization = _extract_labeled_value(content, "조직") or metadata.get("department") or "부서 미상"
        duty = _extract_labeled_value(content, "담당업무") or metadata.get("title") or "담당업무 미상"
        phone = _extract_labeled_value(content, "전화번호")
        phone = _format_phone_number(phone) if phone else "전화번호 미상"
        key = (organization, duty, phone)
        if key in seen:
            continue
        seen.add(key)
        shown += 1
        lines.append("")
        lines.append(f"{shown}. {organization}")
        lines.append(f"    - 담당업무: {duty}")
        lines.append(f"    - 전화번호: {phone}")
        source_url = metadata.get("source_url") or metadata.get("url")
        if source_url:
            lines.append(f"    - [원문 보기]({source_url})")
        if shown >= 5:
            break
    return "\n".join(lines)


def _answer_calendar_events(hits: list[dict]) -> str:
    calendar_hits = [hit for hit in hits if (hit.get("metadata") or {}).get("document_type") == "calendar"]
    if not calendar_hits:
        return "현재 수집된 학사일정 데이터에서 관련 일정을 찾지 못했습니다."

    lines = ["검색된 학사일정은 다음과 같습니다."]
    for idx, hit in enumerate(calendar_hits[:5], start=1):
        metadata = hit.get("metadata") or {}
        content = re.sub(r"\s+", " ", str(hit.get("content") or "")).strip()
        title = metadata.get("title") or _extract_labeled_value(content, "일정명") or "일정명 없음"
        period = _extract_labeled_value(content, "기간") or metadata.get("published_at") or "기간 정보 없음"
        department = _extract_labeled_value(content, "주관부서") or metadata.get("department") or "주관부서 미상"
        lines.append("")
        lines.append(f"{idx}. {title}")
        lines.append(f"    - 기간: {period}")
        lines.append(f"    - 주관부서: {department}")
        source_url = metadata.get("source_url") or metadata.get("url")
        if source_url:
            lines.append(f"    - [원문 보기]({source_url})")
    return "\n".join(lines)


def _answer_library_info(hits: list[dict]) -> str:
    library_hits = [hit for hit in hits if (hit.get("metadata") or {}).get("document_type") == "library"]
    if not library_hits:
        return "현재 수집된 도서관 데이터에서 관련 이용정보를 찾지 못했습니다."

    lines = ["검색된 도서관 이용정보는 다음과 같습니다."]
    for idx, hit in enumerate(library_hits[:5], start=1):
        metadata = hit.get("metadata") or {}
        title = metadata.get("title") or "도서관 이용정보"
        sub_category = metadata.get("sub_category") or "이용안내"
        snippet = re.sub(r"\s+", " ", str(hit.get("content") or "")).strip()
        lines.append("")
        lines.append(f"{idx}. {title}")
        lines.append(f"    - 구분: {sub_category}")
        if snippet and re.search(r"\d{1,2}:\d{2}|평일|토요일|방학|학기 중", snippet):
            lines.append(f"    - 확인된 내용: {snippet[:220]}")
        source_url = metadata.get("source_url") or metadata.get("url")
        if source_url:
            lines.append(f"    - [원문 보기]({source_url})")
    lines.append("")
    lines.append("도서관 운영시간은 학기/방학/시험기간에 따라 바뀔 수 있으므로 원문 공지를 함께 확인해 주세요.")
    return "\n".join(lines)


def _course_hits_for_listing(hits: list[dict]) -> list[dict]:
    course_hits = [hit for hit in hits if (hit.get("metadata") or {}).get("document_type") == "course"]
    major_course_hits = [
        hit
        for hit in course_hits
        if "dongguk://major-course/" in str((hit.get("metadata") or {}).get("source_url") or (hit.get("metadata") or {}).get("url") or "")
        or "학점:" in str(hit.get("content") or "")
    ]
    if major_course_hits:
        return major_course_hits
    return course_hits


def _answer_courses(hits: list[dict]) -> str:
    course_hits = _course_hits_for_listing(hits)
    if not course_hits:
        return "현재 수집된 교육과정 데이터에서 관련 교과목을 찾지 못했습니다."

    lines = ["수집된 교육과정 데이터에서 확인되는 교과목은 다음과 같습니다."]
    seen: set[str] = set()
    shown = 0
    for hit in course_hits:
        metadata = hit.get("metadata") or {}
        content = re.sub(r"\s+", " ", str(hit.get("content") or "")).strip()
        code = _extract_labeled_value(content, "학수번호")
        name = _extract_labeled_value(content, "교과목명")
        credits = _extract_labeled_value(content, "학점")
        course_type = _extract_labeled_value(content, "전공구분") or metadata.get("sub_category") or ""
        target = _extract_labeled_value(content, "이수대상")
        semester = _extract_labeled_value(content, "개설학기")
        note = _extract_labeled_value(content, "비고")
        title = " ".join(part for part in [code, name] if part) or metadata.get("title") or "교과목명 없음"
        if title in seen:
            continue
        seen.add(title)
        shown += 1
        lines.append("")
        lines.append(f"{shown}. {title}")
        if credits:
            lines.append(f"    - 학점: {credits}")
        if course_type:
            lines.append(f"    - 전공구분: {course_type}")
        if target:
            lines.append(f"    - 이수대상: {target}")
        if semester:
            lines.append(f"    - 개설학기: {semester}")
        if note:
            lines.append(f"    - 비고: {note}")
        source_url = _public_source_url(metadata)
        if source_url:
            lines.append(f"    - 출처: {source_url}")
        else:
            lines.append("    - 출처: 수집된 교육과정 데이터")
        if shown >= 8:
            break

    lines.append("")
    lines.append("위 목록은 현재 수집된 교육과정 데이터에 있는 항목만 표시한 것입니다.")
    return "\n".join(lines)


def _course_followup_index(query: str) -> int | None:
    normalized = query.replace(" ", "")
    if not re.search(r"교과목|과목|강의|수업|배워|내용|해설", normalized):
        return None
    match = re.search(r"(\d+)\s*번", query)
    if not match:
        return None
    index = int(match.group(1)) - 1
    return index if index >= 0 else None


def _course_identity(hit: dict) -> tuple[str, str, str]:
    metadata = hit.get("metadata") or {}
    content = re.sub(r"\s+", " ", str(hit.get("content") or "")).strip()
    code = _extract_labeled_value(content, "학수번호")
    name = _extract_labeled_value(content, "교과목명")
    title = " ".join(part for part in [code, name] if part) or metadata.get("title") or "교과목명 없음"
    return code, name, title


def _find_course_description(code: str, name: str) -> dict | None:
    index_path = PROCESSED_DATA_DIR / "document_index.jsonl"
    if not index_path.exists():
        return None
    with index_path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            try:
                document = json.loads(line)
            except json.JSONDecodeError:
                continue
            if document.get("document_type") != "course":
                continue
            content = str(document.get("content") or "")
            if "해설:" not in content:
                continue
            if code and f"학수번호: {code}" in content:
                return document
            if name and f"교과목명: {name}" in content:
                return document
    return None


def _answer_course_detail_from_hit(hit: dict) -> tuple[str, list[dict]]:
    metadata = hit.get("metadata") or {}
    content = re.sub(r"\s+", " ", str(hit.get("content") or "")).strip()
    code, name, title = _course_identity(hit)
    credits = _extract_labeled_value(content, "학점")
    course_type = _extract_labeled_value(content, "전공구분") or metadata.get("sub_category") or ""
    target = _extract_labeled_value(content, "이수대상")
    semester = _extract_labeled_value(content, "개설학기")
    note = _extract_labeled_value(content, "비고")

    description_doc = _find_course_description(code, name)
    description = ""
    description_source = ""
    if description_doc:
        description_content = re.sub(r"\s+", " ", str(description_doc.get("content") or "")).strip()
        description = _extract_labeled_value(description_content, "해설")
        description_source = str(description_doc.get("url") or "")

    lines = [f"{title}에서 확인된 내용입니다."]
    if description:
        lines.append(f"- 교과목 해설: {description}")
    else:
        lines.append("- 교과목 해설: 현재 수집된 데이터에서 해설 본문은 확인되지 않습니다.")
    if credits:
        lines.append(f"- 학점: {credits}")
    if course_type:
        lines.append(f"- 전공구분: {course_type}")
    if target:
        lines.append(f"- 이수대상: {target}")
    if semester:
        lines.append(f"- 개설학기: {semester}")
    if note:
        lines.append(f"- 비고: {note}")
    source_url = description_source if description_source.startswith(("http://", "https://")) else _public_source_url(metadata)
    if source_url:
        lines.append(f"- 출처: {source_url}")
    else:
        lines.append("- 출처: 수집된 교육과정 데이터")
    lines.append("위 내용은 수집된 교육과정 데이터에 있는 항목만 사용했습니다.")

    sources = [hit]
    if description_doc:
        sources.insert(
            0,
            _food_hit_from_document({**description_doc, "document_type": "course"}, score=1.0),
        )
    return "\n".join(lines), sources


def _answer_course_followup(query: str, session_id: str) -> tuple[str, list[dict]] | None:
    index = _course_followup_index(query)
    if index is None:
        return None
    previous_hits = _SESSION_COURSE_HITS.get(session_id) or []
    if index >= len(previous_hits):
        return "직전 교과목 목록에서 해당 번호를 찾지 못했습니다. 교과목명을 직접 입력해 주세요.", []
    return _answer_course_detail_from_hit(previous_hits[index])


def _ensure_dataset(key: str) -> Tuple[pd.DataFrame, None, None]:
    artifacts = DATASET_ARTIFACTS.get(key)
    if artifacts is None:
        raise KeyError(f"Unsupported dataset '{key}'")
    
    chunk_path = artifacts.chunk_path
    csv_path = artifacts.csv_path

    if not chunk_path.exists() and csv_path.exists():
        artifacts.chunk_path = csv_path
        chunk_path = csv_path

    chunk_mtime = chunk_path.stat().st_mtime if chunk_path.exists() else -1.0

    cache = _datasets.get(key)
    if cache and cache.chunk_path == chunk_path and cache.chunk_mtime == chunk_mtime:
        return cache.chunks, None, None

    try:
        if chunk_path.exists():
            if chunk_path.suffix == ".csv":
                chunks_df = pd.read_csv(chunk_path)
            else:
                chunks_df = pd.read_parquet(chunk_path)
        else:
            chunks_df, _, _ = _DATASET_LOADERS[key]()
            chunk_path = DATASET_ARTIFACTS[key].chunk_path
            chunk_mtime = chunk_path.stat().st_mtime if chunk_path.exists() else -1.0
    except FileNotFoundError:
        chunks_df, _, _ = _DATASET_LOADERS[key]()
        chunk_path = DATASET_ARTIFACTS[key].chunk_path
        chunk_mtime = chunk_path.stat().st_mtime if chunk_path.exists() else -1.0

    _datasets[key] = DatasetCache(
        chunks=chunks_df,
        chunk_path=chunk_path,
        chunk_mtime=chunk_mtime,
    )
    return chunks_df, None, None


def _add_to_dataset_cache(key: str, doc_id: str, text: str, metadata: Dict) -> None:
    """캐시된 데이터셋에 새 항목을 점진적으로 추가합니다 (전체 리로드 방지)."""
    if key not in _datasets:
        # 캐시에 없으면 로드 (이 시점에 로드하는 것은 어쩔 수 없음, 하지만 이후에는 캐시됨)
        _ensure_dataset(key)
    
    cache = _datasets[key]
    
    # 1. DataFrame에 행 추가
    new_row = metadata.copy()
    new_row["chunk_id"] = doc_id
    new_row["chunk_text"] = text
    # ensure all columns exist
    for col in cache.chunks.columns:
        if col not in new_row:
            new_row[col] = None
            
    # pd.concat is better than append
    new_df = pd.DataFrame([new_row])
    # 기존 컬럼 순서 유지를 위해 reindex
    new_df = new_df.reindex(columns=cache.chunks.columns)
    
    cache.chunks = pd.concat([cache.chunks, new_df], ignore_index=True)

    logging.info(f"⚡ Incremental update for '{key}': Added 1 item. New size: {len(cache.chunks)}")


@app.on_event("startup")
def bootstrap_artifacts() -> None:
    """애플리케이션 시작 시 데이터셋과 분류기 등 주요 아티팩트를 미리 로드합니다."""
    # Ensure DB tables exist
    try:
        init_db()
        logging.info("✅ Database tables initialized.")
    except Exception as e:
        logging.error(f"❌ Failed to initialize database: {e}")
    
    for key in _DATASET_LOADERS:
        try:
            _ensure_dataset(key)
            logging.info(f"✅ Dataset '{key}' successfully loaded.")
        except (KeyError, FileNotFoundError, ValueError) as exc:
            logging.error(f"⚠️ Failed to warmup dataset '{key}': {exc}", exc_info=True)
            # 데이터셋 로드 실패는 심각한 문제일 수 있으므로,
            # 필요에 따라 여기서 애플리케이션을 종료시키는 로직을 추가할 수 있습니다.
            # Ex: raise RuntimeError(f"Critical failure loading dataset {key}") from exc

    if EMBED_WARMUP_ENABLED:
        try:
            logging.info("⏳ Warming up embedding model...")
            get_embedder()
            logging.info("✅ Embedding model warmup completed.")
        except Exception as exc:
            logging.warning("⚠️ Embedding model warmup failed: %s", exc)
    else:
        logging.info("ℹ️ Embedding model warmup skipped.")



@app.post("/admin/submit")
async def submit_pending(req: SubmitRequest):
    session = SessionLocal()
    try:
        item = PendingItem(
            source_type=req.source_type,
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



@app.post("/admin/approve/{item_id}")
async def approve_pending(item_id: int):
    session = SessionLocal()
    try:
        logging.info(f"👉 [Admin] Approving item ID: {item_id}")
        item = session.query(PendingItem).filter(PendingItem.id == item_id).first()
        if not item:
            logging.error(f"❌ [Admin] Item not found: {item_id}")
            return {"status": "error", "message": "Item not found"}

        data = json.loads(item.data)
        
        # 공통 Notice 객체 생성 준비
        notice = None
        
        if item.source_type == "custom_knowledge":
            logging.info(f"📝 [Admin] Processing custom knowledge: {data.get('question')}")
            
            notice = Notice(
                board=data.get("category", "기타"), # e.g. 학과정보
                title=data.get("question"),
                category="FAQ",
                published_date=datetime.now().strftime("%Y-%m-%d"),
                content=data.get("answer"),
                is_manual=1
            )

        elif item.source_type == "event":
            logging.info(f"📅 [Admin] Processing event: {data.get('title')}")
            
            # 내용을 상세하게 구성
            content_parts = []
            if data.get("description"):
                content_parts.append(data.get("description"))
            
            date_str = f"일시: {data.get('start_date')}"
            if data.get("end_date") and data.get("end_date") != data.get("start_date"):
                date_str += f" ~ {data.get('end_date')}"
            content_parts.append(date_str)
            
            if data.get("location"):
                content_parts.append(f"장소: {data.get('location')}")
                
            full_content = "\n\n".join(content_parts)

            notice = Notice(
                board=data.get("department", "학과행사"),
                title=data.get("title"),
                category="행사",
                published_date=data.get("start_date"),
                content=full_content,
                is_manual=1
            )

        elif item.source_type == "announcement":
            logging.info(f"📢 [Admin] Processing announcement: {data.get('title')}")
            
            notice = Notice(
                board=data.get("department", "공지사항"),
                title=data.get("title"),
                category=data.get("category", "일반"),
                published_date=data.get("date"),
                content=data.get("content"),
                is_manual=1
            )
        
        if notice:
            # 1. Save to DB (Notices table)
            session.add(notice)
            session.commit()
            logging.info(f"✅ [Admin] Notice saved to DB. ID: {notice.id}")

            # 2. Create Chunk
            doc_id = make_doc_id(notice.title, notice.board, notice.published_date)

            # Check for collision
            existing_chunk = session.query(Chunk).filter(Chunk.chunk_id == doc_id).first()
            if existing_chunk:
                logging.warning(f"⚠️ [Admin] Chunk ID collision for {doc_id}. Appending random UUID.")
                doc_id = f"{doc_id}_{uuid.uuid4().hex[:8]}"
            
            text_content = notice.content
            prefix_parts = []
            if notice.board:
                prefix_parts.append(f"게시판: {notice.board}")
            if notice.category:
                prefix_parts.append(f"분류: {notice.category}")
            if notice.published_date:
                prefix_parts.append(f"게시일: {notice.published_date}")
            
            if prefix_parts:
                text_content = f"[{', '.join(prefix_parts)}]\n\n{text_content}"

            chunk = Chunk(
                chunk_id=doc_id,
                chunk_text=text_content,
                notice_id=notice.id
            )
            session.add(chunk)
            session.commit()

            # 3. Upsert to Chroma (dongguk_notices)
            target_collection = "dongguk_notices"
            embedding = encode_texts([text_content])
            metadata = {
                "source": "notices",
                "title": notice.title,
                "topics": notice.board,
                "published_at": notice.published_date,
                "category": notice.category
            }
            metadata = {k: (v if v is not None else "") for k, v in metadata.items()}
            
            upsert_items(
                name=target_collection,
                ids=[doc_id],
                documents=[text_content],
                metadatas=[metadata],
                embeddings=embedding
            )
            logging.info(f"✅ [Admin] Upserted to ChromaDB (Notice)")

            # 3.5. Append to CSV (Persistent Storage)
            try:
                artifacts = DATASET_ARTIFACTS["notices"]
                csv_path = artifacts.csv_path
                
                # notices.csv schema: chunk_id,doc_id,chunk_text,position,token_len,title,topics,published_at,url,attachments,source,notice_id,rule_id,schedule_id,course_id,staff_id
                new_row = {
                    "chunk_id": doc_id,
                    "doc_id": doc_id,
                    "chunk_text": text_content,
                    "position": 0,
                    "token_len": len(text_content), 
                    "title": notice.title,
                    "topics": notice.board,
                    "published_at": notice.published_date,
                    "url": "",
                    "attachments": "[]",
                    "source": "notices",
                    "notice_id": notice.id,
                    "rule_id": "",
                    "schedule_id": "",
                    "course_id": "",
                    "staff_id": ""
                }
                
                if csv_path.exists():
                    new_df = pd.DataFrame([new_row])
                    new_df.to_csv(csv_path, mode='a', header=False, index=False, encoding='utf-8-sig')
                    logging.info(f"✅ [Admin] Appended to notices.csv")
                else:
                    logging.warning(f"⚠️ [Admin] notices.csv not found. Skipping CSV append.")

            except Exception as e:
                logging.error(f"❌ [Admin] Failed to append to CSV: {e}")

            # 4. Trigger reload
            try:
                if "notices" in _datasets:
                    del _datasets["notices"]
                _ensure_dataset("notices")
                logging.info(f"✅ [Admin] Reloaded notices dataset.")
            except Exception as e:
                logging.error(f"❌ [Admin] Failed to reload notices: {e}")

            item.status = "approved"
            session.commit()
            return {"status": "approved", "chunk_id": doc_id}

        else:
             item.status = "approved_manually" 
             session.commit()
             return {"status": "approved_manually"}

    except Exception as e:
        session.rollback()
        logging.error(f"🔥 [Admin] Critical Error in approve_pending: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
    finally:
        session.close()


@app.post("/admin/reject/{item_id}")
async def reject_pending(item_id: int):
    session = SessionLocal()
    try:
        item = session.query(PendingItem).filter(PendingItem.id == item_id).first()
        if item:
            item.status = "rejected"
            session.commit()
        return {"status": "rejected"}
    finally:
        session.close()


@app.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest) -> AskResponse:
    raw_query = req.question.strip()
    if not raw_query:
        raise HTTPException(status_code=400, detail="질문이 비어 있습니다.")

    # 쿼리 확장 로직 적용
    expanded_query = expand_query(raw_query)
    query_variants = await run_in_threadpool(generate_query_variants, expanded_query)
    if expanded_query not in query_variants:
        query_variants.insert(0, expanded_query)
    query = query_variants[0]
    logging.info(f"Original query: '{raw_query}', Expanded query: '{expanded_query}', Variants: {query_variants}")

    session_id = req.session_id or str(uuid.uuid4())

    # 로그에 처리된 질문과 세션 ID를 출력하여 디버깅을 돕습니다.
    logging.info(f"session: '{session_id}'")

    # LLM에게 현재 날짜를 전달하여 "오늘", "이번 학기" 등의 표현을 해석하도록 돕습니다.
    from datetime import timedelta, timezone
    KST = timezone(timedelta(hours=9))
    current_date = datetime.now(KST).strftime('%Y년 %m월 %d일 %H시 %M분 (KST)')

    course_followup = _answer_course_followup(raw_query, session_id) if DETERMINISTIC_ANSWERS_ENABLED else None
    if course_followup is not None:
        answer, followup_sources = course_followup
        citations = "\n".join(
            (
                f"- {(hit.get('metadata') or {}).get('title') or '교과목'} — {_public_source_url(hit.get('metadata') or {})}"
                if _public_source_url(hit.get("metadata") or {})
                else f"- {(hit.get('metadata') or {}).get('title') or '교과목'}"
            )
            for hit in followup_sources
        )
        sources = [
            SourceChunk(
                source=(hit.get("metadata") or {}).get("source", ""),
                metadata=_metadata_for_response(hit.get("metadata") or {}),
                snippet=hit.get("content", ""),
            )
            for hit in followup_sources
        ]
        return AskResponse(answer=answer, citations=citations, route=["course"], sources=sources)

    if not _is_school_info_query(raw_query):
        logging.info("General chat query detected. Skipping RAG retrieval.")
        try:
            answer = await generate_general_chat_answer(
                question=raw_query,
                session_id=session_id,
                current_date=current_date,
            )
        except Exception:  # noqa: BLE001
            logging.exception("General chat generation failed.")
            answer = "안녕하세요, 저는 동국대학교 공개 정보를 안내하는 AI 어시스턴트 동똑이입니다. 공지, 학사, 장학, 수강신청처럼 궁금한 학교 정보를 물어봐 주세요."
        return AskResponse(answer=answer.replace("**", ""), citations="", route=["general"], sources=[])

    user_major = req.major
    
    # --- 날짜 및 학과 필터링 로직 ---
    final_where_filter: Dict = {}
    date_range_filter = await _date_range_filter_from_query(query)
    
    # 1. 학과 필터링 (ChromaDB where 절 사용)
    if user_major and user_major != "Default": 
        final_where_filter["major"] = {"$eq": user_major}

    # 로깅 추가 (디버깅 용이)
    logging.info(f"Applying ChromaDB filters: {final_where_filter}")
    
    latest_notice_query = _is_latest_notice_query(raw_query)
    date_scoped_notice_query = _is_date_scoped_notice_query(raw_query, date_range_filter)
    exchange_scholarship_query = _is_exchange_scholarship_query(raw_query)
    query_intent = _build_search_intent(query)
    food_sub_category = _food_subcategory(query_intent) if query_intent.name == "food" else None
    deterministic_query = DETERMINISTIC_ANSWERS_ENABLED and (
        latest_notice_query or date_scoped_notice_query or exchange_scholarship_query or query_intent.name == "food"
    )
    if deterministic_query:
        route = [query_intent.name if query_intent.name == "food" else "notices"]
    elif query_intent.name != "general":
        route = [query_intent.name]
    else:
        route = await route_query(query)

    async def run_retrieval(search_query: str) -> list[dict]:
        if latest_notice_query or date_scoped_notice_query:
            hits = await run_in_threadpool(_latest_notice_hits, ASK_TOP_K, date_range_filter)
            logging.info("Latest notice retrieval hits: %s", len(hits))
            return hits

        if DETERMINISTIC_ANSWERS_ENABLED and query_intent.name == "food":
            hits = await run_in_threadpool(
                _food_document_hits,
                ASK_TOP_K,
                food_sub_category,
                food_sub_category is None,
            )
            logging.info("Food document retrieval hits: %s", len(hits))
            return hits

        # /search, /answer/context와 동일한 신규 ingestion 컬렉션을 사용한다.
        hits = await run_in_threadpool(
            _document_retriever.search,
            search_query,
            ASK_TOP_K,
            None,
            date_range_filter,
            query_intent,
        )
        logging.info("Unified document retrieval hits: %s", len(hits))
        return hits

    merged = await run_retrieval(query)
    if CORRECTIVE_RETRY_ENABLED and _should_retry_retrieval(merged):
        retry_query = next((variant for variant in query_variants[1:] if variant != query), None)
        if retry_query:
            logging.info("Running corrective retrieval retry with variant query: %s", retry_query)
            retry_merged = await run_retrieval(retry_query)
            if retry_merged:
                query = retry_query
                merged = retry_merged

    if not merged:
        logging.info("No search results found. Blocking LLM answer generation without context.")

    context_parts = []
    for idx, hit in enumerate(merged):
        metadata = hit.get("metadata") or {}
        part = f"문서 {idx+1} [출처: {metadata.get('source', '알 수 없음')}]:\n"
        if metadata.get("title"):
            part += f"제목: {metadata.get('title')}\n"
        if metadata.get("published_at"):
            part += f"게시일: {metadata.get('published_at')}\n"
        source_url = metadata.get("source_url") or metadata.get("url")
        if source_url:
            part += f"URL: {source_url}\n"
        part += f"내용:\n{hit.get('content', '')}\n"
        
        # --- NEW ATTACHMENT PROCESSING ---
        attachments_str = metadata.get('attachments')
        if attachments_str:
            try:
                # attachments_str이 비어있지 않은 경우에만 json.loads 시도
                if attachments_str.strip(): # 비어있는 문자열 체크
                    attachments = json.loads(attachments_str)
                else:
                    attachments = [] # 비어있는 경우 빈 리스트로 처리

                if isinstance(attachments, list):
                    pdf_links = []
                    for att in attachments:
                        if isinstance(att, dict) and 'name' in att and 'url' in att:
                            file_name = att['name']
                            file_url = att['url']
                            # Check if it's a PDF or a file link
                            # For now, include all attachments as clickable links, not just PDFs
                            pdf_links.append(f"- [{file_name}]({file_url})")
                    if pdf_links:
                        part += "\n첨부파일:\n" + "\n".join(pdf_links) + "\n"
            except json.JSONDecodeError:
                logging.warning(f"Failed to decode attachments JSON: {attachments_str}")
        # --- END NEW ATTACHMENT PROCESSING ---

        context_parts.append(part)
    
    context_text = "\n\n---\n\n".join(context_parts)
    context_text = context_text[:ASK_MAX_CONTEXT_LENGTH]
    if not merged:
        answer = _no_results_answer(query_intent)
    elif DETERMINISTIC_ANSWERS_ENABLED and (latest_notice_query or date_scoped_notice_query):
        answer = _answer_latest_notices(merged)
    elif DETERMINISTIC_ANSWERS_ENABLED and exchange_scholarship_query:
        answer = _answer_exchange_scholarships(merged)
    elif DETERMINISTIC_ANSWERS_ENABLED and query_intent.name == "food":
        answer = _answer_food_menu(merged, food_sub_category)
    elif DETERMINISTIC_ANSWERS_ENABLED and query_intent.name == "department":
        answer = _answer_department_contacts(merged)
    elif DETERMINISTIC_ANSWERS_ENABLED and query_intent.name == "calendar":
        answer = _answer_calendar_events(merged)
    elif DETERMINISTIC_ANSWERS_ENABLED and query_intent.name == "library":
        answer = _answer_library_info(merged)
    elif DETERMINISTIC_ANSWERS_ENABLED and query_intent.name == "course":
        _SESSION_COURSE_HITS[session_id] = _course_hits_for_listing(merged)[:8]
        answer = _answer_courses(merged)
    else:
        try:
            answer = await generate_langchain_answer(
                question=query,
                context=context_text,
                session_id=session_id,
                current_date=current_date,
            )
        except Exception:  # noqa: BLE001
            logging.exception("LLM answer generation failed; returning retrieval fallback.")
            answer = _fallback_answer_from_hits(query, merged)
    
    # 후처리: 볼드체(**) 서식 강제 제거
    answer = answer.replace("**", "")
    
    citation_lines = []
    for hit in merged:
        metadata = hit.get("metadata") or {}
        title = metadata.get("title") or "제목 없음"
        published_at = metadata.get("published_at")
        source_url = _public_source_url(metadata)
        if source_url and published_at:
            citation_lines.append(f"- {title} ({published_at}) — {source_url}")
        elif source_url:
            citation_lines.append(f"- {title} — {source_url}")
        else:
            citation_lines.append(f"- {title}")
    citations = "\n".join(citation_lines)

    sources = [
        SourceChunk(
            source=(hit.get("metadata") or {}).get("source", ""),
            metadata=_metadata_for_response(hit.get("metadata") or {}),
            snippet=hit.get("content", ""),
        )
        for hit in merged
    ]

    return AskResponse(answer=answer, citations=citations, route=route, sources=sources)


@app.get("/health")
def health() -> dict:
    status = {}
    for key in _DATASET_LOADERS:
        cache = _datasets.get(key)
        status[key] = 0 if cache is None else len(cache.chunks)
    latest_ingestion = _ingestion_pipeline.latest_status(limit=1)
    return {"status": "ok", "datasets": status, "ingestion": latest_ingestion}


@app.post("/ingest/run")
async def run_ingestion(req: IngestRunRequest) -> dict:
    results = await run_in_threadpool(_ingestion_pipeline.run, req.source_name, req.limit, req.force, req.clean)
    return {"status": "ok", "results": results}


@app.get("/ingest/status")
async def ingest_status(limit: int = 20) -> dict:
    items = await run_in_threadpool(_ingestion_pipeline.latest_status, limit)
    return {"status": "ok", "items": items}


@app.post("/search")
async def search_documents(req: SearchRequest) -> dict:
    date_range = await _date_range_filter_from_query(req.query, req.date_range)
    if (_is_latest_notice_query(req.query) or _is_date_scoped_notice_query(req.query, date_range)) and not req.category:
        hits = await run_in_threadpool(_latest_notice_hits, req.top_k, date_range)
        return {"query": req.query, "count": len(hits), "hits": hits}

    filters = {}
    if req.category:
        filters["category"] = req.category
    if req.campus:
        filters["campus"] = req.campus
    intent = _build_search_intent(req.query, explicit_category=req.category)
    if DETERMINISTIC_ANSWERS_ENABLED and intent.name == "food" and not req.category:
        food_sub_category = _food_subcategory(intent)
        hits = await run_in_threadpool(
            _food_document_hits,
            req.top_k,
            food_sub_category,
            food_sub_category is None,
        )
        return {"query": req.query, "count": len(hits), "hits": hits}
    where = filters or None
    hits = await run_in_threadpool(
        _document_retriever.search,
        req.query,
        req.top_k,
        where,
        date_range,
        intent,
    )
    return {"query": req.query, "count": len(hits), "hits": hits}


@app.post("/answer/context")
async def answer_context(req: ContextRequest) -> dict:
    date_range = await _date_range_filter_from_query(req.query, req.date_range)
    if (_is_latest_notice_query(req.query) or _is_date_scoped_notice_query(req.query, date_range)) and not req.category:
        hits = await run_in_threadpool(_latest_notice_hits, req.top_k, date_range)
        context = "\n\n---\n\n".join(
            f"[{hit['metadata'].get('title', '제목 없음')}]\nURL: {hit['metadata'].get('source_url', hit['metadata'].get('url', ''))}\n{hit['content']}"
            for hit in hits
        )
        return {"query": req.query, "count": len(hits), "context": context, "hits": hits}

    filters = {}
    if req.category:
        filters["category"] = req.category
    if req.campus:
        filters["campus"] = req.campus
    intent = _build_search_intent(req.query, explicit_category=req.category)
    if DETERMINISTIC_ANSWERS_ENABLED and intent.name == "food" and not req.category:
        food_sub_category = _food_subcategory(intent)
        hits = await run_in_threadpool(
            _food_document_hits,
            req.top_k,
            food_sub_category,
            food_sub_category is None,
        )
        context = "\n\n---\n\n".join(
            f"[{hit['metadata'].get('title', '제목 없음')}]\nURL: {hit['metadata'].get('source_url', hit['metadata'].get('url', ''))}\n{hit['content']}"
            for hit in hits
        )
        return {"query": req.query, "count": len(hits), "context": context, "hits": hits}
    hits = await run_in_threadpool(
        _document_retriever.search,
        req.query,
        req.top_k,
        filters or None,
        date_range,
        intent,
    )
    context = "\n\n---\n\n".join(
        f"[{hit['metadata'].get('title', '제목 없음')}]\nURL: {hit['metadata'].get('source_url', hit['metadata'].get('url', ''))}\n{hit['content']}"
        for hit in hits
    )
    return {"query": req.query, "count": len(hits), "context": context, "hits": hits}
