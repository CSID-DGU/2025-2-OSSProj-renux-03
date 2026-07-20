from __future__ import annotations

from datetime import date
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.rag_service import _build_notification_candidates_from_frames  # noqa: E402


def test_notification_candidates_filters_and_deduplicates_notice_deadlines():
    notices = pd.DataFrame(
        [
            {
                "chunk_id": "notice-1-a",
                "doc_id": "notice-1",
                "notice_id": 1,
                "position": 0,
                "title": "교외장학 서류 제출 안내",
                "topics": "장학공지",
                "category": "장학",
                "published_at": "2026-07-01",
                "apply_deadline": "2026-07-10",
                "url": "https://example.com/1",
                "chunk_text": "서류 제출 기한 안내",
            },
            {
                "chunk_id": "notice-1-b",
                "doc_id": "notice-1",
                "notice_id": 1,
                "position": 1,
                "title": "교외장학 서류 제출 안내",
                "topics": "장학공지",
                "category": "장학",
                "published_at": "2026-07-01",
                "apply_deadline": "2026-07-10",
                "url": "https://example.com/1",
                "chunk_text": "두 번째 청크",
            },
            {
                "chunk_id": "notice-old",
                "doc_id": "notice-old",
                "notice_id": 2,
                "position": 0,
                "title": "지난 공지",
                "topics": "일반공지",
                "category": "일반",
                "published_at": "2026-06-01",
                "apply_deadline": "2026-06-30",
                "url": "https://example.com/2",
                "chunk_text": "지난 마감",
            },
        ]
    )

    candidates = _build_notification_candidates_from_frames(
        notices_df=notices,
        schedule_df=pd.DataFrame(),
        start=date(2026, 7, 1),
        end=date(2026, 7, 31),
        sources={"notices"},
        limit=20,
        today=date(2026, 7, 6),
    )

    assert len(candidates) == 1
    assert candidates[0].id == "notices:1"
    assert candidates[0].topic == "scholarship"
    assert candidates[0].target_date == "2026-07-10"
    assert candidates[0].d_day == 4


def test_notification_candidates_uses_schedule_end_as_target_date():
    schedule = pd.DataFrame(
        [
            {
                "chunk_id": "schedule-1",
                "schedule_id": 11,
                "title": "수강신청 정정",
                "schedule_start": "2026-09-01",
                "schedule_end": "2026-09-05",
                "category": "학사",
                "department": "교무팀",
                "chunk_text": "학사일정: 수강신청 정정",
            }
        ]
    )

    candidates = _build_notification_candidates_from_frames(
        notices_df=pd.DataFrame(),
        schedule_df=schedule,
        start=date(2026, 9, 1),
        end=date(2026, 9, 30),
        sources={"schedule"},
        limit=20,
        today=date(2026, 9, 1),
    )

    assert len(candidates) == 1
    assert candidates[0].id == "schedule:11"
    assert candidates[0].topic == "academic_schedule"
    assert candidates[0].target_date == "2026-09-05"
    assert candidates[0].date_source == "schedule_end"
