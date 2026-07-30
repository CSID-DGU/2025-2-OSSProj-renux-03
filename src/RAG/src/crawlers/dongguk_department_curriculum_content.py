"""동국대학교 학과별 교과과정 소스에서 RAG용 통합 CSV를 생성합니다.

입력:
- dongguk_department_curriculum_sources.csv

출력:
- dongguk_courses_all.csv

전략:
1. 본부 교육과정 안내의 최신 단과대학 PDF 15개를 전체 학과의 기준 자료로 수집한다.
2. PDF의 표 가로선·열 위치를 이용해 서로 다른 표 형식을 공통 스키마로 정규화한다.
3. 학과 홈페이지 표와 교과목 해설은 설명을 보강하는 보조 자료로 수집한다.
4. 일시적인 수집 실패에는 마지막 정상 데이터를 보존하고 학과별 진단 CSV를 함께 생성한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence
import re

import pandas as pd
import requests
from bs4 import BeautifulSoup, FeatureNotFound, Tag

from src.config import DATA_SOURCES
from src.crawlers.dongguk_official_curriculum_pdf import (
    OFFICIAL_CURRICULUM_PAGE,
    OfficialCourseRow,
    OfficialCurriculumPdfSource,
    fetch_official_curriculum_sources,
    fetch_pdf_bytes,
    extract_official_curriculum_pdf,
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DonggukCurriculumContentCrawler/0.1)",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}
PARSER_CANDIDATES = ("lxml", "html5lib", "html.parser")
SOURCE_CSV_PATH = DATA_SOURCES["courses_curriculum_sources"]
OUTPUT_CSV_PATH = DATA_SOURCES["courses_all"]
DIAGNOSIS_OUTPUT_PATH = OUTPUT_CSV_PATH.with_name("dongguk_courses_collection_diagnosis.csv")
CURRICULUM_LINKS_XLSX_GLOB = "dongguk_department_curriculum_links_*.xlsx"
CURATED_SHEET_NAME = "curriculum_links"
CURRICULUM_OVERRIDES_PATH = SOURCE_CSV_PATH.with_name("dongguk_curriculum_source_overrides.csv")
CURRICULUM_PAGE_KEYWORDS = (
    "교과과정",
    "교육과정",
    "전공교육과정",
    "전공과목",
    "개설총괄표",
    "이수체계도",
    "학부과정",
    "교과목 해설",
)
COURSE_HEADER_MAP = {
    "course_code": ("학수번호", "과목코드", "학수", "code", "course code", "subject code"),
    "title": ("교과목명", "과목명", "국문교과목명", "교과목", "교과명", "title", "course"),
    "english_title": ("영문명", "english", "영문교과목명"),
    "credit": ("학점", "credit"),
    "semester": ("개설학기", "학기", "semester"),
    "grade": ("학년", "이수대상", "대상", "수강대상"),
    "course_type": ("전공구분", "이수구분", "구분", "이수", "category", "type"),
    "description": ("해설", "설명", "비고", "교과목 해설", "내용", "description"),
}
IGNORED_TEXT_PATTERNS = (
    "개인정보처리방침",
    "이메일무단수집거부",
    "찾아오시는 길",
    "사이트맵",
)
EXCLUDED_SOURCE_TITLE_TERMS = (
    "대학원",
)
BOILERPLATE_TEXT_SNIPPETS = (
    "등록된 팝업이 없습니다",
    "portal",
    "ndrims",
    "e-class",
    "groupwawre",
    "groupware",
    "webmail",
    "중앙도서관",
    "인쇄 공유 페이스북 공유하기 트위터 공유하기",
)
CONTENT_SIGNAL_TERMS = (
    "학점",
    "학수번호",
    "교과목명",
    "과목명",
    "이수구분",
    "전공구분",
    "이수대상",
    "개설학기",
)
HEADING_RECORD_TAGS = ("h3", "h4")


@dataclass(frozen=True)
class CurriculumSource:
    college_name: str
    department_name: str
    department_key: str
    department_url: str
    curriculum_title: str
    curriculum_url: str
    source_type: str


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def fetch_page_html(url: str, *, timeout: float = 15.0) -> str:
    response = requests.get(url, headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding
    return response.text


def make_soup(markup: str) -> BeautifulSoup:
    last_exc: Exception | None = None
    for parser in PARSER_CANDIDATES:
        try:
            return BeautifulSoup(markup, parser)
        except FeatureNotFound as exc:
            last_exc = exc
            continue
    raise RuntimeError("사용 가능한 HTML 파서를 찾을 수 없습니다.") from last_exc


def find_curated_curriculum_workbook() -> Optional[Path]:
    candidates = sorted(SOURCE_CSV_PATH.parent.glob(CURRICULUM_LINKS_XLSX_GLOB))
    if not candidates:
        return None
    return candidates[-1]


def load_curriculum_sources_from_workbook(path: Path) -> list[CurriculumSource]:
    df = pd.read_excel(path, sheet_name=CURATED_SHEET_NAME).fillna("").astype(str)
    rows: list[CurriculumSource] = []
    seen: set[tuple[str, str, str]] = set()

    for _, row in df.iterrows():
        status = normalize_text(row.get("상태", ""))
        if status not in {"found_department_page", "found_college_page"}:
            continue

        curriculum_url = normalize_text(row.get("교과과정_URL", ""))
        department_name = normalize_text(row.get("학과/전공", ""))
        department_url = normalize_text(row.get("학과홈페이지", ""))
        curriculum_title = normalize_text(row.get("페이지명/메뉴", ""))
        college_name = normalize_text(row.get("단과대학", ""))
        if not curriculum_url or not department_name:
            continue
        if any(term in curriculum_title for term in EXCLUDED_SOURCE_TITLE_TERMS):
            continue

        source_type = "curriculum_page"
        if "총괄표" in curriculum_title or "이수체계" in curriculum_title or "개설교과목" in curriculum_title:
            source_type = "curriculum_table"
        elif "해설" in curriculum_title:
            source_type = "course_description"

        key = (department_name, curriculum_url, source_type)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            CurriculumSource(
                college_name=college_name,
                department_name=department_name,
                department_key=department_name,
                department_url=department_url,
                curriculum_title=curriculum_title,
                curriculum_url=curriculum_url,
                source_type=source_type,
            )
        )

    return rows


def load_curriculum_source_overrides(path: Path) -> list[CurriculumSource]:
    if not path.exists():
        return []
    df = pd.read_csv(path).fillna("").astype(str)
    rows: list[CurriculumSource] = []
    for _, row in df.iterrows():
        department_name = normalize_text(row.get("department_name", ""))
        curriculum_url = normalize_text(row.get("curriculum_url", ""))
        if not department_name or not curriculum_url:
            continue
        rows.append(
            CurriculumSource(
                college_name=normalize_text(row.get("college_name", "")),
                department_name=department_name,
                department_key=normalize_text(row.get("department_key", "")) or department_name,
                department_url=normalize_text(row.get("department_url", "")),
                curriculum_title=normalize_text(row.get("curriculum_title", "")) or "전공과목 개설총괄표",
                curriculum_url=curriculum_url,
                source_type=normalize_text(row.get("source_type", "")) or "curriculum_table",
            )
        )
    return rows


def load_curriculum_sources(path: Path) -> list[CurriculumSource]:
    if not path.exists():
        raise FileNotFoundError(f"Curriculum source CSV not found: {path}")

    df = pd.read_csv(path).fillna("").astype(str)
    rows: list[CurriculumSource] = []
    seen: set[tuple[str, str, str]] = set()
    for _, row in df.iterrows():
        if row.get("status", "").strip() != "found":
            continue
        curriculum_url = row.get("curriculum_url", "").strip()
        department_name = row.get("department_name", "").strip()
        curriculum_title = row.get("curriculum_title", "").strip()
        if not curriculum_url or not department_name:
            continue
        if any(term in curriculum_title for term in EXCLUDED_SOURCE_TITLE_TERMS):
            continue
        key = (department_name, curriculum_url, row.get("source_type", "").strip())
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            CurriculumSource(
                college_name=row.get("college_name", "").strip(),
                department_name=department_name,
                department_key=row.get("department_key", "").strip(),
                department_url=row.get("department_url", "").strip(),
                curriculum_title=curriculum_title,
                curriculum_url=curriculum_url,
                source_type=row.get("source_type", "").strip() or "curriculum_page",
            )
        )
    workbook = find_curated_curriculum_workbook()
    curated_rows = load_curriculum_sources_from_workbook(workbook) if workbook is not None else []
    override_rows = load_curriculum_source_overrides(CURRICULUM_OVERRIDES_PATH)
    overridden_departments = {row.department_name for row in override_rows}
    curated_rows = override_rows + [row for row in curated_rows if row.department_name not in overridden_departments]
    if not curated_rows:
        return rows
    covered_departments = {row.department_name for row in curated_rows}
    supplemented_rows = curated_rows + [row for row in rows if row.department_name not in covered_departments]
    return supplemented_rows


def _positive_span(value: object) -> int:
    try:
        return max(1, int(str(value or "1")))
    except (TypeError, ValueError):
        return 1


def _expand_html_table_rows(table: Tag) -> list[tuple[list[str], bool]]:
    """rowspan/colspan을 실제 셀로 펼칩니다.

    학과 페이지 상당수가 학수번호·학년을 rowspan으로 묶고 복수 헤더를 사용합니다.
    단순히 ``tr`` 안의 셀 개수만 맞추면 이후 열이 왼쪽으로 밀려 학점과 학기가
    서로 바뀌므로, 행 병합 값을 다음 행에도 명시적으로 복제합니다.
    """
    expanded: list[tuple[list[str], bool]] = []
    active_spans: dict[int, tuple[str, int]] = {}

    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if not cells:
            continue
        row: dict[int, str] = {}
        for column, (value, remaining) in list(active_spans.items()):
            row[column] = value
            if remaining <= 1:
                del active_spans[column]
            else:
                active_spans[column] = (value, remaining - 1)

        column = 0
        for cell in cells:
            while column in row:
                column += 1
            text = normalize_text(cell.get_text(" ", strip=True))
            colspan = _positive_span(cell.get("colspan"))
            rowspan = _positive_span(cell.get("rowspan"))
            for offset in range(colspan):
                target = column + offset
                row[target] = text
                if rowspan > 1:
                    active_spans[target] = (text, rowspan - 1)
            column += colspan

        width = max(row, default=-1) + 1
        expanded.append(([row.get(index, "") for index in range(width)], all(cell.name == "th" for cell in cells)))
    return expanded


def _unique_headers(headers: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    unique: list[str] = []
    for index, header in enumerate(headers):
        base = normalize_text(header) or f"col_{index + 1}"
        counts[base] = counts.get(base, 0) + 1
        unique.append(base if counts[base] == 1 else f"{base}__{counts[base]}")
    return unique


def read_table_to_df(table: Tag) -> pd.DataFrame:
    expanded = _expand_html_table_rows(table)
    if not expanded:
        return pd.DataFrame()

    width = max(len(row) for row, _ in expanded)
    padded = [(row + [""] * (width - len(row)), is_header) for row, is_header in expanded]

    header_rows: list[list[str]] = []
    body_rows: list[list[str]] = []
    body_started = False
    for row, is_header in padded:
        if is_header and not body_started:
            header_rows.append(row)
        else:
            body_started = True
            body_rows.append(row)

    if header_rows:
        headers = []
        for column in range(width):
            parts: list[str] = []
            for row in header_rows:
                value = normalize_text(row[column])
                if value and value not in parts:
                    parts.append(value)
            headers.append(" ".join(parts))
    else:
        headers = [f"col_{index + 1}" for index in range(width)]

    return pd.DataFrame(body_rows, columns=_unique_headers(headers))


def table_relevance_score(df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    header_text = " ".join(df.columns.tolist()).lower()
    # 중복 헤더가 있는 표에서도 stack()이 예외를 내지 않도록 셀 배열을 직접 평탄화한다.
    body_text = " ".join(str(value) for value in df.astype(str).head(5).fillna("").to_numpy().ravel()).lower()
    score = 0
    for tokens in COURSE_HEADER_MAP.values():
        for token in tokens:
            token_lower = token.lower()
            if token_lower in header_text:
                score += 3
            elif token_lower in body_text:
                score += 1
    return score


def find_section_title(table: Tag) -> str:
    heading = table.find_previous(["h1", "h2", "h3", "h4", "strong", "dt"])
    if not heading:
        return ""
    return normalize_text(heading.get_text(" ", strip=True))


def canonical_field_name(column: str) -> str:
    normalized = normalize_text(column).lower()
    for canonical, variants in COURSE_HEADER_MAP.items():
        if any(token.lower() in normalized for token in variants):
            return canonical
    return column


def normalize_semester(value: str) -> str:
    normalized = normalize_text(value)
    if normalized in {"1", "2"}:
        return f"{normalized}학기"
    return normalized


def normalize_credit(value: str) -> str:
    normalized = normalize_text(value)
    match = re.search(r"\d+(?:\.\d+)?", normalized)
    return match.group(0) if match else ""


def normalize_grade_list(value: str) -> str:
    normalized = normalize_text(value)
    if "전체" in normalized:
        return "1|2|3|4"
    grades: set[int] = set()
    for start, end in re.findall(r"([1-6])\s*(?:~|-|–|—|∼)\s*([1-6])", normalized):
        low, high = sorted((int(start), int(end)))
        grades.update(range(low, high + 1))
    grades.update(int(item) for item in re.findall(r"(?<!\d)([1-6])\s*(?:학년|년)", normalized))
    grades.update(int(item) for item in re.findall(r"(?<!\d)([1-6])(?!\d)", normalized))
    return "|".join(str(grade) for grade in sorted(grades))


def normalize_semester_list(value: str) -> str:
    normalized = normalize_text(value)
    if any(token in normalized for token in ("매학기", "공통", "1, 2", "1,2", "1/2")):
        return "1|2"
    semesters = set(int(item) for item in re.findall(r"(?<!\d)([12])\s*학기", normalized))
    if not semesters and normalized in {"1", "2"}:
        semesters.add(int(normalized))
    return "|".join(str(semester) for semester in sorted(semesters))


def _coalesce_duplicate_columns(df: pd.DataFrame) -> pd.DataFrame:
    """canonical rename 뒤 같은 의미가 된 열을 첫 비어있지 않은 값으로 합칩니다."""
    if not df.columns.duplicated().any():
        return df
    result: dict[str, pd.Series] = {}
    for column in dict.fromkeys(str(value) for value in df.columns):
        matches = df.loc[:, df.columns == column]
        if isinstance(matches, pd.Series) or matches.shape[1] == 1:
            result[column] = matches if isinstance(matches, pd.Series) else matches.iloc[:, 0]
            continue
        result[column] = matches.apply(
            lambda row: next((normalize_text(value) for value in row if normalize_text(value)), ""),
            axis=1,
        )
    return pd.DataFrame(result)


def _record_quality_score(row: Mapping[str, str]) -> int:
    return sum(
        (
            25 if normalize_text(row.get("title", "")) else 0,
            20 if normalize_text(row.get("course_code", "")) else 0,
            20 if normalize_credit(row.get("credit", "")) else 0,
            15 if normalize_grade_list(row.get("grade", "")) else 0,
            10 if normalize_semester_list(row.get("semester", "")) else 0,
            10 if normalize_text(row.get("course_type", "")) else 0,
        )
    )


def choose_record_title(row: dict[str, str]) -> str:
    for key in ("title", "course_name", "교과목명", "국문교과목명", "과목명"):
        value = normalize_text(row.get(key, ""))
        if value:
            return value
    for value in row.values():
        value_str = normalize_text(value)
        if value_str:
            return value_str[:80]
    return "교과과정 정보"


def build_table_records(df: pd.DataFrame, source: CurriculumSource, section_title: str) -> list[dict[str, str]]:
    if df.empty:
        return []

    renamed = {col: canonical_field_name(col) for col in df.columns}
    working = df.rename(columns=renamed).copy()
    working = _coalesce_duplicate_columns(working)
    working = working.map(normalize_text)  # pandas 3.0: applymap 제거됨 → DataFrame.map
    working = working.loc[:, ~(working.eq("").all())]
    if working.empty:
        return []

    records: list[dict[str, str]] = []
    for _, row in working.iterrows():
        row_dict = {str(col): normalize_text(val) for col, val in row.items()}
        row_values = [value for value in row_dict.values() if value]
        if not row_values:
            continue

        title = choose_record_title(row_dict)
        course_code = row_dict.get("course_code", "")
        description = row_dict.get("description", "")
        raw_text = "\n".join(f"{key}: {value}" for key, value in row_dict.items() if value)
        if not raw_text:
            continue

        record = {
                "college_name": source.college_name,
                "department_name": source.department_name,
                "department_url": source.department_url,
                "curriculum_title": source.curriculum_title,
                "curriculum_url": source.curriculum_url,
                "source_type": source.source_type,
                "section_title": section_title or source.curriculum_title,
                "record_type": "table_row",
                "course_code": course_code,
                "title": title,
                "course_name": title,
                "description": description,
                "credit": row_dict.get("credit", ""),
                "semester": normalize_semester(row_dict.get("semester", "")),
                "grade": row_dict.get("grade", ""),
                "course_type": row_dict.get("course_type", ""),
                "english_title": row_dict.get("english_title", ""),
                "raw_text": raw_text,
                "credit_value": normalize_credit(row_dict.get("credit", "")),
                "recommended_grades": normalize_grade_list(row_dict.get("grade", "")),
                "offered_semesters": normalize_semester_list(row_dict.get("semester", "")),
                "is_required": str(
                    any(
                        token in f"{row_dict.get('course_type', '')} {description} {raw_text}"
                        for token in ("전공필수", "필수이수", "필수 이수")
                    )
                ).lower(),
                "availability_status": "curriculum_only",
                "collection_status": "fresh",
                "collection_error": "",
                "collected_at": utc_now_iso(),
            }
        record["data_quality_score"] = str(_record_quality_score(record))
        records.append(record)
    return records


def build_official_pdf_record(row: OfficialCourseRow) -> dict[str, str]:
    raw_fields = {
        "course_code": row.course_code,
        "title": row.title,
        "credit": row.credit,
        "theory_hours": row.theory_hours,
        "practice_hours": row.practice_hours,
        "course_type": row.course_type,
        "grade": row.grade,
        "original_language": row.original_language,
        "semester": row.semester,
        "remarks": row.remarks,
    }
    raw_text = "\n".join(f"{key}: {value}" for key, value in raw_fields.items() if value)
    record = {
        "college_name": row.college_name,
        "department_name": row.department_name,
        "department_url": OFFICIAL_CURRICULUM_PAGE,
        "curriculum_title": f"{row.curriculum_year}학년도 공식 교과과정",
        "curriculum_url": row.source_url,
        "source_type": "official_curriculum_pdf",
        "section_title": "교과 교육과정",
        "record_type": "table_row",
        "course_code": row.course_code,
        "title": row.title,
        "course_name": row.title,
        "description": row.remarks,
        "credit": row.credit,
        "semester": normalize_semester(row.semester),
        "grade": row.grade,
        "course_type": row.course_type,
        "english_title": "",
        "raw_text": raw_text,
        "credit_value": normalize_credit(row.credit),
        "recommended_grades": normalize_grade_list(row.grade),
        "offered_semesters": normalize_semester_list(row.semester),
        "is_required": str(row.is_required).lower(),
        "availability_status": "curriculum_only",
        "collection_status": "fresh",
        "collection_error": "",
        "collected_at": utc_now_iso(),
        "curriculum_year": str(row.curriculum_year),
        "source_page": str(row.page_number),
        "source_priority": "100",
    }
    record["data_quality_score"] = str(_record_quality_score(record))
    return record


def _pdf_source_as_curriculum_source(source: OfficialCurriculumPdfSource) -> CurriculumSource:
    return CurriculumSource(
        college_name=source.college_name,
        department_name=source.college_name,
        department_key=source.college_name,
        department_url=OFFICIAL_CURRICULUM_PAGE,
        curriculum_title=f"{source.year}학년도 공식 교과과정",
        curriculum_url=source.url,
        source_type="official_curriculum_pdf",
    )


def find_content_root(soup: BeautifulSoup) -> Tag:
    for selector in [
        "#jwxe_main_content",
        ".fr-view",
        ".cont",
        ".content",
        "#content",
        "main",
    ]:
        node = soup.select_one(selector)
        if isinstance(node, Tag):
            return node
    if isinstance(soup.body, Tag):
        return soup.body
    return soup


def extract_page_title(soup: BeautifulSoup) -> str:
    if soup.title and soup.title.string:
        return normalize_text(soup.title.string)
    return ""


def is_useful_section_text(title: str, text: str, source: CurriculumSource) -> bool:
    normalized_title = normalize_text(title)
    normalized_text = normalize_text(text)
    if not normalized_text:
        return False

    lowered = normalized_text.lower()
    if any(snippet in lowered for snippet in BOILERPLATE_TEXT_SNIPPETS):
        return False
    if normalized_title.lower() == "popup zone":
        return False
    if normalized_title in {source.department_name, f"동국대학교 {source.department_name}"}:
        return False
    if any(pattern in normalized_text for pattern in IGNORED_TEXT_PATTERNS):
        return False

    has_signal_term = any(term in normalized_text for term in CONTENT_SIGNAL_TERMS)
    has_sentence = any(token in normalized_text for token in ("다.", "된다.", "한다.", ". "))
    if has_signal_term or has_sentence:
        return True

    # 메뉴/네비게이션 텍스트는 길어도 실제 교과 정보가 아니므로 제외한다.
    return False


def build_heading_paragraph_records(soup: BeautifulSoup, source: CurriculumSource) -> list[dict[str, str]]:
    root = find_content_root(soup)
    records: list[dict[str, str]] = []

    for heading in root.find_all(HEADING_RECORD_TAGS, recursive=True):
        title = normalize_text(heading.get_text(" ", strip=True))
        if not title or title in {"popup zone", source.department_name, f"동국대학교 {source.department_name}"}:
            continue

        parts: list[str] = []
        sibling = heading.find_next_sibling()
        while sibling:
            if isinstance(sibling, Tag) and sibling.name in HEADING_RECORD_TAGS:
                break
            if isinstance(sibling, Tag) and sibling.name in {"p", "ul", "ol", "div"}:
                text = normalize_text(sibling.get_text(" ", strip=True))
                if text:
                    parts.append(text)
            sibling = sibling.find_next_sibling()

        description = normalize_text(" ".join(parts))
        if not description:
            continue
        if not is_useful_section_text(title, description, source):
            continue

        records.append(
            {
                "college_name": source.college_name,
                "department_name": source.department_name,
                "department_url": source.department_url,
                "curriculum_title": source.curriculum_title,
                "curriculum_url": source.curriculum_url,
                "source_type": source.source_type,
                "section_title": source.curriculum_title or "교과과정",
                "record_type": "section_text",
                "course_code": "",
                "title": title,
                "course_name": title,
                "description": description,
                "credit": "",
                "semester": "",
                "grade": "",
                "course_type": "",
                "english_title": "",
                "raw_text": description,
                "credit_value": "",
                "recommended_grades": "",
                "offered_semesters": "",
                "is_required": "false",
                "availability_status": "curriculum_only",
                "data_quality_score": "0",
                "collection_status": "fresh",
                "collection_error": "",
                "collected_at": utc_now_iso(),
            }
        )

    return records


def build_section_text_records(soup: BeautifulSoup, source: CurriculumSource) -> list[dict[str, str]]:
    root = find_content_root(soup)
    records: list[dict[str, str]] = []
    current_title = source.curriculum_title or "교과과정"
    current_parts: list[str] = []

    def flush() -> None:
        nonlocal current_parts
        text = normalize_text("\n".join(current_parts))
        if not text:
            current_parts = []
            return
        if not is_useful_section_text(current_title, text, source):
            current_parts = []
            return
        records.append(
            {
                "college_name": source.college_name,
                "department_name": source.department_name,
                "department_url": source.department_url,
                "curriculum_title": source.curriculum_title,
                "curriculum_url": source.curriculum_url,
                "source_type": source.source_type,
                "section_title": current_title,
                "record_type": "section_text",
                "course_code": "",
                "title": current_title or "교과과정 정보",
                "course_name": current_title or "교과과정 정보",
                "description": text,
                "credit": "",
                "semester": "",
                "grade": "",
                "course_type": "",
                "english_title": "",
                "raw_text": text,
                "credit_value": "",
                "recommended_grades": "",
                "offered_semesters": "",
                "is_required": "false",
                "availability_status": "curriculum_only",
                "data_quality_score": "0",
                "collection_status": "fresh",
                "collection_error": "",
                "collected_at": utc_now_iso(),
            }
        )
        current_parts = []

    for node in root.find_all(["h1", "h2", "h3", "h4", "strong", "p", "li"], recursive=True):
        text = normalize_text(node.get_text(" ", strip=True))
        if not text:
            continue
        if node.name in {"h1", "h2", "h3", "h4", "strong"}:
            if current_parts:
                flush()
            current_title = text
            continue
        current_parts.append(text)

    if current_parts:
        flush()
    return records


def parse_curriculum_page(source: CurriculumSource) -> list[dict[str, str]]:
    html = fetch_page_html(source.curriculum_url)
    soup = make_soup(html)
    page_title = extract_page_title(soup)

    # 서울캠 학부 기준 수집이므로 대학원 전용 페이지는 제외한다.
    if "대학원과정" in page_title or "대학원 교과과정" in page_title:
        return []

    records: list[dict[str, str]] = []
    for table in soup.find_all("table"):
        df = read_table_to_df(table)
        if table_relevance_score(df) < 3:
            continue
        section_title = find_section_title(table)
        records.extend(build_table_records(df, source, section_title))

    if records:
        return records
    records = build_heading_paragraph_records(soup, source)
    if records:
        return records
    return build_section_text_records(soup, source)


def dedupe_records(records: Iterable[dict[str, str]]) -> pd.DataFrame:
    df = pd.DataFrame(records)
    if df.empty:
        return df
    dedupe_key = (
        df["department_name"].astype(str)
        + "||"
        + df["curriculum_url"].astype(str)
        + "||"
        + df["record_type"].astype(str)
        + "||"
        + df["course_code"].astype(str)
        + "||"
        + df["title"].astype(str)
        + "||"
        + df["raw_text"].astype(str)
    )
    df = df.loc[~dedupe_key.duplicated()].copy()
    df.sort_values(["college_name", "department_name", "curriculum_url", "record_type", "title"], inplace=True)
    return df


def load_previous_good_records(path: Path) -> dict[str, list[dict[str, str]]]:
    if not path.exists():
        return {}
    previous = pd.read_csv(path).fillna("").astype(str)
    if previous.empty or "curriculum_url" not in previous.columns:
        return {}
    if "record_type" in previous.columns:
        previous = previous[previous["record_type"] != "crawl_error"].copy()
    grouped: dict[str, list[dict[str, str]]] = {}
    for _, row in previous.iterrows():
        url = normalize_text(row.get("curriculum_url", ""))
        if url:
            grouped.setdefault(url, []).append({str(key): normalize_text(value) for key, value in row.to_dict().items()})
    return grouped


def stale_records_for_source(
    source: CurriculumSource,
    previous_records: Mapping[str, list[dict[str, str]]],
    error: str,
) -> list[dict[str, str]]:
    stale: list[dict[str, str]] = []
    for previous in previous_records.get(source.curriculum_url, []):
        record = dict(previous)
        record["collection_status"] = "stale_after_error"
        record["collection_error"] = normalize_text(error)[:500]
        record.setdefault("availability_status", "curriculum_only")
        record.setdefault("credit_value", normalize_credit(record.get("credit", "")))
        record.setdefault("recommended_grades", normalize_grade_list(record.get("grade", "")))
        record.setdefault("offered_semesters", normalize_semester_list(record.get("semester", "")))
        record.setdefault(
            "is_required",
            str(
                any(
                    token in f"{record.get('course_type', '')} {record.get('description', '')} {record.get('raw_text', '')}"
                    for token in ("전공필수", "필수이수", "필수 이수")
                )
            ).lower(),
        )
        record.setdefault("data_quality_score", str(_record_quality_score(record)))
        record.setdefault("collected_at", "")
        stale.append(record)
    return stale


def build_collection_diagnosis(
    sources: Sequence[CurriculumSource],
    combined_df: pd.DataFrame,
) -> pd.DataFrame:
    official_departments: set[str] = set()
    if not combined_df.empty and {"source_type", "department_name"}.issubset(combined_df.columns):
        official_departments = {
            normalize_text(department)
            for department in combined_df.loc[
                combined_df["source_type"].astype(str).eq("official_curriculum_pdf"),
                "department_name",
            ]
            if normalize_text(department) and not normalize_text(department).endswith("공통")
        }
    if official_departments:
        source_departments = sorted(official_departments)
    else:
        collected_departments = (
            set(combined_df["department_name"].astype(str).map(normalize_text))
            if not combined_df.empty and "department_name" in combined_df.columns
            else set()
        )
        source_departments = sorted(
            {source.department_name for source in sources}
            | {department for department in collected_departments if department}
        )

    rows: list[dict[str, object]] = []
    for department in source_departments:
        subset = (
            combined_df[combined_df["department_name"].astype(str) == department]
            if not combined_df.empty and "department_name" in combined_df.columns
            else pd.DataFrame()
        )
        official_subset = (
            subset[subset["source_type"].astype(str).eq("official_curriculum_pdf")]
            if not subset.empty and "source_type" in subset.columns
            else pd.DataFrame()
        )
        if not official_subset.empty:
            subset = official_subset
        table_rows = subset[subset.get("record_type", "").astype(str) == "table_row"] if not subset.empty else subset
        if not table_rows.empty:
            course_key = table_rows.get("course_code", "").astype(str).str.strip()
            title_key = table_rows.get("course_name", "").astype(str).str.strip()
            course_key = course_key.where(course_key.eq(""), course_key + "||" + title_key)
            course_key = course_key.where(course_key.ne(""), title_key)
            quality = pd.to_numeric(table_rows.get("data_quality_score", "0"), errors="coerce").fillna(0)
            table_rows = (
                table_rows.assign(_course_key=course_key, _quality=quality)
                .sort_values("_quality", ascending=False)
                .drop_duplicates("_course_key", keep="first")
                .drop(columns=["_course_key", "_quality"])
            )
        credit_ready = table_rows
        if not credit_ready.empty:
            credit_ready = credit_ready[
                credit_ready.get("credit_value", "").astype(str).str.strip().ne("")
            ]
        recommendation_ready = table_rows
        if not recommendation_ready.empty:
            recommendation_ready = recommendation_ready[
                recommendation_ready.get("credit_value", "").astype(str).str.strip().ne("")
                & recommendation_ready.get("recommended_grades", "").astype(str).str.strip().ne("")
            ]
        fully_specified = recommendation_ready
        if not fully_specified.empty:
            fully_specified = fully_specified[
                fully_specified.get("offered_semesters", "").astype(str).str.strip().ne("")
            ]

        grades_present: set[int] = set()
        for value in fully_specified.get("recommended_grades", pd.Series(dtype=str)).astype(str):
            grades_present.update(int(item) for item in re.findall(r"(?<!\d)([1-6])(?!\d)", value))
        supported_pairs = 0
        possible_pairs = len(grades_present) * 2
        for grade in sorted(grades_present):
            for semester in (1, 2):
                total_credits = 0.0
                for _, course in fully_specified.iterrows():
                    course_grades = {
                        int(item)
                        for item in re.findall(
                            r"(?<!\d)([1-6])(?!\d)",
                            normalize_text(course.get("recommended_grades", "")),
                        )
                    }
                    course_semesters = {
                        int(item)
                        for item in re.findall(
                            r"(?<!\d)([12])(?!\d)",
                            normalize_text(course.get("offered_semesters", "")),
                        )
                    }
                    if grade in course_grades and semester in course_semesters:
                        try:
                            total_credits += float(course.get("credit_value", 0) or 0)
                        except (TypeError, ValueError):
                            continue
                if total_credits >= 18:
                    supported_pairs += 1

        fresh_rows = (
            int(subset.get("collection_status", "").astype(str).eq("fresh").sum())
            if not subset.empty and "collection_status" in subset.columns
            else 0
        )
        stale_rows = (
            int(subset.get("collection_status", "").astype(str).eq("stale_after_error").sum())
            if not subset.empty and "collection_status" in subset.columns
            else 0
        )
        status = "recommendation_ready"
        if len(recommendation_ready) == 0:
            status = "not_recommendation_ready"
        elif stale_rows:
            status = "stale_but_recommendation_ready"
        if len(table_rows) == 0:
            capability_status = "unavailable"
        elif len(credit_ready) == 0:
            capability_status = "course_list_only"
        elif possible_pairs and supported_pairs == possible_pairs:
            capability_status = "full_recommendation"
        else:
            capability_status = "partial_recommendation"
        rows.append(
            {
                "department_name": department,
                "total_records": len(subset),
                "table_rows": len(table_rows),
                "credit_ready_rows": len(credit_ready),
                "recommendation_ready_rows": len(recommendation_ready),
                "fully_specified_rows": len(fully_specified),
                "supported_grade_semester_pairs": supported_pairs,
                "possible_grade_semester_pairs": possible_pairs,
                "fresh_rows": fresh_rows,
                "stale_rows": stale_rows,
                "course_code_coverage": round(
                    table_rows.get("course_code", "").astype(str).str.strip().ne("").mean(), 4
                )
                if len(table_rows)
                else 0.0,
                "credit_coverage": round(
                    table_rows.get("credit_value", "").astype(str).str.strip().ne("").mean(), 4
                )
                if len(table_rows)
                else 0.0,
                "grade_coverage": round(
                    table_rows.get("recommended_grades", "").astype(str).str.strip().ne("").mean(), 4
                )
                if len(table_rows)
                else 0.0,
                "semester_coverage": round(
                    table_rows.get("offered_semesters", "").astype(str).str.strip().ne("").mean(), 4
                )
                if len(table_rows)
                else 0.0,
                "status": status,
                "capability_status": capability_status,
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    sources = load_curriculum_sources(SOURCE_CSV_PATH)
    previous_records = load_previous_good_records(OUTPUT_CSV_PATH)
    all_records: list[dict[str, str]] = []
    error_records: list[dict[str, str]] = []

    try:
        official_pdf_sources = fetch_official_curriculum_sources()
    except Exception as exc:  # noqa: BLE001
        official_pdf_sources = ()
        print(f"공식 교과과정 PDF 목록 수집 실패: {exc}")

    for pdf_source in official_pdf_sources:
        source = _pdf_source_as_curriculum_source(pdf_source)
        try:
            pdf_rows = extract_official_curriculum_pdf(fetch_pdf_bytes(pdf_source.url), pdf_source)
            records = [build_official_pdf_record(row) for row in pdf_rows]
            print(
                f"[공식 PDF {pdf_source.sequence:02d}/15] "
                f"{pdf_source.college_name}: {len(records)}개 교과목"
            )
        except Exception as exc:  # noqa: BLE001
            stale = stale_records_for_source(source, previous_records, str(exc))
            if stale:
                all_records.extend(stale)
                continue
            error_records.append(
                {
                    "college_name": source.college_name,
                    "department_name": source.department_name,
                    "department_url": source.department_url,
                    "curriculum_title": source.curriculum_title,
                    "curriculum_url": source.curriculum_url,
                    "source_type": source.source_type,
                    "section_title": "",
                    "record_type": "crawl_error",
                    "course_code": "",
                    "title": "",
                    "course_name": "",
                    "description": "",
                    "credit": "",
                    "semester": "",
                    "grade": "",
                    "course_type": "",
                    "english_title": "",
                    "raw_text": f"error: {exc}",
                    "credit_value": "",
                    "recommended_grades": "",
                    "offered_semesters": "",
                    "is_required": "false",
                    "availability_status": "curriculum_only",
                    "data_quality_score": "0",
                    "collection_status": "crawl_error",
                    "collection_error": normalize_text(str(exc))[:500],
                    "collected_at": utc_now_iso(),
                    "curriculum_year": str(pdf_source.year),
                    "source_page": "",
                    "source_priority": "100",
                }
            )
            continue
        if not records:
            stale = stale_records_for_source(
                source,
                previous_records,
                "공식 PDF에서 교과목 레코드를 찾지 못했습니다.",
            )
            if stale:
                all_records.extend(stale)
                continue
        all_records.extend(records)

    for source in sources:
        try:
            records = parse_curriculum_page(source)
        except Exception as exc:  # noqa: BLE001
            stale = stale_records_for_source(source, previous_records, str(exc))
            if stale:
                all_records.extend(stale)
                continue
            error_records.append(
                {
                    "college_name": source.college_name,
                    "department_name": source.department_name,
                    "department_url": source.department_url,
                    "curriculum_title": source.curriculum_title,
                    "curriculum_url": source.curriculum_url,
                    "source_type": source.source_type,
                    "section_title": "",
                    "record_type": "crawl_error",
                    "course_code": "",
                    "title": "",
                    "course_name": "",
                    "description": "",
                    "credit": "",
                    "semester": "",
                    "grade": "",
                    "course_type": "",
                    "english_title": "",
                    "raw_text": f"error: {exc}",
                    "credit_value": "",
                    "recommended_grades": "",
                    "offered_semesters": "",
                    "is_required": "false",
                    "availability_status": "curriculum_only",
                    "data_quality_score": "0",
                    "collection_status": "crawl_error",
                    "collection_error": normalize_text(str(exc))[:500],
                    "collected_at": utc_now_iso(),
                }
            )
            continue
        if not records:
            stale = stale_records_for_source(source, previous_records, "페이지에서 교과목 레코드를 찾지 못했습니다.")
            if stale:
                all_records.extend(stale)
                continue
        all_records.extend(records)

    combined_df = dedupe_records(all_records + error_records)
    if not combined_df.empty:
        combined_df["course_code_conflict"] = "false"
        official_mask = combined_df.get("source_type", "").astype(str).eq("official_curriculum_pdf")
        official_rows = combined_df[official_mask & combined_df.get("course_code", "").astype(str).str.strip().ne("")]
        if not official_rows.empty:
            conflict_keys = {
                (department, code)
                for (department, code), group in official_rows.groupby(["department_name", "course_code"])
                if group["course_name"].astype(str).nunique() > 1
            }
            if conflict_keys:
                conflict_mask = combined_df.apply(
                    lambda row: (str(row.get("department_name", "")), str(row.get("course_code", "")))
                    in conflict_keys,
                    axis=1,
                )
                combined_df.loc[conflict_mask, "course_code_conflict"] = "true"
    OUTPUT_CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    combined_df.to_csv(OUTPUT_CSV_PATH, index=False, encoding="utf-8-sig")
    diagnosis_df = build_collection_diagnosis(sources, combined_df)
    diagnosis_df.to_csv(DIAGNOSIS_OUTPUT_PATH, index=False, encoding="utf-8-sig")

    print(f"저장 완료: {OUTPUT_CSV_PATH}")
    print(f"진단 저장 완료: {DIAGNOSIS_OUTPUT_PATH}")
    print(f"입력 소스 수: {len(sources)}")
    print(f"공식 교과과정 PDF 수: {len(official_pdf_sources)}")
    print(f"생성 레코드 수: {len(combined_df)}")
    if not diagnosis_df.empty:
        print(
            "개인화 추천 가능 학과 수: "
            f"{int(diagnosis_df['status'].isin(['recommendation_ready', 'stale_but_recommendation_ready']).sum())}"
            f"/{len(diagnosis_df)}"
        )


if __name__ == "__main__":
    main()
