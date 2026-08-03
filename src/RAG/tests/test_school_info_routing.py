from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api import rag_service  # noqa: E402
from src.services.router import keyword_route  # noqa: E402


@pytest.mark.parametrize(
    "question",
    [
        "교내에서 지갑을 잃어버렸는데 어디에 신고해?",
        "학생증으로 이용할 수 있는 열람실 위치를 알려줘",
        "오늘 밤 10시 이후 이용 가능한 공부 공간이 있어?",
        "법학관 프린터 위치와 결제 방법을 알려줘",
        "셔틀버스 막차 몇 시야?",
        "WISE 도서관 운영시간을 알려줘",
        "교내 편의점 중 밤늦게까지 여는 곳을 알려줘",
        "학생식당 식권 결제 수단을 확인해 줘",
    ],
)
def test_campus_service_question_is_retrieved_from_notices(question):
    assert rag_service._has_school_info_terms(question) is True
    assert "notices" in keyword_route(question)


def test_support_contact_question_also_routes_to_staff():
    routes = keyword_route("교내 와이파이 연결 방법과 장애 신고처가 궁금해")

    assert "notices" in routes
    assert "staff" in routes


def test_unrelated_smalltalk_is_not_forced_into_school_retrieval():
    assert rag_service._has_school_info_terms("오늘 날씨가 어때?") is False
