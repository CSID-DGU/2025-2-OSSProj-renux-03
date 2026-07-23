"""Date-bound academic-schedule queries must filter before semantic top-k."""
from __future__ import annotations

import asyncio
from datetime import date
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import api.rag_service as rag_service  # noqa: E402
from api.rag_service import (  # noqa: E402
    _build_balanced_shortlist,
    _retrieve_frames,
    _schedule_date_hits,
    _select_answer_evidence,
)
from src.utils.date_parser import QueryDateFilter  # noqa: E402


def _schedule_chunks() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "chunk_id": "may",
                "title": "5월 일정",
                "chunk_text": "5월 학사일정",
                "schedule_start": "2026-05-19",
                "schedule_end": "2026-05-19",
            },
            {
                "chunk_id": "july",
                "title": "7월 휴학 신청",
                "chunk_text": "7월 학사일정",
                "schedule_start": "2026-07-13",
                "schedule_end": "2026-07-17",
            },
            {
                "chunk_id": "spans-months",
                "title": "월말부터 이어지는 일정",
                "chunk_text": "7월 말 일정",
                "schedule_start": "2026-07-31",
                "schedule_end": "2026-08-05",
            },
        ]
    )


def _july_filter() -> QueryDateFilter:
    return QueryDateFilter(
        start=date(2026, 7, 1),
        end=date(2026, 7, 31),
        label="this_month",
        is_relative=True,
    )


def test_schedule_date_hits_filters_before_ranking_and_keeps_overlaps():
    hits = _schedule_date_hits(
        chunks_df=_schedule_chunks(),
        top_k=10,
        date_filter=_july_filter(),
    )

    assert hits["chunk_id"].tolist() == ["july", "spans-months"]
    assert hits["hybrid_score"].tolist() == [1.0, 1.0]


def test_schedule_date_retrieval_bypasses_hybrid_search(monkeypatch):
    monkeypatch.setattr(
        rag_service,
        "_ensure_dataset",
        lambda dataset: (_schedule_chunks(), object(), object(), None),
    )

    def fail_if_hybrid_search_runs(*args, **kwargs):
        raise AssertionError("날짜 지정 학사일정 조회에서 일반 하이브리드 검색이 실행되면 안 됩니다")

    monkeypatch.setattr(rag_service, "hybrid_search_with_meta", fail_if_hybrid_search_runs)

    frames, eliminated, unavailable = asyncio.run(
        _retrieve_frames(
            route=["schedule"],
            query="이번 달 학사일정 알려줘",
            final_where_filter={},
            notice_board_filter=None,
            date_filter=_july_filter(),
            entry_year=None,
            request_id="test-schedule-date",
        )
    )

    assert eliminated is False
    assert unavailable == []
    assert len(frames) == 1
    assert frames[0]["chunk_id"].tolist() == ["july", "spans-months"]


def test_date_bound_schedule_selection_keeps_all_chronological_rows(monkeypatch):
    shortlist = _schedule_chunks().copy()
    shortlist["dataset"] = "schedule"
    shortlist["candidate_id"] = [f"c{index}" for index in range(1, len(shortlist) + 1)]

    async def fail_if_selector_runs(*args, **kwargs):
        raise AssertionError("날짜 지정 학사일정 목록에 LLM 근거 선택기가 실행되면 안 됩니다")

    monkeypatch.setattr(rag_service, "_select_evidence_for_answer", fail_if_selector_runs)
    selected, selector_fallback = asyncio.run(
        _select_answer_evidence(
            "이번 달 학사일정 알려줘",
            shortlist,
            [],
            recent_notice_query=False,
            date_bound_schedule_query=True,
        )
    )

    assert selector_fallback is False
    assert selected["chunk_id"].tolist() == ["may", "july", "spans-months"]


def test_schedule_shortlist_can_preserve_the_full_date_range():
    schedule = pd.concat([_schedule_chunks()] * 3, ignore_index=True)
    schedule["chunk_id"] = [f"schedule-{index}" for index in range(len(schedule))]
    schedule["dataset"] = "schedule"
    schedule["hybrid_score"] = 1.0

    shortlist = _build_balanced_shortlist([schedule], per_dataset=10, max_candidates=10)

    assert len(shortlist) == 9
