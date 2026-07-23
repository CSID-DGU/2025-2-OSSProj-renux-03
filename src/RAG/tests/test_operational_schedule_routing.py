from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import rag_service  # noqa: E402


def test_operational_schedule_question_adds_notices_for_current_details():
    assert rag_service._should_append_notices_for_schedule_query(
        "이번 학기 수강정정 기간과 시간을 알려줘",
        ["schedule"],
    ) is True


def test_generic_academic_calendar_stays_schedule_only():
    assert rag_service._should_append_notices_for_schedule_query(
        "이번 달 학사일정 알려줘",
        ["schedule"],
    ) is False


def test_current_operational_query_exposes_its_notice_title_term():
    assert rag_service._current_operational_notice_terms(
        "이번 학기 수강정정 기간을 날짜별로 알려줘",
        ["schedule", "notices"],
    ) == ["수강정정"]
