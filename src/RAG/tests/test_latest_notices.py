"""최신 공지는 의미 유사도가 아니라 게시판별 게시일 역순으로 조회한다."""
from __future__ import annotations

import asyncio
from datetime import date
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.rag_service import (  # noqa: E402
    _extract_notice_board_filter,
    _is_recent_notice_query,
    _latest_notice_hits,
    _retrieve_frames,
)
import api.rag_service as rag_service  # noqa: E402
from src.utils.date_parser import QueryDateFilter  # noqa: E402


def _notice_chunks() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "chunk_id": "scholarship-new-0",
                "doc_id": "scholarship-new",
                "notice_id": 30,
                "position": 0,
                "title": "신규 장학 공지",
                "chunk_text": "신규 장학 공지 본문 첫 청크",
                "topics": "장학공지",
                "published_at": "2026-06-20",
                "url": "https://example.com/30",
                "source": "notices",
            },
            {
                "chunk_id": "scholarship-new-1",
                "doc_id": "scholarship-new",
                "notice_id": 30,
                "position": 1,
                "title": "신규 장학 공지",
                "chunk_text": "신규 장학 공지 본문 두 번째 청크",
                "topics": "장학공지",
                "published_at": "2026-06-20",
                "url": "https://example.com/30",
                "source": "notices",
            },
            {
                "chunk_id": "academic-new",
                "doc_id": "academic-new",
                "notice_id": 20,
                "position": 0,
                "title": "더 최신인 학사 공지",
                "chunk_text": "학사 공지 본문",
                "topics": "학사공지",
                "published_at": "2026-06-22",
                "url": "https://example.com/20",
                "source": "notices",
            },
            {
                "chunk_id": "scholarship-old",
                "doc_id": "scholarship-old",
                "notice_id": 10,
                "position": 0,
                "title": "과거 장학 공지",
                "chunk_text": "과거 장학 공지 본문",
                "topics": "장학공지",
                "published_at": "2026-05-01",
                "url": "https://example.com/10",
                "source": "notices",
            },
        ]
    )


def test_latest_notice_hits_filters_board_sorts_by_date_and_deduplicates_chunks():
    hits = _latest_notice_hits(
        chunks_df=_notice_chunks(),
        top_k=5,
        where_filter={"topics": {"$eq": "장학공지"}},
    )

    assert hits["chunk_id"].tolist() == ["scholarship-new-0", "scholarship-old"]
    assert hits["published_at"].tolist() == ["2026-06-20", "2026-05-01"]
    assert hits["hybrid_score"].tolist() == [1.0, 1.0]


def test_recent_date_label_means_latest_available_not_fixed_recent_days():
    recent_filter = QueryDateFilter(
        start=date(2026, 6, 17),
        end=date(2026, 6, 23),
        label="recent",
        is_relative=True,
    )

    hits = _latest_notice_hits(
        chunks_df=_notice_chunks(),
        top_k=5,
        where_filter={"topics": {"$eq": "장학공지"}},
        date_filter=recent_filter,
    )

    assert hits["chunk_id"].tolist() == ["scholarship-new-0", "scholarship-old"]


def test_explicit_date_filter_is_applied_before_latest_sort():
    may_filter = QueryDateFilter(
        start=date(2026, 5, 1),
        end=date(2026, 5, 31),
        label="specific_month",
    )

    hits = _latest_notice_hits(
        chunks_df=_notice_chunks(),
        top_k=5,
        where_filter={"topics": {"$eq": "장학공지"}},
        date_filter=may_filter,
    )

    assert hits["chunk_id"].tolist() == ["scholarship-old"]


def test_recent_notice_intent_and_board_alias_are_detected():
    route = ["notices"]

    assert _is_recent_notice_query("최근 장학 공지 알려줘", route) is True
    assert _extract_notice_board_filter("최근 장학 공지 알려줘", route) == "장학공지"
    assert _extract_notice_board_filter("최신 학사공지 보여줘", route) == "학사공지"


def test_recent_notice_retrieval_bypasses_hybrid_search(monkeypatch):
    monkeypatch.setattr(
        rag_service,
        "_ensure_dataset",
        lambda dataset: (_notice_chunks(), object(), object(), None),
    )

    def fail_if_hybrid_search_runs(*args, **kwargs):
        raise AssertionError("최신 공지 조회에서 하이브리드 검색이 실행되면 안 됩니다")

    monkeypatch.setattr(rag_service, "hybrid_search_with_meta", fail_if_hybrid_search_runs)

    frames, eliminated, unavailable = asyncio.run(
        _retrieve_frames(
            route=["notices"],
            query="최근 장학 공지 알려줘",
            final_where_filter={},
            notice_board_filter="장학공지",
            date_filter=QueryDateFilter(
                start=date(2026, 6, 17),
                end=date(2026, 6, 23),
                label="recent",
                is_relative=True,
            ),
            entry_year=None,
            request_id="test-latest-notices",
            recent_notice_query=True,
        )
    )

    assert eliminated is False
    assert unavailable == []
    assert len(frames) == 1
    assert frames[0]["chunk_id"].tolist() == ["scholarship-new-0", "scholarship-old"]
