from __future__ import annotations

from src.crawlers.dongguk_official_curriculum_pdf import (
    OfficialCurriculumPdfSource,
    detect_department_name,
    discover_official_curriculum_pdfs,
    extract_course_rows_from_page,
)


def _word(text: str, x0: float, x1: float, top: float, bottom: float | None = None):
    return {
        "text": text,
        "x0": x0,
        "x1": x1,
        "top": top,
        "bottom": bottom if bottom is not None else top + 7,
    }


def test_discovers_latest_college_pdfs_only():
    html = """
    <a href="/resources/files/curriculum/2025/1. 불교대학.pdf">old</a>
    <a href="/resources/files/curriculum/2026/1. 불교대학.pdf">one</a>
    <a href="/resources/files/curriculum/2026/10. 첨단융합대학.pdf">ten</a>
    <a href="/resources/files/curriculum/2026/16. 다전공.pdf">excluded</a>
    """

    sources = discover_official_curriculum_pdfs(html)

    assert [(source.year, source.sequence, source.college_name) for source in sources] == [
        (2026, 1, "불교대학"),
        (2026, 10, "첨단융합대학"),
    ]


def test_department_detection_uses_intro_heading_not_body_sentence():
    assert (
        detect_department_name(
            "Department of Physical Education\n"
            "체육교육과\n"
            "교육목표 및 인재상\n"
            "학적 이해와 인간 움직임의 과학적 이해를 토 1. 학부"
        )
        == "체육교육과"
    )
    assert detect_department_name("▶ 조소전공 ▶ 조소전공\n교육목표 및 인재상") == ""


def test_extracts_wrapped_course_title_and_hyphenated_code():
    words = [
        _word("학수번호", 60, 100, 100),
        _word("교과목명", 120, 160, 100),
        _word("학점", 190, 210, 100),
        _word("이론", 220, 240, 100),
        _word("실습", 245, 265, 100),
        _word("전공구분", 270, 300, 100),
        _word("이수대상", 310, 340, 100),
        _word("원어강의", 350, 375, 100),
        _word("개설학기", 380, 410, 100),
        _word("비고", 420, 445, 100),
        _word("디지털헬스케어소프트웨어", 115, 185, 115),
        _word("CAW-2002", 60, 105, 121),
        _word("3", 195, 205, 121),
        _word("3", 225, 235, 121),
        _word("0", 250, 260, 121),
        _word("기초", 275, 295, 121),
        _word("1,2학년", 310, 340, 121),
        _word("1학기", 385, 405, 121),
        _word("디자인", 135, 165, 127),
        _word("필수이수과목", 60, 120, 150),
    ]
    source = OfficialCurriculumPdfSource(
        year=2026,
        sequence=14,
        college_name="미래융합대학",
        url="https://www.dongguk.edu/curriculum.pdf",
    )

    rows = extract_course_rows_from_page(
        words,
        page_height=700,
        source=source,
        department_name="사회복지상담학과",
        page_number=31,
    )

    assert len(rows) == 1
    assert rows[0].course_code == "CAW-2002"
    assert rows[0].title == "디지털헬스케어소프트웨어디자인"
    assert rows[0].credit == "3"
    assert rows[0].grade == "1,2학년"
    assert rows[0].semester == "1학기"


def test_keeps_long_title_word_near_credit_column_and_five_digit_code():
    words = [
        _word("학수번호", 60, 100, 100),
        _word("교과목명", 120, 160, 100),
        _word("학점", 190, 210, 100),
        _word("이론", 220, 240, 100),
        _word("실습", 245, 265, 100),
        _word("전공구분", 270, 300, 100),
        _word("이수대상", 310, 340, 100),
        _word("원어강의", 350, 375, 100),
        _word("개설학기", 380, 410, 100),
        _word("비고", 420, 445, 100),
        _word("KLE40060", 60, 105, 121),
        _word("나노소재", 120, 150, 121),
        _word("응용", 152, 166, 121),
        _word("및", 168, 176, 121),
        _word("실험", 178, 194, 121),
        _word("3", 202, 208, 121),
        _word("3", 225, 235, 121),
        _word("0", 250, 260, 121),
        _word("전문", 275, 295, 121),
        _word("4학년", 310, 340, 121),
        _word("2학기", 385, 405, 121),
    ]
    source = OfficialCurriculumPdfSource(
        year=2026,
        sequence=9,
        college_name="공과대학",
        url="https://www.dongguk.edu/curriculum.pdf",
    )

    rows = extract_course_rows_from_page(
        words,
        page_height=700,
        source=source,
        department_name="에너지신소재공학과",
        page_number=228,
    )

    assert len(rows) == 1
    assert rows[0].course_code == "KLE40060"
    assert rows[0].title == "나노소재 응용 및 실험"
    assert rows[0].credit == "3"


def test_uses_table_lines_for_title_wrapped_above_and_below_code():
    words = [
        _word("학수번호", 60, 100, 100),
        _word("교과목명", 120, 160, 100),
        _word("학점", 190, 210, 100),
        _word("이수대상", 310, 340, 100),
        _word("MGT4076", 60, 100, 140),
        _word("How", 120, 135, 134),
        _word("to", 138, 146, 134),
        _word("Reason", 149, 170, 134),
        _word("and", 120, 132, 146),
        _word("Persuade", 135, 165, 146),
        _word("3", 195, 205, 140),
        _word("3학년", 310, 340, 140),
        _word("MGT4077", 60, 100, 166),
        _word("Empowering", 120, 155, 158),
        _word("techniques", 158, 190, 158),
        _word("Manage", 120, 145, 166),
        _word("People", 148, 170, 166),
        _word("and", 120, 132, 176),
        _word("Innovation", 135, 170, 176),
        _word("3", 195, 205, 166),
        _word("3학년", 310, 340, 166),
    ]
    lines = [
        {"top": 128, "x0": 50, "x1": 360, "y0": 580, "y1": 580},
        {"top": 154, "x0": 50, "x1": 360, "y0": 554, "y1": 554},
        {"top": 184, "x0": 50, "x1": 360, "y0": 524, "y1": 524},
    ]
    source = OfficialCurriculumPdfSource(
        year=2026,
        sequence=7,
        college_name="경영대학",
        url="https://www.dongguk.edu/curriculum.pdf",
    )

    rows = extract_course_rows_from_page(
        words,
        page_height=700,
        source=source,
        department_name="경영학과",
        page_number=21,
        horizontal_lines=lines,
    )

    assert [row.title for row in rows] == [
        "How to Reason and Persuade",
        "Empowering techniques Manage People and Innovation",
    ]


def test_splits_multiple_course_codes_inside_one_merged_table_cell():
    words = [
        _word("학수번호", 60, 100, 100),
        _word("교과목명", 120, 160, 100),
        _word("학점", 190, 210, 100),
        _word("이수대상", 310, 340, 100),
        _word("PMY4061", 60, 100, 140),
        _word("병원심화실무실습", 120, 180, 140),
        _word("12", 195, 205, 140),
        _word("6학년", 310, 340, 140),
        _word("PMY4062", 60, 100, 154),
        _word("지역약국심화실무실습", 120, 185, 154),
        _word("12", 195, 205, 154),
        _word("6학년", 310, 340, 154),
    ]
    lines = [
        {"top": 128, "x0": 50, "x1": 360, "y0": 580, "y1": 580},
        {"top": 168, "x0": 50, "x1": 360, "y0": 540, "y1": 540},
    ]
    source = OfficialCurriculumPdfSource(
        year=2026,
        sequence=13,
        college_name="약학대학",
        url="https://www.dongguk.edu/curriculum.pdf",
    )

    rows = extract_course_rows_from_page(
        words,
        page_height=700,
        source=source,
        department_name="약학과",
        page_number=18,
        horizontal_lines=lines,
    )

    assert [(row.course_code, row.title, row.credit) for row in rows] == [
        ("PMY4061", "병원심화실무실습", "12"),
        ("PMY4062", "지역약국심화실무실습", "12"),
    ]


def test_recognizes_stacked_original_language_and_semester_headers():
    words = [
        _word("원어", 350, 370, 94),
        _word("개설", 385, 405, 94),
        _word("학수번호", 60, 100, 100),
        _word("교과목명", 120, 160, 100),
        _word("학점", 190, 210, 100),
        _word("이수대상", 310, 340, 100),
        _word("비고", 420, 445, 100),
        _word("강의", 350, 370, 106),
        _word("학기", 385, 405, 106),
        _word("BIO2001", 60, 100, 120),
        _word("환경생물학", 120, 160, 120),
        _word("3", 195, 205, 120),
        _word("2학년", 310, 340, 120),
        _word("영어", 352, 368, 120),
        _word("1", 390, 400, 120),
    ]
    source = OfficialCurriculumPdfSource(
        year=2026,
        sequence=8,
        college_name="바이오시스템대학",
        url="https://www.dongguk.edu/curriculum.pdf",
    )

    rows = extract_course_rows_from_page(
        words,
        page_height=700,
        source=source,
        department_name="생명과학과",
        page_number=43,
    )

    assert len(rows) == 1
    assert rows[0].original_language == "영어"
    assert rows[0].semester == "1"
