from __future__ import annotations

import json

from src.ingestion.dedup import Deduplicator
from src.schemas.document import DocumentSchema


def test_deduplicator_blocks_same_url(tmp_path):
    index_path = tmp_path / "index.jsonl"
    index_path.write_text(
        json.dumps(
            {
                "title": "공지 A",
                "category": "학사공지",
                "published_at": "2025-01-01",
                "url": "https://example.com/a",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    dedup = Deduplicator(index_path)
    doc = DocumentSchema(
        id="doc-1",
        source="dongguk_official",
        category="학사공지",
        sub_category="수업/성적",
        title="다른 제목",
        content="본문",
        url="https://example.com/a",
        published_at="2025-01-02",
        updated_at="2025-01-02",
        department="교무학생처",
        campus="서울",
        document_type="notice",
        has_attachment=False,
        attachment_urls=[],
        valid_from=None,
        valid_until=None,
        collected_at="2025-01-02T00:00:00Z",
    )

    assert dedup.is_duplicate(doc) is True
