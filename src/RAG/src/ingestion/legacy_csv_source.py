from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from src.config import BASE_DIR


Loader = Callable[[pd.DataFrame, dict[str, Any]], list[dict[str, Any]]]


def load_legacy_csv_documents(settings: dict[str, Any], limit: int | None = None) -> list[dict[str, Any]]:
    csv_path = _resolve_path(settings["legacy_csv"])
    if not csv_path.exists():
        raise FileNotFoundError(f"Legacy CSV not found: {csv_path}")

    df = pd.read_csv(csv_path)
    kind = settings.get("legacy_kind", "notice")
    loader = LOADERS.get(kind)
    if loader is None:
        raise ValueError(f"Unsupported legacy_kind: {kind}")

    items = loader(df, settings)
    items = [item for item in items if _text(item.get("content"))]

    if settings.get("sort_by"):
        sort_by = settings["sort_by"]
        if sort_by in df.columns:
            items.sort(key=lambda item: item.get("published_at") or item.get("valid_from") or "", reverse=True)
    elif kind == "notice":
        items.sort(key=lambda item: item.get("published_at") or "", reverse=True)

    if limit:
        items = items[:limit]
    return items


def _load_notice(df: pd.DataFrame, settings: dict[str, Any]) -> list[dict[str, Any]]:
    board_name = settings["legacy_board_name"]
    scoped = df[df["게시판"] == board_name].copy()
    items: list[dict[str, Any]] = []
    for _, row in scoped.iterrows():
        attachments = _parse_attachments(row.get("첨부파일"))
        items.append(
            _base_payload(
                settings,
                title=_text(row.get("제목")),
                content=_text(row.get("본문")),
                url=_text(row.get("상세URL")),
                published_at=_format_date(row.get("게시일")),
                updated_at=_format_date(row.get("게시일")),
                sub_category=_text(row.get("카테고리")) or settings.get("sub_category"),
                attachment_urls=[item["url"] for item in attachments if item.get("url")],
            )
        )
    return items


def _load_schedule(df: pd.DataFrame, settings: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for idx, row in df.iterrows():
        start = _format_date(row.get("start"))
        end = _format_date(row.get("end")) or start
        title = _first_text(row, ["2", "내용", "일정", "title"]) or f"학사일정 {idx + 1}"
        department = _text(row.get("주관부서")) or _extract_department(title) or settings.get("department")
        content = _join_lines(
            [
                f"일정명: {title}",
                f"기간: {start or ''} ~ {end or ''}",
                f"주관부서: {department or ''}",
            ]
        )
        items.append(
            _base_payload(
                settings,
                title=title,
                content=content,
                url=_row_url(settings, "schedule", idx),
                published_at=start,
                updated_at=start,
                department=department,
                valid_from=start,
                valid_until=end,
            )
        )
    return items


def _load_rules(df: pd.DataFrame, settings: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for idx, row in df.iterrows():
        filename = _text(row.get("filename")) or f"rule-{idx + 1}"
        relative_dir = _text(row.get("relative_dir"))
        text = _text(row.get("text"))
        category = _rule_category(relative_dir) or settings.get("category")
        content = _join_lines([f"규정명: {filename}", f"분류: {relative_dir or ''}", text])
        items.append(
            _base_payload(
                settings,
                category=category,
                sub_category=relative_dir or settings.get("sub_category"),
                title=filename,
                content=content,
                url=_row_url(settings, "rule", idx, filename),
                published_at=_date_from_text(filename),
                updated_at=_date_from_text(filename),
            )
        )
    return items


def _load_staff(df: pd.DataFrame, settings: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for idx, row in df.iterrows():
        organization = _text(row.get("조직(트리)")) or _text(row.get("Data_0")) or "동국대학교"
        role_or_unit = _text(row.get("Data_0"))
        duty = _text(row.get("Data_1"))
        phone = _text(row.get("Data_2"))
        title = " - ".join(part for part in [organization, duty or role_or_unit] if part)
        content = _join_lines(
            [
                f"조직: {organization}",
                f"부서/직책: {role_or_unit or ''}",
                f"담당업무: {duty or ''}",
                f"전화번호: {phone or ''}",
            ]
        )
        items.append(
            _base_payload(
                settings,
                title=title or f"교직원 연락처 {idx + 1}",
                content=content,
                url=_row_url(settings, "staff", idx, title),
                department=organization,
            )
        )
    return items


def _load_course_descriptions(df: pd.DataFrame, settings: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for idx, row in df.iterrows():
        code = _text(row.get("학수번호"))
        korean_name = _text(row.get("국문교과목명"))
        english_name = _text(row.get("영문명"))
        title = " ".join(part for part in [code, korean_name] if part) or f"교과목 설명 {idx + 1}"
        content = _join_lines(
            [
                f"학수번호: {code or ''}",
                f"교과목명: {korean_name or ''}",
                f"영문명: {english_name or ''}",
                f"해설: {_text(row.get('해설'))}",
            ]
        )
        items.append(
            _base_payload(
                settings,
                title=title,
                content=content,
                url=_row_url(settings, "course-description", idx, code or korean_name),
                sub_category=settings.get("sub_category") or "교과목해설",
            )
        )
    return items


def _load_major_courses(df: pd.DataFrame, settings: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for idx, row in df.iterrows():
        code = _text(row.get("학수번호"))
        course_name = _text(row.get("교과목명"))
        title = " ".join(part for part in [code, course_name] if part) or f"교육과정 {idx + 1}"
        content = _join_lines(
            [
                f"학수번호: {code or ''}",
                f"교과목명: {course_name or ''}",
                f"학점: {_text(row.get('학점'))}",
                f"이론: {_text(row.get('이론'))}",
                f"실습: {_text(row.get('실습'))}",
                f"전공구분: {_text(row.get('전공구분'))}",
                f"이수대상: {_text(row.get('이수대상'))}",
                f"원어강의: {_text(row.get('원어강의'))}",
                f"개설학기: {_text(row.get('개설학기'))}",
                f"비고: {_text(row.get('비고'))}",
            ]
        )
        items.append(
            _base_payload(
                settings,
                title=title,
                content=content,
                url=_row_url(settings, "major-course", idx, code or course_name),
                sub_category=_text(row.get("전공구분")) or settings.get("sub_category"),
            )
        )
    return items


LOADERS: dict[str, Loader] = {
    "notice": _load_notice,
    "schedule": _load_schedule,
    "rules": _load_rules,
    "staff": _load_staff,
    "course_descriptions": _load_course_descriptions,
    "major_courses": _load_major_courses,
}


def _base_payload(
    settings: dict[str, Any],
    *,
    title: str,
    content: str,
    url: str,
    category: str | None = None,
    sub_category: str | None = None,
    published_at: str | None = None,
    updated_at: str | None = None,
    department: str | None = None,
    attachment_urls: list[str] | None = None,
    valid_from: str | None = None,
    valid_until: str | None = None,
) -> dict[str, Any]:
    return {
        "source": settings["source"],
        "category": category or settings["category"],
        "sub_category": sub_category if sub_category is not None else settings.get("sub_category"),
        "title": title,
        "content": content,
        "url": url,
        "published_at": published_at,
        "updated_at": updated_at,
        "department": department if department is not None else settings.get("department"),
        "campus": settings.get("campus"),
        "document_type": settings.get("document_type", "notice"),
        "attachment_urls": attachment_urls or [],
        "valid_from": valid_from,
        "valid_until": valid_until,
    }


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else BASE_DIR / path


def _parse_attachments(value: Any) -> list[dict[str, Any]]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _first_text(row: pd.Series, columns: list[str]) -> str | None:
    for column in columns:
        if column in row:
            value = _text(row.get(column))
            if value:
                return value
    return None


def _text(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    if text.lower() == "nan":
        return ""
    return " ".join(text.split())


def _format_date(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.strftime("%Y-%m-%d")


def _date_from_text(value: str | None) -> str | None:
    if not value:
        return None
    match = pd.Series([value]).str.extract(r"(\d{4})[.\-](\d{1,2})[.\-](\d{1,2})").iloc[0]
    if match.isna().any():
        return None
    year, month, day = match.tolist()
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def _rule_category(relative_dir: str | None) -> str | None:
    if not relative_dir:
        return None
    return relative_dir.split("/")[0].split("_")[-1]


def _extract_department(text: str) -> str | None:
    if "주관부서:" not in text:
        return None
    return text.split("주관부서:", 1)[1].split(")", 1)[0].strip()


def _join_lines(parts: list[str | None]) -> str:
    return "\n".join(part for part in parts if part and part.strip())


def _row_url(settings: dict[str, Any], prefix: str, idx: int, key: str | None = None) -> str:
    base_url = settings.get("base_url") or settings.get("source_url")
    if base_url and str(base_url).startswith("http"):
        return f"{str(base_url).rstrip('#')}#{prefix}-{idx + 1}"
    stable_key = _text(key) or str(idx + 1)
    return f"dongguk://{prefix}/{stable_key}"


__all__ = ["load_legacy_csv_documents"]
