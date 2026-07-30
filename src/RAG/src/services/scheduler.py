"""공지/학식/교과과정 데이터의 주기적 자동 갱신 스케줄러 (rag-service 프로세스 내부).

별도 워커 컨테이너 대신 서빙 프로세스 안에서 APScheduler로 돌린다:
- 이미 로드된 임베딩 모델을 재사용 → 추가 메모리 없음.
- Chroma 클라이언트를 단일 프로세스가 소유 → 멀티프로세스 동시 접근 위험 없음.

작업은 BackgroundScheduler의 워커 스레드에서 실행되어 asyncio 이벤트 루프(서빙)를 막지 않는다.
재진입 방지(max_instances=1)·중복 누적 방지(coalesce=True)를 적용한다.

기본 비활성(RAG_SCHEDULER_ENABLED=0). 배포 환경에서 env로 켠다.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from src.config import (
    RAG_NOTICES_REFRESH_MAX_PAGES,
    RAG_SCHEDULER_ENABLED,
    RAG_SCHEDULER_REQUEST_RETRIES,
    RAG_SCHEDULER_REQUEST_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))

_scheduler = None  # 단일 인스턴스 보관(중복 시작 방지)

# 작업별 마지막 실행 기록. 관리자 화면에서 "돌긴 돌았나"를 로그를 뒤지지 않고 확인하기 위한 것.
# 프로세스 메모리에만 두므로 재시작하면 비어 있고, 그 경우 화면은 '기록 없음'으로 표시한다.
_LAST_RUNS: dict[str, dict[str, str | None]] = {}

JOB_LABELS = {
    "refresh_notices": "공지 수집",
    "refresh_meals": "학식 수집",
    "refresh_courses": "교과과정 수집",
}


def _record_run(job_id: str, status: str, message: str | None = None) -> None:
    _LAST_RUNS[job_id] = {
        "last_run_at": datetime.now(KST).isoformat(),
        "last_status": status,
        "last_message": message,
    }


def get_scheduler_status() -> dict:
    """등록된 자동 작업의 다음 실행 시각과 마지막 결과를 돌려준다."""
    jobs = []
    scheduler = _scheduler
    if scheduler is not None:
        for job in scheduler.get_jobs():
            history = _LAST_RUNS.get(job.id, {})
            next_run = getattr(job, "next_run_time", None)
            jobs.append({
                "id": job.id,
                "name": JOB_LABELS.get(job.id, job.id),
                "next_run_at": next_run.isoformat() if next_run else None,
                "trigger": str(job.trigger),
                "last_run_at": history.get("last_run_at"),
                "last_status": history.get("last_status"),
                "last_message": history.get("last_message"),
            })
    else:
        # 스케줄러가 꺼져 있어도 수동 실행 기록은 보여 준다.
        for job_id, history in _LAST_RUNS.items():
            jobs.append({
                "id": job_id,
                "name": JOB_LABELS.get(job_id, job_id),
                "next_run_at": None,
                "trigger": None,
                "last_run_at": history.get("last_run_at"),
                "last_status": history.get("last_status"),
                "last_message": history.get("last_message"),
            })

    return {"enabled": bool(RAG_SCHEDULER_ENABLED and scheduler is not None), "jobs": jobs}


def _refresh_runtime_dataset_state(dataset: str) -> None:
    """Make scheduler-written artifacts visible to the serving process at once."""
    try:
        from api.rag_service import refresh_runtime_dataset_state

        snapshot = refresh_runtime_dataset_state([dataset])
        logger.info(
            "[scheduler] 런타임 캐시 갱신 dataset=%s chunks=%s dense=%s",
            dataset,
            snapshot.get("counts", {}).get(dataset),
            snapshot.get("dense_counts", {}).get(dataset),
        )
    except Exception as exc:  # noqa: BLE001 - sync itself succeeded; expose reload failure in logs
        logger.error("[scheduler] 런타임 캐시 갱신 실패 dataset=%s: %s", dataset, exc, exc_info=True)


def refresh_notices_job() -> None:
    """공지 게시판 최근 페이지를 크롤링해 증분 동기화 + 인덱스 갱신한다."""
    from src.crawlers.dongguk_notices import crawl_notices
    from src.pipelines.notices_sync import load_known_article_ids_by_board, sync_notices

    start = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    logger.info(
        "[scheduler] 공지 갱신 시작 (%s) request_timeout=%ss request_retries=%s",
        start,
        RAG_SCHEDULER_REQUEST_TIMEOUT_SECONDS,
        RAG_SCHEDULER_REQUEST_RETRIES,
    )
    try:
        try:
            known_ids_by_board = load_known_article_ids_by_board()
        except Exception:  # noqa: BLE001 — 기존 수집 ID 로드는 조기 중단 최적화일 뿐이다.
            known_ids_by_board = None
        df = crawl_notices(
            known_ids_by_board=known_ids_by_board,
            max_pages=RAG_NOTICES_REFRESH_MAX_PAGES,
            delay=0.2,
            request_timeout=RAG_SCHEDULER_REQUEST_TIMEOUT_SECONDS,
            request_retries=RAG_SCHEDULER_REQUEST_RETRIES,
        )
        summary = sync_notices(df, allow_missing_detection=False, mode="full-sync")
        _refresh_runtime_dataset_state("notices")
        logger.info(
            "[scheduler] 공지 갱신 완료 seen=%s new=%s updated=%s deleted=%s failed=%s",
            summary.get("seen"), summary.get("new"), summary.get("updated"),
            summary.get("deleted"), summary.get("failed"),
        )
        _record_run(
            "refresh_notices",
            "ok",
            f"신규 {summary.get('new', 0)} · 수정 {summary.get('updated', 0)} · 실패 {summary.get('failed', 0)}",
        )
    except Exception as exc:  # noqa: BLE001 — 한 번의 실패가 스케줄러를 죽이지 않도록
        logger.error("[scheduler] 공지 갱신 실패: %s", exc, exc_info=True)
        _record_run("refresh_notices", "failed", str(exc))


def refresh_meals_job() -> None:
    """학식 식단을 크롤링해 CSV 저장 후 meals 인덱스를 재구축한다."""
    from src.crawlers.dongguk_meals import crawl_meals
    from src.pipelines.ingest import ingest_meals

    start = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    logger.info(
        "[scheduler] 학식 갱신 시작 (%s) request_timeout=%ss request_retries=%s",
        start,
        RAG_SCHEDULER_REQUEST_TIMEOUT_SECONDS,
        RAG_SCHEDULER_REQUEST_RETRIES,
    )
    try:
        df = crawl_meals(
            days_ahead=13,
            request_timeout=RAG_SCHEDULER_REQUEST_TIMEOUT_SECONDS,
            request_retries=RAG_SCHEDULER_REQUEST_RETRIES,
        )
        if df.empty:
            logger.warning("[scheduler] 학식 수집 0건 — 기존 인덱스 보존(갱신 건너뜀)")
            _record_run("refresh_meals", "skipped", "수집 0건 — 기존 인덱스 보존")
            return
        chunks_df, _, _ = ingest_meals(df)
        _refresh_runtime_dataset_state("meals")
        logger.info("[scheduler] 학식 갱신 완료: %s행 → %s chunks", len(df), len(chunks_df))
        _record_run("refresh_meals", "ok", f"{len(df)}행 → {len(chunks_df)} chunks")
    except Exception as exc:  # noqa: BLE001
        logger.error("[scheduler] 학식 갱신 실패: %s", exc, exc_info=True)
        _record_run("refresh_meals", "failed", str(exc))


def refresh_courses_job() -> None:
    """학과별 교과과정을 다시 수집하고 courses 인덱스를 갱신합니다."""
    from src.crawlers.dongguk_department_curriculum_content import main as crawl_courses
    from src.pipelines.ingest import ingest_courses

    start = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    logger.info("[scheduler] 교과과정 갱신 시작 (%s)", start)
    try:
        crawl_courses()
        chunks_df, _, _ = ingest_courses(refresh_from_csv=True)
        _refresh_runtime_dataset_state("courses")
        logger.info("[scheduler] 교과과정 갱신 완료: %s chunks", len(chunks_df))
        _record_run("refresh_courses", "ok", f"{len(chunks_df)} chunks")
    except Exception as exc:  # noqa: BLE001
        logger.error("[scheduler] 교과과정 갱신 실패: %s", exc, exc_info=True)
        _record_run("refresh_courses", "failed", str(exc))


def start_scheduler():
    """RAG_SCHEDULER_ENABLED=1 이면 백그라운드 스케줄러를 시작한다. 시작된 인스턴스를 반환(없으면 None)."""
    global _scheduler
    if not RAG_SCHEDULER_ENABLED:
        logger.info("[scheduler] 비활성(RAG_SCHEDULER_ENABLED=0) — 데이터 자동 갱신을 건너뜁니다.")
        return None
    if _scheduler is not None:
        return _scheduler

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.warning("[scheduler] APScheduler 미설치 — 자동 갱신을 건너뜁니다(requirements.txt 확인).")
        return None

    scheduler = BackgroundScheduler(timezone="Asia/Seoul")
    # 부팅 시 따라잡기(catch-up) 실행을 막기 위해 misfire 유예를 짧게 둔다.
    # → 컨테이너를 켜도 '정해진 시각'이 아니면 수집하지 않는다.
    job_defaults = dict(max_instances=1, coalesce=True, misfire_grace_time=120)

    # 고정 시각(cron)에만 실행 — docker up 때마다 수집하지 않는다.
    # 기본: 공지 매일 0/6/12/18시, 학식 매일 04:30, 교과과정 매주 일요일 03:00.
    notices_cron = os.getenv("RAG_NOTICES_REFRESH_CRON", "0 0,6,12,18 * * *")
    meals_cron = os.getenv("RAG_MEALS_REFRESH_CRON", "30 4 * * *")
    courses_cron = os.getenv("RAG_COURSES_REFRESH_CRON", "0 3 * * 0")
    scheduler.add_job(
        refresh_notices_job,
        CronTrigger.from_crontab(notices_cron, timezone="Asia/Seoul"),
        id="refresh_notices", **job_defaults,
    )
    scheduler.add_job(
        refresh_meals_job,
        CronTrigger.from_crontab(meals_cron, timezone="Asia/Seoul"),
        id="refresh_meals", **job_defaults,
    )
    scheduler.add_job(
        refresh_courses_job,
        CronTrigger.from_crontab(courses_cron, timezone="Asia/Seoul"),
        id="refresh_courses", **job_defaults,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "[scheduler] 시작됨 — 공지 cron='%s', 학식 cron='%s', 교과과정 cron='%s' (부팅 시 즉시 실행 안 함)",
        notices_cron, meals_cron, courses_cron,
    )
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:  # noqa: BLE001
            pass
        _scheduler = None


__all__ = [
    "start_scheduler",
    "shutdown_scheduler",
    "get_scheduler_status",
    "refresh_notices_job",
    "refresh_meals_job",
    "refresh_courses_job",
]
