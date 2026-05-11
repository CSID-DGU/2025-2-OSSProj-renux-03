from __future__ import annotations

from src.rag.chunker import TextChunker
from src.schemas.document import DocumentSchema


def test_chunker_emits_metadata_with_source_url():
    doc = DocumentSchema(
        id="doc-1",
        source="dongguk_official",
        category="학사공지",
        sub_category="수업/성적",
        title="수강신청 정정 안내",
        content=("수강신청 정정 안내입니다.\n" * 120),
        url="https://example.com/doc-1",
        published_at="2025-01-01",
        updated_at="2025-01-01",
        department="교무학생처",
        campus="서울",
        document_type="notice",
        has_attachment=False,
        attachment_urls=[],
        valid_from=None,
        valid_until=None,
        collected_at="2025-01-01T00:00:00Z",
    )

    chunks = TextChunker(chunk_size=1000, chunk_overlap=120).chunk_document(doc)

    assert len(chunks) >= 2
    assert chunks[0].metadata.document_id == "doc-1"
    assert chunks[0].metadata.source_url == "https://example.com/doc-1"
