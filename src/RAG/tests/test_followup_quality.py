from __future__ import annotations

import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.langchain_chat import validate_followup_questions  # noqa: E402
from api import rag_service  # noqa: E402


SOURCES = [
    {
        "source": "notices",
        "title": "2026학년도 교내장학 신청 안내",
        "snippet": "교내장학 신청 서류와 신청 결과 확인 방법은 학생 포털에서 안내합니다.",
        "metadata": {"campus_scope": "seoul"},
    }
]


def test_followups_remove_repeats_duplicates_wrong_campus_and_inventions():
    candidates = [
        "교내장학 신청 방법이 궁금해요",  # original repeat
        "장학 신청 서류는 무엇인가요?",  # valid
        "장학 신청 서류가 무엇인가요?",  # near duplicate
        "WISE캠퍼스 장학도 신청할 수 있나요?",  # wrong campus
        "와이즈 캠퍼스 장학 신청 서류도 같은가요?",  # wrong campus alias
        "신청 금액은 500만원인가요?",  # invented amount
        "신청 마감은 12월 31일인가요?",  # invented date
        "내일 날씨는 어떤가요?",  # unsupported domain
        "오늘 학식 메뉴는 무엇인가요?",  # meals absent from supported domains
        "신청 결과는 어디에서 확인하나요?",  # valid
    ]
    result = validate_followup_questions(
        candidates,
        question="교내장학 신청 방법이 궁금해요",
        answer="교내장학은 안내된 신청 서류를 준비해 학생 포털에서 신청합니다.",
        source_context=SOURCES,
        campus_scope="seoul_bmc",
        supported_domains=["notices"],
        count=5,
    )

    assert result == [
        "장학 신청 서류는 무엇인가요?",
        "신청 결과는 어디에서 확인하나요?",
    ]
    assert len(result) < 5  # 유효하지 않은 질문으로 정해진 개수를 채우지 않는다.


def test_followups_require_grounded_source_context():
    result = validate_followup_questions(
        ["신청 서류는 무엇인가요?"],
        question="신청 방법은?",
        answer="신청할 수 있습니다.",
        source_context=[],
        campus_scope="seoul_bmc",
        supported_domains=["notices"],
        count=3,
    )
    assert result == []


def test_wise_followup_is_allowed_only_in_wise_scope_when_source_supports_it():
    wise_sources = [{
        "source": "rules",
        "title": "WISE캠퍼스 휴학 규정",
        "snippet": "WISE캠퍼스 휴학 신청 서류를 안내합니다.",
        "metadata": {"campus_scope": "wise"},
    }]
    result = validate_followup_questions(
        ["WISE캠퍼스 휴학 신청 서류는 무엇인가요?"],
        question="WISE캠퍼스 휴학 규정 알려줘",
        answer="WISE캠퍼스 휴학 신청은 규정에 따릅니다.",
        source_context=wise_sources,
        campus_scope="wise",
        supported_domains=["rules"],
        count=3,
    )
    assert result == ["WISE캠퍼스 휴학 신청 서류는 무엇인가요?"]


def test_both_api_paths_generate_followups_only_after_grounding_stage():
    for endpoint in (rag_service.ask, rag_service.ask_stream):
        source = inspect.getsource(endpoint)
        grounding_position = source.index("grounding_result = await check_answer_grounding")
        followup_position = source.index("suggested_questions = await generate_followup_questions")
        assert grounding_position < followup_position


def test_single_generic_token_overlap_does_not_pass_topic_validation():
    result = validate_followup_questions(
        [
            "장학 관련 기숙사 비용도 알려주세요",
            "장학 신청과 기숙사 비용의 관계는 무엇인가요?",
        ],
        question="교내장학 신청 방법은?",
        answer="교내장학 신청 서류를 준비하세요.",
        source_context=SOURCES,
        campus_scope="seoul_bmc",
        supported_domains=["notices"],
        count=3,
    )
    assert result == []


def test_grounding_disabled_or_unchecked_never_allows_followups():
    grounded = SimpleNamespace(checked=True, grounded=True)
    unchecked = SimpleNamespace(checked=False, grounded=True)
    rejected = SimpleNamespace(checked=True, grounded=False)

    assert rag_service._grounding_allows_followups(False, grounded) is False
    assert rag_service._grounding_allows_followups(True, unchecked) is False
    assert rag_service._grounding_allows_followups(True, rejected) is False
    assert rag_service._grounding_allows_followups(True, grounded) is True
