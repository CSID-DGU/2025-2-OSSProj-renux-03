from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.database import Base, Chunk, Notice, SourceDocument  # noqa: E402
from src.pipelines.ingest import build_notice_index_frame_from_session  # noqa: E402


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_notice_db_index_frame_preserves_document_identity_and_chunk_order():
    session = _session()
    try:
        url = "https://www.dongguk.edu/article/HAKSANOTICE/detail/100"
        session.add(
            SourceDocument(
                dataset="notices",
                source_type="html_notice",
                source_id="HAKSANOTICE:100",
                source_url=url,
                document_key="notices:HAKSANOTICE:100",
                title="수강정정 안내",
                status="active",
            )
        )
        notice = Notice(
            board="학사공지",
            title="수강정정 안내",
            category="학사공지",
            detail_url=url,
            content="공지 본문",
            attachments="[]",
        )
        session.add(notice)
        session.flush()
        session.add_all(
            [
                Chunk(
                    chunk_id="notice-100-0",
                    chunk_text="[수강정정 안내]\n\n첫 번째 청크",
                    notice_id=notice.id,
                    doc_id="notices:HAKSANOTICE:100",
                    position=0,
                ),
                Chunk(
                    chunk_id="notice-100-1",
                    chunk_text="[수강정정 안내]\n\n수강정정 기간은 5월 21일부터입니다.",
                    notice_id=notice.id,
                    doc_id="notices:HAKSANOTICE:100",
                    position=1,
                ),
            ]
        )
        session.commit()

        frame = build_notice_index_frame_from_session(session)
    finally:
        session.close()

    assert frame["doc_id"].tolist() == ["notices:HAKSANOTICE:100"] * 2
    assert frame["position"].tolist() == [0, 1]


def test_notice_db_index_frame_backfills_legacy_chunk_identity_from_source_document():
    session = _session()
    try:
        url = "https://www.dongguk.edu/article/HAKSANOTICE/detail/101"
        session.add(
            SourceDocument(
                dataset="notices",
                source_type="html_notice",
                source_id="HAKSANOTICE:101",
                source_url=url,
                document_key="notices:HAKSANOTICE:101",
                title="수강정정 안내",
                status="active",
            )
        )
        notice = Notice(
            board="학사공지",
            title="수강정정 안내",
            category="학사공지",
            detail_url=url,
            content="공지 본문",
            attachments="[]",
        )
        session.add(notice)
        session.flush()
        # Pre-migration rows have only the vector chunk ID and body text.
        session.add_all(
            [
                Chunk(chunk_id="notice-101-0", chunk_text="첫 청크", notice_id=notice.id),
                Chunk(chunk_id="notice-101-1", chunk_text="둘째 청크", notice_id=notice.id),
            ]
        )
        session.commit()

        frame = build_notice_index_frame_from_session(session)
    finally:
        session.close()

    assert frame["doc_id"].tolist() == ["notices:HAKSANOTICE:101"] * 2
    assert frame["position"].tolist() == [0, 1]
