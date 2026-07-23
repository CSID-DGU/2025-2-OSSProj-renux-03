from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pandas as pd
import pytest


RAG_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAG_ROOT))

from src.crawlers.dongguk_entry_year_guide import (  # noqa: E402
    EntryYearGuideParseError,
    HOPE_COURSE_PERIOD_SECTION,
    _build_records_for_pdf,
    _extract_hope_course_application_text,
)
from src.pipelines.ingest import _entry_year_guide_cache_is_stale, build_rule_chunks  # noqa: E402
from src.services.source_contract import normalized_source_contract  # noqa: E402


@pytest.fixture(scope="module")
def hope_course_record() -> dict[str, str]:
    records = _build_records_for_pdf(RAG_ROOT / "data" / "2026_edu.pdf")
    matches = [record for record in records if record["section"] == HOPE_COURSE_PERIOD_SECTION]
    assert len(matches) == 1
    return matches[0]


def test_2026_hope_course_table_recovers_exact_official_facts_and_lineage(hope_course_record):
    record = hope_course_record
    compact = record["text"].replace(" ", "")

    assert "희망강의신청(장바구니)" in record["title"]
    assert "1학기재학ㆍ휴학생전체2026.01.19.(월)10:00~01.21.(수)23:59전체과목" in compact
    assert "2학기재학ㆍ휴학생전체2026.07.20.(월)10:00~07.22.(수)23:59전체과목" in compact
    assert "학교사정에따라추후변동가능" in compact
    assert record["source_file"] == "2026_edu.pdf"
    assert record["published_at"] == "2026-02-19"
    assert record["source_version"] == "2026-02-19"
    assert record["source_sha256"] == "b8e8fa3ec1820babe803226994f00f38b242df03c083fae51dc5c1e47b96fb12"
    assert record["source_url"].startswith("https://www.dongguk.edu/resources/files/")
    assert record["source_page_url"] == "https://www.dongguk.edu/page/209"
    assert record["page_start"] == record["page_end"] == "19"
    assert record["page_label"] == "17"


def test_hope_course_record_stays_atomic_through_rule_chunking(hope_course_record):
    record = hope_course_record.copy()
    record["db_id"] = 1

    chunks = build_rule_chunks(pd.DataFrame([record]))

    assert len(chunks) == 1
    chunk = chunks.iloc[0]
    compact = re.sub(r"\s+", "", chunk["chunk_text"])
    assert "2026.07.20.(월)10:00~07.22.(수)23:59" in compact
    assert re.search(r"재학[ㆍᆞ]휴학생전체", compact)
    assert "학교사정에따라추후변동가능" in compact
    assert chunk["url"] == record["source_url"]
    for field in (
        "source_file",
        "source_page_url",
        "source_version",
        "source_sha256",
        "page_start",
        "page_end",
        "page_label",
    ):
        assert chunk[field] == record[field]

    source = {
        "source": "rules",
        "chunk_id": chunk["chunk_id"],
        "url": chunk["url"],
        "published_at": chunk["published_at"],
        "snippet": chunk["chunk_text"],
        "metadata": chunk.drop(labels=["chunk_text"]).to_dict(),
    }
    lineage = normalized_source_contract(source)
    assert lineage["dataset"] == "rules"
    assert lineage["source_type"] == "entry_year_guide_pdf"
    assert lineage["locator"] == "2026_edu.pdf"
    assert lineage["url"] == record["source_url"]
    assert source["metadata"]["page_label"] == "17"


def test_exposed_hope_course_section_fails_when_table_values_are_missing():
    broken = """
    2. 희망강의신청
    가. 취지
    (1) 수강신청 대비 장바구니 개념
    (2) 수강신청 편의 제공
    나. 대상 및 신청기간
    ※ 학교사정에 따라 추후 변동 가능
    다. 신청방법: nDRIMS
    """

    with pytest.raises(EntryYearGuideParseError, match="1학기 or 2학기"):
        _extract_hope_course_application_text(broken)


def test_guide_cache_is_invalidated_by_parser_or_source_change(tmp_path):
    output = tmp_path / "guide_sections.csv"
    parser = tmp_path / "crawler.py"
    output.write_text("cached", encoding="utf-8")
    parser.write_text("old parser", encoding="utf-8")
    output.touch()
    assert _entry_year_guide_cache_is_stale(output, [parser]) is False

    parser.write_text("new parser", encoding="utf-8")
    parser.touch()
    output_mtime = output.stat().st_mtime_ns
    parser_mtime = max(parser.stat().st_mtime_ns, output_mtime + 1)
    os.utime(parser, ns=(parser_mtime, parser_mtime))
    assert _entry_year_guide_cache_is_stale(output, [parser]) is True
