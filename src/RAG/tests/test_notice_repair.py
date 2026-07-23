from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

RAG_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = RAG_ROOT / "scripts"
for path in (RAG_ROOT, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from repair_notices import APPLY_CONFIRMATION, main as repair_main  # noqa: E402
from src.database import Base, Chunk, Notice, SourceDocument  # noqa: E402
from src.services.notice_repair import (  # noqa: E402
    _canonicalize_payload,
    _frame_for_payload,
    apply_notice_repairs,
    plan_notice_repairs,
)


def _session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_dry_run_plans_legacy_notice_without_mutating_file_or_database(tmp_path):
    factory = _session_factory()
    normalized = tmp_path / "notices-1.json"
    payload = {
        "document_key": "notices:JANGHAKNOTICE:1",
        "dataset": "notices",
        "source_type": "html_notice",
        "source_id": "JANGHAKNOTICE:1",
        "board_name": "장학공지",
        "board_code": "JANGHAKNOTICE",
        "article_id": 1,
        "title": "2026학년도 교내장학 신청 안내",
        "category": "",
        "published_at": "2026-07-20",
        "detail_url": "https://www.dongguk.edu/article/JANGHAKNOTICE/detail/1",
        "content_text": "교내장학 신청 기간과 제출 서류를 학생에게 안내하는 충분한 길이의 공식 공지 본문입니다.",
        "content_html": "",
        "attachments": [],
        "is_pinned": False,
        "schema_version": 1,
    }
    normalized.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    original_bytes = normalized.read_bytes()
    session = factory()
    session.add(
        SourceDocument(
            dataset="notices",
            source_type="html_notice",
            source_id=payload["source_id"],
            source_url=payload["detail_url"],
            document_key=payload["document_key"],
            title=payload["title"],
            category="",
            published_at=payload["published_at"],
            status="parse_failed",
            content_hash="legacy",
            schema_version=1,
            normalized_path=str(normalized),
            parse_error="본문이 비어 있습니다.",
        )
    )
    session.commit()

    manifest = plan_notice_repairs(session, assumed_embedding_chunks_per_minute=120)

    session.expire_all()
    document = session.query(SourceDocument).one()
    assert manifest["operation"] == "dry_run"
    assert manifest["mutated"] is False
    assert manifest["counts"]["documents"] == 1
    assert manifest["counts"]["embed_new"] == 1
    assert manifest["documents"][0]["category_after"] == "장학공지"
    assert manifest["documents"][0]["category_source"] == "board_fallback"
    assert normalized.read_bytes() == original_bytes
    assert document.status == "parse_failed"
    assert document.schema_version == 1
    assert document.parse_error == "본문이 비어 있습니다."
    session.close()


def test_cli_requires_explicit_confirmation_before_apply(capsys):
    class ForbiddenFactory:
        def __call__(self):
            raise AssertionError("database must not be opened before confirmation")

    assert repair_main(["--apply"], session_factory=ForbiddenFactory()) == 2
    assert APPLY_CONFIRMATION in capsys.readouterr().err


def test_metadata_only_apply_preserves_legacy_chunk_id_and_skips_reembedding(tmp_path):
    factory = _session_factory()
    normalized = tmp_path / "legacy.json"
    payload = {
        "document_key": "notices:JANGHAKNOTICE:2",
        "dataset": "notices",
        "source_type": "html_notice",
        "source_id": "JANGHAKNOTICE:2",
        "board_name": "장학공지",
        "board_code": "JANGHAKNOTICE",
        "article_id": 2,
        "title": "교내장학 신청 안내",
        "category": "",
        "published_at": "2026-07-20",
        "detail_url": "https://www.dongguk.edu/article/JANGHAKNOTICE/detail/2",
        "content_text": "교내장학 신청 기간과 제출 서류 및 결과 확인 방법을 설명하는 공식 공지 본문입니다.",
        "content_html": "",
        "attachments": [],
        "is_pinned": False,
        "schema_version": 1,
    }
    normalized.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    session = factory()
    document = SourceDocument(
        dataset="notices", source_type="html_notice", source_id=payload["source_id"],
        source_url=payload["detail_url"], document_key=payload["document_key"],
        title=payload["title"], category="", published_at=payload["published_at"],
        status="active", content_hash="legacy", schema_version=1,
        normalized_path=str(normalized), parse_error=None,
    )
    notice = Notice(
        board="장학공지", title=payload["title"], category="", published_date=payload["published_at"],
        detail_url=payload["detail_url"], content=payload["content_text"], attachments="[]",
    )
    session.add_all([document, notice])
    session.flush()
    canonical = _canonicalize_payload(document, payload)
    generated = _frame_for_payload(canonical, notice.id)
    session.add(Chunk(chunk_id="legacy-stable-id", chunk_text=str(generated.iloc[0]["chunk_text"]), notice_id=notice.id))
    session.commit()
    session.close()

    updated_ids: list[str] = []

    def metadata_updater(_collection, ids, _metadatas):
        updated_ids.extend(ids)

    def forbid_embedding(*_args, **_kwargs):
        raise AssertionError("unchanged text must not be re-embedded")

    result = apply_notice_repairs(
        session_factory=factory,
        batch_size=10,
        embed_upserter=forbid_embedding,
        metadata_updater=metadata_updater,
        artifact_refresher=lambda: None,
        csv_exporter=lambda _session: None,
    )

    assert result["counts"]["metadata_only"] == 1
    assert result["counts"]["estimated_embedding_chunks"] == 0
    assert updated_ids == ["legacy-stable-id"]
    verification = factory()
    assert verification.query(Chunk).one().chunk_id == "legacy-stable-id"
    assert verification.query(SourceDocument).one().category == "장학공지"
    assert verification.query(SourceDocument).one().parse_error is None
    verification.close()
