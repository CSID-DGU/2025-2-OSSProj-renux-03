from __future__ import annotations

from bs4 import BeautifulSoup

from src.crawlers.dongguk_department_curriculum_content import (
    CurriculumSource,
    build_table_records,
    make_soup,
    normalize_grade_list,
    read_table_to_df,
    stale_records_for_source,
    table_relevance_score,
)


def _source() -> CurriculumSource:
    return CurriculumSource(
        college_name="공과대학",
        department_name="테스트학과",
        department_key="테스트학과",
        department_url="https://example.dongguk.edu",
        curriculum_title="전공과목 개설총괄표",
        curriculum_url="https://example.dongguk.edu/page/1",
        source_type="curriculum_table",
    )


def test_table_parser_expands_rowspan_and_multirow_headers_without_column_shift():
    soup = make_soup(
        """
        <table>
          <tr>
            <th rowspan="2">학수번호</th>
            <th rowspan="2">교과목명</th>
            <th colspan="2">이수 정보</th>
          </tr>
          <tr><th>학점</th><th>이수대상</th></tr>
          <tr><td rowspan="2">CSE3001</td><td>인공지능</td><td>3</td><td>3학년</td></tr>
          <tr><td>인공지능실습</td><td>1</td><td>3학년</td></tr>
        </table>
        """
    )

    frame = read_table_to_df(soup.find("table"))

    assert frame.shape == (2, 4)
    assert frame.iloc[1, 0] == "CSE3001"
    assert frame.iloc[1, 1] == "인공지능실습"
    assert "학점" in frame.columns[2]
    assert table_relevance_score(frame) >= 3


def test_duplicate_semantic_headers_are_coalesced_instead_of_crashing():
    soup = BeautifulSoup(
        """
        <table>
          <tr><th>학수번호</th><th>과목명</th><th>교과목명</th><th>학점</th><th>학년</th></tr>
          <tr><td>CSE3001</td><td></td><td>인공지능</td><td>3</td><td>3</td></tr>
        </table>
        """,
        "html.parser",
    )

    records = build_table_records(read_table_to_df(soup.find("table")), _source(), "교육과정")

    assert len(records) == 1
    assert records[0]["course_name"] == "인공지능"
    assert records[0]["credit_value"] == "3"
    assert records[0]["recommended_grades"] == "3"


def test_transient_crawl_failure_keeps_last_known_good_rows():
    previous = {
        _source().curriculum_url: [
            {
                "department_name": "테스트학과",
                "curriculum_url": _source().curriculum_url,
                "record_type": "table_row",
                "course_code": "CSE3001",
                "title": "인공지능",
                "course_name": "인공지능",
                "credit": "3",
                "grade": "3학년",
                "semester": "1학기",
                "course_type": "전문",
            }
        ]
    }

    stale = stale_records_for_source(_source(), previous, "timeout")

    assert stale[0]["course_code"] == "CSE3001"
    assert stale[0]["collection_status"] == "stale_after_error"
    assert stale[0]["collection_error"] == "timeout"


def test_whole_grade_courses_are_available_to_all_undergraduate_years():
    assert normalize_grade_list("전체학년") == "1|2|3|4"
