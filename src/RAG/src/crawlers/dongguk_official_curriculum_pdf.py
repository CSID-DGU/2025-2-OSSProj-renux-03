"""동국대학교 본부가 공개한 연도별 교과과정 PDF를 구조화합니다.

학과 홈페이지는 메뉴 경로와 표 형식이 서로 달라 수집 누락이 생기기 쉽습니다.
본부 교육과정 페이지의 단과대학별 PDF는 동일한 편집 양식을 사용하므로 과목명,
학수번호, 학점, 이수대상, 개설학기의 1차 기준(source of truth)으로 사용합니다.
학과 홈페이지 자료는 이후 교과목 해설을 보강하는 용도로 유지합니다.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from io import BytesIO
import re
from typing import Iterable, Mapping, Sequence
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


OFFICIAL_CURRICULUM_PAGE = "https://www.dongguk.edu/page/137"
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DonggukCurriculumPdfCrawler/1.0)",
    "Accept-Language": "ko-KR,ko;q=0.9",
}
COURSE_CODE_RE = re.compile(r"^[A-Z]{2,10}-?(?:\d{4,5}|\*{4})$")
DEPARTMENT_NAME_RE = re.compile(r"(?:학과|학부|전공|과)$")
PDF_LINK_RE = re.compile(r"/curriculum/(?P<year>\d{4})/(?P<number>\d+)\.\s*[^/]+\.pdf$", re.I)
TABLE_REQUIRED_HEADERS = ("학수번호", "교과목명", "학점", "이수대상")
SECTION_STOP_WORDS = (
    "필수이수과목",
    "필수이수권장과목",
    "필수이수 권장과목",
    "이수권장과목",
    "이수 권장과목",
    "전공필수",
    "교과목별 학습성과",
    "비교과 교육과정",
    "전공인정 타 학과",
    "전공인정 타학과",
    "마이크로디그리 과정",
)


@dataclass(frozen=True)
class OfficialCurriculumPdfSource:
    year: int
    sequence: int
    college_name: str
    url: str


@dataclass(frozen=True)
class OfficialCourseRow:
    college_name: str
    department_name: str
    course_code: str
    title: str
    credit: str
    theory_hours: str
    practice_hours: str
    course_type: str
    grade: str
    original_language: str
    semester: str
    remarks: str
    source_url: str
    curriculum_year: int
    page_number: int
    is_required: bool = False


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", "" if value is None else str(value)).strip()


def _normalized_name(value: str) -> str:
    return re.sub(r"[^가-힣a-z0-9]", "", _clean(value).lower())


def _college_name_from_url(url: str) -> str:
    filename = unquote(urlparse(url).path.rsplit("/", 1)[-1])
    filename = re.sub(r"^\d+\.\s*", "", filename)
    return re.sub(r"\.pdf$", "", filename, flags=re.I).strip()


def discover_official_curriculum_pdfs(
    html: str,
    *,
    page_url: str = OFFICIAL_CURRICULUM_PAGE,
    year: int | None = None,
) -> tuple[OfficialCurriculumPdfSource, ...]:
    """교육과정 안내 페이지에서 최신 단과대학 PDF(1~15번)를 찾습니다."""
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[OfficialCurriculumPdfSource] = []
    for anchor in soup.find_all("a", href=True):
        url = urljoin(page_url, _clean(anchor.get("href")))
        match = PDF_LINK_RE.search(unquote(urlparse(url).path))
        if not match:
            continue
        sequence = int(match.group("number"))
        if not 1 <= sequence <= 15:
            continue
        source_year = int(match.group("year"))
        candidates.append(
            OfficialCurriculumPdfSource(
                year=source_year,
                sequence=sequence,
                college_name=_college_name_from_url(url),
                url=url,
            )
        )

    if not candidates:
        return ()
    selected_year = year if year is not None else max(source.year for source in candidates)
    deduped = {
        source.sequence: source
        for source in candidates
        if source.year == selected_year
    }
    return tuple(deduped[number] for number in sorted(deduped))


def fetch_official_curriculum_sources(
    *,
    page_url: str = OFFICIAL_CURRICULUM_PAGE,
    year: int | None = None,
    timeout: float = 30.0,
) -> tuple[OfficialCurriculumPdfSource, ...]:
    response = requests.get(page_url, headers=DEFAULT_HEADERS, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding
    return discover_official_curriculum_pdfs(response.text, page_url=page_url, year=year)


def fetch_pdf_bytes(url: str, *, timeout: float = 90.0) -> bytes:
    response = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
    response.raise_for_status()
    if not response.content.startswith(b"%PDF"):
        raise ValueError(f"PDF가 아닌 응답을 받았습니다: {url}")
    return response.content


def detect_department_name(text: str) -> str:
    """학과 소개 첫머리에서 현재 학과명을 읽습니다.

    본문 중 "학부"로 끝나는 문장이 오인되는 것을 막기 위해 첫 네 줄만 사용합니다.
    """
    for raw_line in text.splitlines()[:4]:
        line = _clean(raw_line).replace("․", "·")
        line = re.sub(r"^[▶■□❏○●•\-]+\s*", "", line)
        if not line or len(line) > 35:
            continue
        if "교육목표" in line or "학과(전공)" in line or line.count("▶"):
            continue
        if re.search(r"\d", line):
            continue
        if DEPARTMENT_NAME_RE.search(line):
            return line
    return ""


def _header_word(words: Sequence[Mapping[str, object]], label: str) -> Mapping[str, object] | None:
    exact = [word for word in words if _clean(word.get("text")) == label]
    if exact:
        return min(exact, key=lambda word: float(word.get("top", 0)))
    if label == "전공구분":
        variants = [word for word in words if _clean(word.get("text")).startswith("전공구")]
        return min(variants, key=lambda word: float(word.get("top", 0))) if variants else None
    if label == "원어강의":
        variants = [word for word in words if _clean(word.get("text")).startswith("원어강")]
        if variants:
            return min(variants, key=lambda word: float(word.get("top", 0)))
    if label == "개설학기":
        variants = [word for word in words if _clean(word.get("text")).startswith("개설학")]
        if variants:
            return min(variants, key=lambda word: float(word.get("top", 0)))

    stacked_parts = {
        "원어강의": ("원어", "강의"),
        "개설학기": ("개설", "학기"),
    }
    if label in stacked_parts:
        first_text, second_text = stacked_parts[label]
        first_words = [word for word in words if _clean(word.get("text")) == first_text]
        second_words = [word for word in words if _clean(word.get("text")) == second_text]
        pairs: list[tuple[Mapping[str, object], Mapping[str, object]]] = []
        for first in first_words:
            first_center = (float(first["x0"]) + float(first["x1"])) / 2
            for second in second_words:
                second_center = (float(second["x0"]) + float(second["x1"])) / 2
                if abs(first_center - second_center) <= 4 and abs(
                    float(first.get("top", 0)) - float(second.get("top", 0))
                ) <= 15:
                    pairs.append((first, second))
        if pairs:
            first, second = min(
                pairs,
                key=lambda pair: min(
                    float(pair[0].get("top", 0)),
                    float(pair[1].get("top", 0)),
                ),
            )
            return {
                "text": label,
                "x0": min(float(first["x0"]), float(second["x0"])),
                "x1": max(float(first["x1"]), float(second["x1"])),
                "top": min(float(first.get("top", 0)), float(second.get("top", 0))),
                "bottom": max(
                    float(first.get("bottom", first.get("top", 0))),
                    float(second.get("bottom", second.get("top", 0))),
                ),
            }
    return None


def _find_table_headers(
    words: Sequence[Mapping[str, object]],
) -> tuple[dict[str, float], float] | None:
    labels = (
        "학수번호",
        "교과목명",
        "학점",
        "이론",
        "실습",
        "전공구분",
        "이수대상",
        "원어강의",
        "개설학기",
        "비고",
    )
    found = {label: _header_word(words, label) for label in labels}
    if any(found[label] is None for label in TABLE_REQUIRED_HEADERS):
        return None

    required_tops = [float(found[label]["top"]) for label in TABLE_REQUIRED_HEADERS if found[label] is not None]
    if max(required_tops) - min(required_tops) > 15:
        return None

    positions: dict[str, float] = {}
    for label, word in found.items():
        if word is not None:
            positions[label] = (float(word["x0"]) + float(word["x1"])) / 2
    header_bottom = max(
        float(word.get("bottom", word.get("top", 0)))
        for word in found.values()
        if word is not None
    )
    return positions, header_bottom


def _column_boundaries(positions: Mapping[str, float]) -> list[tuple[str, float, float]]:
    ordered = sorted(positions.items(), key=lambda item: item[1])
    split_points: list[float] = []
    for index in range(len(ordered) - 1):
        label, center = ordered[index]
        next_label, next_center = ordered[index + 1]
        if label == "학수번호" and next_label == "교과목명":
            # 여러 줄 영문 제목의 짧은 끝 조각이 학수번호 열 쪽까지
            # 들여쓰기 없이 돌아오는 PDF가 있어 코드 중심 바로 뒤에서 나눕니다.
            split_points.append(min(center + 12, (center + next_center) / 2))
        elif label == "교과목명" and next_label == "학점":
            # 긴 교과목명은 학점 헤더 가까이까지 이어지는 경우가 많습니다.
            # 단순 중간점으로 나누면 제목 끝 단어가 학점 셀로 잘리므로,
            # 숫자 학점 열의 중심 바로 앞을 경계로 사용합니다.
            split_points.append(next_center - 8)
        else:
            split_points.append((center + next_center) / 2)

    result: list[tuple[str, float, float]] = []
    for index, (label, center) in enumerate(ordered):
        left = float("-inf") if index == 0 else split_points[index - 1]
        right = float("inf") if index + 1 == len(ordered) else split_points[index]
        result.append((label, left, right))
    return result


def _word_column(word: Mapping[str, object], boundaries: Sequence[tuple[str, float, float]]) -> str:
    center = (float(word["x0"]) + float(word["x1"])) / 2
    for label, left, right in boundaries:
        if left <= center < right:
            return label
    return ""


def _section_cutoff(words: Sequence[Mapping[str, object]], header_bottom: float, page_height: float) -> float:
    cutoff = page_height
    lines: list[tuple[float, list[Mapping[str, object]]]] = []
    for word in sorted(words, key=lambda item: (float(item.get("top", 0)), float(item.get("x0", 0)))):
        top = float(word.get("top", 0))
        if top <= header_bottom:
            continue
        if not lines or abs(lines[-1][0] - top) > 1.5:
            lines.append((top, [word]))
        else:
            lines[-1][1].append(word)
    for top, line_words in lines:
        text = "".join(_clean(word.get("text")).replace(" ", "") for word in line_words)
        matches_stop = any(
            stop.replace(" ", "") in text
            for stop in SECTION_STOP_WORDS
            if stop != "전공필수"
        )
        # 표의 비고 셀에 적힌 "전공필수"가 아니라 별도 섹션 제목
        # "전공필수(3과목)"일 때만 본문 종료로 판단합니다.
        matches_required_heading = bool(re.match(r"전공필수\(", text))
        if matches_stop or matches_required_heading:
            cutoff = min(cutoff, top)
    return cutoff


def _join_cell_words(words: Iterable[Mapping[str, object]]) -> str:
    ordered = sorted(words, key=lambda word: (float(word.get("top", 0)), float(word.get("x0", 0))))
    return _clean(" ".join(_clean(word.get("text")) for word in ordered if _clean(word.get("text"))))


def _join_course_title(words: Iterable[Mapping[str, object]]) -> str:
    """한 줄 안의 띄어쓰기는 보존하고, PDF 줄바꿈으로 쪼개진 단어는 다시 붙입니다."""
    ordered = sorted(words, key=lambda word: (float(word.get("top", 0)), float(word.get("x0", 0))))
    lines: list[tuple[float, list[str]]] = []
    for word in ordered:
        text = _clean(word.get("text"))
        if not text:
            continue
        top = float(word.get("top", 0))
        if not lines or abs(lines[-1][0] - top) > 1.5:
            lines.append((top, [text]))
        else:
            lines[-1][1].append(text)
    line_texts = [" ".join(parts) for _, parts in lines]
    if not line_texts:
        return ""
    title = line_texts[0]
    for text in line_texts[1:]:
        previous_ascii = bool(re.search(r"[A-Za-z0-9]$", title))
        next_ascii = bool(re.match(r"[A-Za-z0-9]", text))
        short_word_fragment = " " not in text and len(text) <= 8
        separator = " " if previous_ascii and next_ascii and not short_word_fragment else ""
        title += separator + text
    return _clean(title)


def extract_course_rows_from_page(
    words: Sequence[Mapping[str, object]],
    *,
    page_height: float,
    source: OfficialCurriculumPdfSource,
    department_name: str,
    page_number: int,
    horizontal_lines: Sequence[Mapping[str, object]] = (),
) -> tuple[OfficialCourseRow, ...]:
    header = _find_table_headers(words)
    if header is None or not department_name:
        return ()
    positions, header_bottom = header
    boundaries = _column_boundaries(positions)
    cutoff = _section_cutoff(words, header_bottom, page_height)

    anchors = [
        word
        for word in words
        if header_bottom < float(word.get("top", 0)) < cutoff
        and COURSE_CODE_RE.fullmatch(_clean(word.get("text")))
        and _word_column(word, boundaries) == "학수번호"
    ]
    anchors.sort(key=lambda word: float(word.get("top", 0)))
    table_left = min(positions.values())
    table_right = max(positions.values())
    row_lines = sorted(
        {
            float(line.get("top", 0))
            for line in horizontal_lines
            if abs(float(line.get("y1", 0)) - float(line.get("y0", 0))) <= 1
            and float(line.get("x0", float("inf"))) <= table_left
            and float(line.get("x1", float("-inf"))) >= table_right
            and header_bottom <= float(line.get("top", 0)) <= cutoff
        }
    )
    rows: list[OfficialCourseRow] = []
    for index, anchor in enumerate(anchors):
        anchor_top = float(anchor["top"])
        previous_top = float(anchors[index - 1]["top"]) if index else header_bottom
        next_top = float(anchors[index + 1]["top"]) if index + 1 < len(anchors) else cutoff
        top = (previous_top + anchor_top) / 2
        bottom = (anchor_top + next_top) / 2 if next_top != float("inf") else cutoff
        line_above = [value for value in row_lines if value < anchor_top]
        line_below = [value for value in row_lines if value > anchor_top]
        if line_above and line_below:
            candidate_top = line_above[-1]
            candidate_bottom = line_below[0]
            anchors_in_cell = sum(
                candidate_top < float(candidate.get("top", 0)) < candidate_bottom
                for candidate in anchors
            )
            # 공식 표의 가로선을 우선 사용하면, 학수번호 위·아래로 여러 줄에
            # 걸친 영문 교과목명도 인접 행과 섞이지 않습니다. 다만 선택과목
            # 묶음처럼 한 셀 안에 학수번호가 여러 개 있으면 앵커 중간점으로
            # 다시 나눠 각 과목을 독립 행으로 유지합니다.
            if 4 <= candidate_bottom - candidate_top <= 90 and anchors_in_cell == 1:
                top = candidate_top
                bottom = candidate_bottom
        segment = [
            word
            for word in words
            if top <= float(word.get("top", 0)) < bottom
            and float(word.get("top", 0)) < cutoff
        ]
        cells: dict[str, list[Mapping[str, object]]] = {label: [] for label in positions}
        for word in segment:
            column = _word_column(word, boundaries)
            if column:
                cells.setdefault(column, []).append(word)

        title = _join_course_title(cells.get("교과목명", ()))
        credit = _join_cell_words(cells.get("학점", ()))
        if not title or not re.search(r"\d", credit):
            continue
        remarks = _join_cell_words(cells.get("비고", ()))
        segment_text = _join_cell_words(segment)
        is_required = any(token in f"{remarks} {segment_text}" for token in ("전공필수", "필수이수", "법정필수"))
        rows.append(
            OfficialCourseRow(
                college_name=source.college_name,
                department_name=department_name,
                course_code=_clean(anchor.get("text")),
                title=title,
                credit=credit,
                theory_hours=_join_cell_words(cells.get("이론", ())),
                practice_hours=_join_cell_words(cells.get("실습", ())),
                course_type=_join_cell_words(cells.get("전공구분", ())),
                grade=_join_cell_words(cells.get("이수대상", ())),
                original_language=_join_cell_words(cells.get("원어강의", ())),
                semester=_join_cell_words(cells.get("개설학기", ())),
                remarks=remarks,
                source_url=source.url,
                curriculum_year=source.year,
                page_number=page_number,
                is_required=is_required,
            )
        )
    return tuple(rows)


def _required_course_blob(text: str) -> str:
    compact = text.replace("\u00a0", " ")
    marker = re.search(r"필수이수\s*(?:권장)?\s*과목", compact)
    if not marker:
        return ""
    tail = compact[marker.end() :]
    stop_positions = [
        position
        for token in ("교과목별 학습성과", "비교과 교육과정", "전공인정")
        if (position := tail.find(token)) >= 0
    ]
    if stop_positions:
        tail = tail[: min(stop_positions)]
    return _clean(tail[:2000])


def extract_official_curriculum_pdf(
    pdf_bytes: bytes,
    source: OfficialCurriculumPdfSource,
) -> tuple[OfficialCourseRow, ...]:
    """단과대학 PDF 한 개에서 학과별 교과 교육과정 행을 추출합니다."""
    import pdfplumber

    rows: list[OfficialCourseRow] = []
    required_by_department: dict[str, list[str]] = {}
    current_department = ""
    main_table_continuation = False
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            detected = detect_department_name(text) if "교육목표 및 인재상" in text else ""
            if detected:
                current_department = detected
                main_table_continuation = False
            elif not current_department:
                first_line = _clean(text.splitlines()[0]) if text.splitlines() else ""
                if first_line.endswith("공통"):
                    current_department = first_line

            required_blob = _required_course_blob(text)
            if current_department and required_blob:
                required_by_department.setdefault(current_department, []).append(required_blob)

            lines = [line.strip() for line in text.splitlines() if line.strip()]
            first_lines = " ".join(lines[:3])
            starts_main_table = "교과 교육과정" in first_lines and "마이크로디그리" not in first_lines
            continues_main_table = bool(lines) and lines[0].startswith("학수번호") and main_table_continuation
            should_parse = starts_main_table or continues_main_table
            if not should_parse or not all(header in text for header in TABLE_REQUIRED_HEADERS):
                if not continues_main_table:
                    main_table_continuation = False
                continue
            main_table_continuation = True
            words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
            rows.extend(
                extract_course_rows_from_page(
                    words,
                    page_height=float(page.height),
                    source=source,
                    department_name=current_department,
                    page_number=page_number,
                    horizontal_lines=page.lines,
                )
            )

    enriched: list[OfficialCourseRow] = []
    for row in rows:
        required_text = " ".join(required_by_department.get(row.department_name, ()))
        required = row.is_required or (
            len(_normalized_name(row.title)) >= 2
            and _normalized_name(row.title) in _normalized_name(required_text)
        )
        enriched.append(replace(row, is_required=required))
    return tuple(enriched)


__all__ = [
    "OFFICIAL_CURRICULUM_PAGE",
    "OfficialCourseRow",
    "OfficialCurriculumPdfSource",
    "detect_department_name",
    "discover_official_curriculum_pdfs",
    "extract_course_rows_from_page",
    "extract_official_curriculum_pdf",
    "fetch_official_curriculum_sources",
    "fetch_pdf_bytes",
]
