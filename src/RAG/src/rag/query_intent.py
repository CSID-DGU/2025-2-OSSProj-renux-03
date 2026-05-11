from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


IntentName = Literal[
    "general",
    "food",
    "library",
    "department",
    "calendar",
    "course_registration",
    "scholarship",
    "international",
    "course",
    "notice",
    "rule",
]


@dataclass(frozen=True)
class QueryIntent:
    name: IntentName
    search_query: str
    hard_filters: dict[str, str] = field(default_factory=dict)
    preferred_document_types: tuple[str, ...] = ()
    preferred_categories: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    score_profile: str = "default"
    block_fallback: bool = False


SYNONYM_EXPANSIONS = {
    "학식": "학식 식단 식단표 학생식당 메뉴",
    "밥": "학식 식단 식단표 학생식당 메뉴",
    "수강정정": "수강신청 정정 확인 정정기간",
    "수강 정정": "수강신청 정정 확인 정정기간",
    "교무처": "교무처 교무학생처 학사지원팀",
    "학사팀": "학사팀 학사지원팀 교무학생처",
    "중도": "중앙도서관 도서관",
    "열람실": "열람실 도서관 이용시간",
    "통계학과": "통계학과 통계 수리통계 데이터분석 전공과목 교과목 교육과정",
    "통계학": "통계학 통계 수리통계 데이터분석 전공과목 교과목 교육과정",
}


def classify_query_intent(query: str, explicit_category: str | None = None) -> QueryIntent:
    text = query.strip()
    normalized = text.lower().replace(" ", "")
    search_query = _expand_query(text)
    latest_notice_terms = ("최근", "최신", "새공지", "새로운공지", "올라온공지", "오늘공지")

    if _contains(normalized, ("학식", "식단", "식단표", "학생식당", "메뉴", "밥")):
        hard_filters = {"document_type": "food"}
        preferred_categories = ("식단표",)
        keywords = ["학식", "식단", "식단표", "메뉴"]
        if _contains(normalized, ("상록원",)):
            hard_filters["sub_category"] = "상록원"
            keywords.append("상록원")
        elif _contains(normalized, ("남산학사", "기숙사")):
            hard_filters["sub_category"] = "남산학사"
            keywords.append("남산학사")
        elif _contains(normalized, ("d-flex", "dflex", "디플렉스", "경영관")):
            hard_filters["sub_category"] = "D-Flex"
            keywords.extend(["D-Flex", "경영관"])
        return QueryIntent(
            name="food",
            search_query=search_query,
            hard_filters=hard_filters,
            preferred_document_types=("food",),
            preferred_categories=preferred_categories,
            keywords=tuple(keywords),
            score_profile="exact_type",
            block_fallback=True,
        )

    if _contains(normalized, ("도서관", "중앙도서관", "열람실", "이용시간", "개관시간")):
        return QueryIntent(
            name="library",
            search_query=search_query,
            hard_filters={"document_type": "library"},
            preferred_document_types=("library",),
            preferred_categories=("도서관",),
            keywords=("도서관", "중앙도서관", "열람실", "이용시간"),
            score_profile="exact_type",
            block_fallback=True,
        )

    if _contains(normalized, ("전화번호", "연락처", "담당자", "사무실", "문의처", "부서")):
        return QueryIntent(
            name="department",
            search_query=search_query,
            hard_filters={"document_type": "department"},
            preferred_document_types=("department",),
            preferred_categories=("부서/학과 전화번호",),
            keywords=("전화번호", "연락처", "담당자", "사무실", "문의처"),
            score_profile="exact_type",
            block_fallback=True,
        )

    if _contains(normalized, ("학사일정", "개강", "종강", "방학", "시험기간")):
        return QueryIntent(
            name="calendar",
            search_query=search_query,
            preferred_document_types=("calendar",),
            preferred_categories=("학사일정",),
            keywords=("학사일정", "개강", "종강", "방학", "시험"),
            score_profile="schedule",
        )

    if _contains(normalized, ("수강신청", "수강정정", "정정기간", "계절학기", "폐강", "수강취소")):
        policy_keywords = ("기준", "방법", "학점", "취소", "유의사항", "확인", "쇼핑카트")
        if _contains(normalized, policy_keywords):
            return QueryIntent(
                name="course_registration",
                search_query=search_query,
                preferred_document_types=("academic", "notice", "calendar"),
                preferred_categories=("학사제도", "학사공지", "학사일정"),
                keywords=("수강신청", "수강", "정정", "취소", "기준", "학점"),
                score_profile="academic_policy",
            )
        return QueryIntent(
            name="course_registration",
            search_query=search_query,
            preferred_document_types=("notice", "calendar", "academic"),
            preferred_categories=("학사공지", "학사일정", "학사제도"),
            keywords=("수강신청", "수강", "정정", "계절학기", "폐강"),
            score_profile="academic_notice",
        )

    if _contains(normalized, ("전공과목", "교과목", "교육과정", "개설과목")) or (
        "과목" in normalized and _contains(normalized, ("통계", "학과", "전공"))
    ):
        return QueryIntent(
            name="course",
            search_query=search_query,
            hard_filters={"document_type": "course"},
            preferred_document_types=("course",),
            preferred_categories=("교육과정",),
            keywords=("전공과목", "교과목", "교육과정", "학수번호", "학점", "개설학기"),
            score_profile="exact_type",
            block_fallback=True,
        )

    if _contains(normalized, ("교환학생", "국제교류", "파견", "유학생")):
        return QueryIntent(
            name="international",
            search_query=search_query,
            preferred_document_types=("notice", "international", "scholarship"),
            preferred_categories=("국제교류공지", "국제공지", "유학생공지", "장학공지", "장학제도"),
            keywords=("교환학생", "국제교류", "파견", "유학생", "장학"),
            score_profile="notice_recency",
        )

    if _contains(normalized, ("장학", "장학금", "등록금", "국가장학")):
        return QueryIntent(
            name="scholarship",
            search_query=search_query,
            preferred_document_types=("notice", "scholarship", "rule"),
            preferred_categories=("장학공지", "장학제도"),
            keywords=("장학", "장학금", "등록금", "선발", "신청"),
            score_profile="notice_recency",
        )

    if _contains(normalized, ("학칙", "규정", "졸업요건", "졸업", "휴학", "복학", "징계")):
        return QueryIntent(
            name="rule",
            search_query=search_query,
            preferred_document_types=("rule", "academic"),
            preferred_categories=("학칙", "학칙/규정", "학사제도"),
            keywords=("학칙", "규정", "졸업", "휴학", "복학"),
            score_profile="default",
        )

    if explicit_category or "공지" in normalized:
        score_profile = "latest_notice" if _contains(normalized, latest_notice_terms) else "notice_recency"
        return QueryIntent(
            name="notice",
            search_query=search_query,
            preferred_document_types=("notice",),
            keywords=("공지",),
            score_profile=score_profile,
        )

    return QueryIntent(name="general", search_query=search_query)


def _contains(text: str, terms: tuple[str, ...]) -> bool:
    return any(term.replace(" ", "") in text for term in terms)


def _expand_query(query: str) -> str:
    expanded = query
    for source, replacement in SYNONYM_EXPANSIONS.items():
        if source in expanded:
            expanded = f"{expanded} {replacement}"
    return expanded
