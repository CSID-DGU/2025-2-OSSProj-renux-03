from __future__ import annotations

import pandas as pd

from src.ingestion.legacy_csv_source import load_legacy_csv_documents


def test_load_legacy_csv_documents_filters_notice_board_and_parses_attachments(tmp_path):
    csv_path = tmp_path / "dongguk_notices.csv"
    pd.DataFrame(
        [
            {
                "게시판": "학사공지",
                "제목": "수강신청 정정 안내",
                "카테고리": "수업/성적",
                "게시일": "2026-05-01",
                "상세URL": "https://example.com/a",
                "본문": "본문 A",
                "첨부파일": '[{"name":"안내.pdf","url":"https://example.com/a.pdf"}]',
            },
            {
                "게시판": "일반공지",
                "제목": "일반 안내",
                "카테고리": "",
                "게시일": "2026-05-02",
                "상세URL": "https://example.com/b",
                "본문": "본문 B",
                "첨부파일": "[]",
            },
        ]
    ).to_csv(csv_path, index=False)

    settings = {
        "legacy_csv": str(csv_path),
        "legacy_kind": "notice",
        "legacy_board_name": "학사공지",
        "source": "dongguk_official",
        "category": "학사공지",
        "sub_category": "수업/성적",
        "department": "교무학생처",
        "campus": "서울",
        "document_type": "notice",
    }

    docs = load_legacy_csv_documents(settings, limit=10)

    assert len(docs) == 1
    assert docs[0]["title"] == "수강신청 정정 안내"
    assert docs[0]["attachment_urls"] == ["https://example.com/a.pdf"]


def test_load_legacy_csv_documents_normalizes_schedule(tmp_path):
    csv_path = tmp_path / "dongguk_schedule.csv"
    pd.DataFrame(
        [
            {
                "주관부서": "교무학생처",
                "start": "2026-03-02",
                "end": "2026-03-08",
                "2": "수강신청 정정",
            }
        ]
    ).to_csv(csv_path, index=False)

    settings = {
        "legacy_csv": str(csv_path),
        "legacy_kind": "schedule",
        "source": "dongguk_official",
        "category": "학사일정",
        "sub_category": "일정",
        "department": "교무학생처",
        "campus": "서울",
        "document_type": "calendar",
        "base_url": "https://www.dongguk.edu/schedule/detail?schedule_info_seq=22",
    }

    docs = load_legacy_csv_documents(settings, limit=10)

    assert len(docs) == 1
    assert docs[0]["title"] == "수강신청 정정"
    assert docs[0]["valid_from"] == "2026-03-02"
    assert docs[0]["valid_until"] == "2026-03-08"
    assert docs[0]["url"].endswith("#schedule-1")
