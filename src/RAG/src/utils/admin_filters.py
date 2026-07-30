"""관리자 콘솔의 조회 필터 파싱과 검수 기록 정규화.

rag_service 본체는 임베딩·벡터스토어 등 무거운 의존성을 함께 끌어오므로,
서비스 기동 없이 검증할 수 있는 순수 로직은 여기로 분리한다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

KST = timezone(timedelta(hours=9))


class _ReviewTarget(Protocol):
    """검수 기록을 받을 대상(PendingItem)이 갖춰야 할 필드."""
    review_note: Any
    reviewed_by: Any
    reviewed_at: Any


class AdminFilterError(ValueError):
    """조회 필터 값이 잘못되었을 때. 호출자가 400으로 변환한다."""

    def __init__(self, field: str, value: str) -> None:
        super().__init__(f"{field} 형식이 올바르지 않습니다: {value}")
        self.field = field
        self.value = value


def parse_admin_datetime(value: str | None, field: str) -> datetime | None:
    """관리자 필터의 ISO 날짜/일시 문자열을 파싱한다.

    프런트가 `toISOString()`으로 보내는 'Z' 접미사를 `fromisoformat`이 3.10 이하에서
    받지 못하므로 명시적으로 치환한다. 형식이 틀리면 조용히 무시하지 않고 예외로 알린다
    — 무시하면 사용자는 필터가 걸린 줄 알고 잘못된 결과를 신뢰하게 된다.
    """
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise AdminFilterError(field, value) from exc


def normalize_review_note(note: str | None) -> str | None:
    """공백만 있는 사유는 저장하지 않는다.

    그대로 두면 학과 화면에 '반려 사유: ' 뒤가 비어 보여, 사유를 적은 것과 구분되지 않는다.
    """
    if not note:
        return None
    trimmed = note.strip()
    return trimmed or None


def apply_review_record(
    item: _ReviewTarget,
    note: str | None,
    actor: str | None,
    now: datetime | None = None,
) -> None:
    """검수 처리 기록(사유·처리자·시각)을 항목에 남긴다.

    누가 언제 왜 처리했는지가 없으면 반려 사유를 제출자에게 돌려줄 수도,
    나중에 처리 이력을 감사할 수도 없다.
    """
    item.review_note = normalize_review_note(note)
    item.reviewed_by = normalize_review_note(actor)
    item.reviewed_at = now or datetime.now(KST)


__all__ = [
    "AdminFilterError",
    "apply_review_record",
    "normalize_review_note",
    "parse_admin_datetime",
]
