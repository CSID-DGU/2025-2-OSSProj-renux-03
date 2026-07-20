"""Safe, retryable repair of legacy normalized notice documents.

The public planner is read-only.  Mutations happen only through
``apply_notice_repairs`` and are staged with an explicit index-pending marker so
an interrupted Chroma/embedding operation can be resumed without pretending the
document is healthy.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Iterable, Sequence

import pandas as pd

from src.database import Chunk, DocumentQualityCheck, Notice, SessionLocal, SourceDocument, kst_now
from src.pipelines.ingest import build_notice_chunks
from src.pipelines.notices_sync import (
    AUTO_NOTICE_FILTER,
    NOTICE_COLLECTION,
    NOTICE_SCHEMA_VERSION,
    _build_quality_checks,
    _canonical_notice_category,
    _export_active_notices_csv,
    _hash_notice_content,
    _normalized_notice_to_notice_row,
    _save_quality_checks,
    _upsert_notice_chunks,
    _upsert_notice_domain_rows,
    refresh_notice_artifacts,
)
from src.vectorstore.chroma_client import update_item_metadatas


REPAIR_PENDING_MARKER = "repair:index_pending"
REPAIRABLE_STATUSES = ("active", "updated", "parse_failed")


@dataclass
class _PreparedRepair:
    document_id: int
    document_key: str
    source_id: str
    normalized_path: Path | None
    before_status: str
    final_status: str
    mode: str
    category_before: str
    category_after: str
    category_source: str
    category_board_fallback: str
    payload: dict[str, Any] | None
    checks: list[dict[str, str]]
    errors: list[str]
    warnings: list[str]
    estimated_chunks: int

    def public(self) -> dict[str, Any]:
        return {
            "document_key": self.document_key,
            "source_id": self.source_id,
            "status_before": self.before_status,
            "status_after": self.final_status if self.mode not in {"failed", "noop"} else self.before_status,
            "mode": self.mode,
            "category_before": self.category_before,
            "category_after": self.category_after,
            "category_source": self.category_source,
            "category_board_fallback": self.category_board_fallback,
            "estimated_chunks": self.estimated_chunks,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def _quality_signatures(checks: Iterable[dict[str, str]]) -> set[tuple[str, str, str]]:
    return {
        (str(item.get("check_type", "")), str(item.get("severity", "")), str(item.get("message", "")))
        for item in checks
    }


def _load_payload(path: Path | None) -> tuple[dict[str, Any] | None, str | None]:
    if path is None:
        return None, "normalized_path_missing"
    if not path.exists():
        return None, "normalized_file_missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, "normalized_json_corrupt"
    if not isinstance(payload, dict):
        return None, "normalized_json_not_object"
    return payload, None


def _canonicalize_payload(document: SourceDocument, payload: dict[str, Any]) -> dict[str, Any]:
    repaired = dict(payload)
    for key, default in {
        "document_key": document.document_key,
        "dataset": "notices",
        "source_type": document.source_type or "html_notice",
        "source_id": document.source_id,
        "title": "",
        "category": "",
        "published_at": "",
        "detail_url": document.source_url or "",
        "content_text": "",
        "content_html": "",
        "attachments": [],
        "board_name": "",
        "board_code": "",
    }.items():
        if key not in repaired or repaired[key] is None:
            repaired[key] = default

    existing_source = str(repaired.get("category_source") or "").strip()
    if existing_source == "board_fallback":
        category_input = repaired.get("category_original", "")
    elif "category_original" in repaired:
        category_input = repaired.get("category_original") or repaired.get("category")
    else:
        category_input = repaired.get("category")
    effective, original, source, fallback = _canonical_notice_category(
        category_input,
        repaired.get("board_name"),
        repaired.get("board_code"),
    )
    repaired["category"] = effective
    repaired["category_original"] = original
    repaired["category_source"] = source
    repaired["category_board_fallback"] = fallback
    repaired["schema_version"] = NOTICE_SCHEMA_VERSION
    repaired["content_hash"] = _hash_notice_content(repaired)
    return repaired


def _frame_for_payload(payload: dict[str, Any], notice_id: int | None) -> pd.DataFrame:
    row = _normalized_notice_to_notice_row(payload, db_id=notice_id)
    return build_notice_chunks(pd.DataFrame([row]))


def _chunks_align(frame: pd.DataFrame, chunks: Sequence[Chunk]) -> bool:
    if frame.empty or len(frame) != len(chunks):
        return False
    generated = {
        str(row["chunk_id"]): str(row["chunk_text"])
        for _, row in frame[["chunk_id", "chunk_text"]].iterrows()
    }
    existing = {str(chunk.chunk_id): str(chunk.chunk_text) for chunk in chunks}
    return generated == existing


def _prepare_repairs(
    session,
    *,
    document_keys: Iterable[str] | None = None,
) -> list[_PreparedRepair]:
    query = session.query(SourceDocument).filter(
        SourceDocument.dataset == "notices",
        SourceDocument.status.in_(REPAIRABLE_STATUSES),
    )
    if document_keys is not None:
        keys = list(dict.fromkeys(document_keys))
        if not keys:
            return []
        query = query.filter(SourceDocument.document_key.in_(keys))
    documents = query.order_by(SourceDocument.id.asc()).all()

    notices_by_url = {
        notice.detail_url: notice
        for notice in session.query(Notice).filter(AUTO_NOTICE_FILTER).all()
        if notice.detail_url
    }
    chunks_by_notice: dict[int, list[Chunk]] = defaultdict(list)
    for chunk in session.query(Chunk).filter(Chunk.notice_id.isnot(None)).all():
        chunks_by_notice[int(chunk.notice_id)].append(chunk)
    checks_by_key: dict[str, list[dict[str, str]]] = defaultdict(list)
    for check in session.query(DocumentQualityCheck).all():
        checks_by_key[check.document_key].append(
            {
                "check_type": check.check_type,
                "severity": check.severity,
                "message": check.message,
            }
        )

    prepared: list[_PreparedRepair] = []
    for document in documents:
        before_status = str(document.status or "unknown")
        before_category = str(document.category or "")
        normalized_path = Path(document.normalized_path) if document.normalized_path else None
        payload, load_error = _load_payload(normalized_path)
        if load_error:
            prepared.append(
                _PreparedRepair(
                    document_id=document.id,
                    document_key=document.document_key,
                    source_id=document.source_id,
                    normalized_path=normalized_path,
                    before_status=before_status,
                    final_status=before_status,
                    mode="failed",
                    category_before=before_category,
                    category_after=before_category,
                    category_source="unknown",
                    category_board_fallback="",
                    payload=None,
                    checks=[],
                    errors=[load_error],
                    warnings=[],
                    estimated_chunks=0,
                )
            )
            continue

        assert payload is not None
        repaired = _canonicalize_payload(document, payload)
        checks, parse_error = _build_quality_checks(repaired, attachments_parse_failed=False)
        warnings = [item["message"] for item in checks if item["severity"] == "warning"]
        if parse_error:
            prepared.append(
                _PreparedRepair(
                    document_id=document.id,
                    document_key=document.document_key,
                    source_id=document.source_id,
                    normalized_path=normalized_path,
                    before_status=before_status,
                    final_status=before_status,
                    mode="failed",
                    category_before=before_category,
                    category_after=str(repaired.get("category") or ""),
                    category_source=str(repaired.get("category_source") or "missing"),
                    category_board_fallback=str(repaired.get("category_board_fallback") or ""),
                    payload=None,
                    checks=checks,
                    errors=parse_error.splitlines(),
                    warnings=warnings,
                    estimated_chunks=0,
                )
            )
            continue

        notice = notices_by_url.get(str(repaired.get("detail_url") or ""))
        existing_chunks = [] if notice is None else chunks_by_notice.get(notice.id, [])
        frame = _frame_for_payload(repaired, None if notice is None else notice.id)
        aligned = notice is not None and _chunks_align(frame, existing_chunks)
        payload_changed = repaired != payload
        document_changed = any(
            (
                before_category != str(repaired.get("category") or ""),
                document.schema_version != NOTICE_SCHEMA_VERSION,
                document.content_hash != repaired.get("content_hash"),
                document.source_url != repaired.get("detail_url"),
                document.title != repaired.get("title"),
                document.published_at != repaired.get("published_at"),
                before_status == "parse_failed",
                bool(document.parse_error),
                _quality_signatures(checks_by_key.get(document.document_key, [])) != _quality_signatures(checks),
            )
        )
        requires_index_repair = not aligned
        if not payload_changed and not document_changed and not requires_index_repair:
            mode = "noop"
        elif aligned:
            mode = "metadata_only"
        elif notice is None or not existing_chunks:
            mode = "embed_new"
        else:
            mode = "embed_replace"

        final_status = before_status if before_status in {"active", "updated"} else "updated"
        prepared.append(
            _PreparedRepair(
                document_id=document.id,
                document_key=document.document_key,
                source_id=document.source_id,
                normalized_path=normalized_path,
                before_status=before_status,
                final_status=final_status,
                mode=mode,
                category_before=before_category,
                category_after=str(repaired.get("category") or ""),
                category_source=str(repaired.get("category_source") or "missing"),
                category_board_fallback=str(repaired.get("category_board_fallback") or ""),
                payload=repaired,
                checks=checks,
                errors=[],
                warnings=warnings,
                estimated_chunks=len(frame) if mode in {"embed_new", "embed_replace"} else 0,
            )
        )
    return prepared


def _manifest(
    prepared: Sequence[_PreparedRepair],
    *,
    operation: str,
    assumed_embedding_chunks_per_minute: float | None,
) -> dict[str, Any]:
    modes = Counter(item.mode for item in prepared)
    estimated_chunks = sum(item.estimated_chunks for item in prepared)
    estimated_minutes = None
    if assumed_embedding_chunks_per_minute is not None:
        if assumed_embedding_chunks_per_minute <= 0:
            raise ValueError("assumed_embedding_chunks_per_minute must be positive")
        estimated_minutes = round(estimated_chunks / assumed_embedding_chunks_per_minute, 2)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "operation": operation,
        "dataset": "notices",
        "mutated": operation == "apply",
        "counts": {
            "documents": len(prepared),
            "repairable": sum(modes[mode] for mode in ("metadata_only", "embed_new", "embed_replace")),
            "metadata_only": modes["metadata_only"],
            "embed_new": modes["embed_new"],
            "embed_replace": modes["embed_replace"],
            "failed": modes["failed"],
            "noop": modes["noop"],
            "estimated_embedding_chunks": estimated_chunks,
        },
        "estimates": {
            "external_embedding_api_cost_usd": 0.0,
            "cost_basis": "The configured encode_texts path runs the embedding model locally; hardware/electricity cost is excluded.",
            "assumed_embedding_chunks_per_minute": assumed_embedding_chunks_per_minute,
            "estimated_embedding_minutes": estimated_minutes,
        },
        "documents": [item.public() for item in prepared],
    }


def plan_notice_repairs(
    session,
    *,
    document_keys: Iterable[str] | None = None,
    assumed_embedding_chunks_per_minute: float | None = None,
) -> dict[str, Any]:
    """Return a dry-run manifest without writing files, DB rows, or vectors."""
    prepared = _prepare_repairs(session, document_keys=document_keys)
    return _manifest(
        prepared,
        operation="dry_run",
        assumed_embedding_chunks_per_minute=assumed_embedding_chunks_per_minute,
    )


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _restore_files(backups: dict[Path, bytes | None]) -> None:
    for path, content in backups.items():
        if content is None:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            continue
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".restore", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        except Exception:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise


def _metadata_from_frame(frame: pd.DataFrame) -> tuple[list[str], list[dict[str, object]]]:
    ids = frame["chunk_id"].astype(str).tolist()
    metadata_rows = frame.drop(columns=["chunk_text"]).to_dict(orient="records")
    cleaned: list[dict[str, object]] = []
    for row in metadata_rows:
        item: dict[str, object] = {}
        for key, value in row.items():
            if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
                item[key] = ""
            else:
                item[key] = value
        cleaned.append(item)
    return ids, cleaned


def apply_notice_repairs(
    *,
    session_factory=SessionLocal,
    document_keys: Iterable[str] | None = None,
    batch_size: int = 100,
    assumed_embedding_chunks_per_minute: float | None = None,
    embed_upserter: Callable[..., None] = _upsert_notice_chunks,
    metadata_updater: Callable[[str, Iterable[str], Iterable[dict[str, object]]], None] = update_item_metadatas,
    artifact_refresher: Callable[[], None] = refresh_notice_artifacts,
    csv_exporter: Callable[[Any], None] = _export_active_notices_csv,
    before_stage_commit: Callable[[Sequence[str]], None] | None = None,
) -> dict[str, Any]:
    """Apply planned repairs in retryable batches.

    File and relational updates are rolled back together if staging fails.  Once
    staging commits, each document remains marked ``repair:index_pending`` until
    vector/index work and artifact refresh both succeed; an interruption is then
    safe to retry with the same command.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    planning_session = session_factory()
    try:
        prepared = _prepare_repairs(planning_session, document_keys=document_keys)
    finally:
        planning_session.close()
    actionable = [item for item in prepared if item.mode in {"metadata_only", "embed_new", "embed_replace"}]

    for offset in range(0, len(actionable), batch_size):
        batch = actionable[offset : offset + batch_size]
        keys = [item.document_key for item in batch]
        session = session_factory()
        backups: dict[Path, bytes | None] = {}
        try:
            documents = {
                doc.document_key: doc
                for doc in session.query(SourceDocument).filter(SourceDocument.document_key.in_(keys)).all()
            }
            for item in batch:
                document = documents[item.document_key]
                assert item.payload is not None and item.normalized_path is not None
                path = item.normalized_path
                backups[path] = path.read_bytes() if path.exists() else None
                _atomic_write(path, item.payload)
                document.source_url = item.payload.get("detail_url")
                document.title = item.payload.get("title")
                document.category = item.payload.get("category")
                document.published_at = item.payload.get("published_at")
                document.content_hash = item.payload.get("content_hash")
                document.schema_version = NOTICE_SCHEMA_VERSION
                document.last_parsed_at = kst_now()
                document.status = "updated"
                document.parse_error = REPAIR_PENDING_MARKER
                _save_quality_checks(session, item.document_key, item.checks)
            normalized = [item.payload for item in batch if item.payload is not None]
            notice_rows = _upsert_notice_domain_rows(session, normalized)
            if before_stage_commit is not None:
                before_stage_commit(keys)
            session.commit()
        except Exception:
            session.rollback()
            _restore_files(backups)
            raise

        try:
            row_by_key = {str(row.get("문서키") or ""): row for row in notice_rows}
            source_by_id = {doc.source_id: doc for doc in documents.values() if doc.source_id}
            embedding_rows = [
                row_by_key[item.document_key]
                for item in batch
                if item.mode in {"embed_new", "embed_replace"}
            ]
            if embedding_rows:
                embed_upserter(session, embedding_rows, source_by_id)

            metadata_frames = [
                _frame_for_payload(item.payload, row_by_key[item.document_key]["db_id"])
                for item in batch
                if item.mode == "metadata_only" and item.payload is not None
            ]
            if metadata_frames:
                metadata_frame = pd.concat(metadata_frames, ignore_index=True)
                ids, metadatas = _metadata_from_frame(metadata_frame)
                metadata_updater(NOTICE_COLLECTION, ids, metadatas)
                indexed_at = kst_now()
                for item in batch:
                    if item.mode == "metadata_only":
                        documents[item.document_key].last_indexed_at = indexed_at
            session.commit()
        except Exception:
            session.rollback()
            # The committed pending marker intentionally remains for retry.
            raise
        finally:
            session.close()

    if actionable:
        # A failed refresh leaves pending markers in place and the next apply
        # retries safely; only a complete refresh may declare documents healthy.
        artifact_refresher()
        final_session = session_factory()
        try:
            final_documents = {
                doc.document_key: doc
                for doc in final_session.query(SourceDocument)
                .filter(SourceDocument.document_key.in_([item.document_key for item in actionable]))
                .all()
            }
            for item in actionable:
                document = final_documents[item.document_key]
                document.status = item.final_status
                document.parse_error = None
            final_session.commit()
            csv_exporter(final_session)
        except Exception:
            final_session.rollback()
            raise
        finally:
            final_session.close()

    result = _manifest(
        prepared,
        operation="apply",
        assumed_embedding_chunks_per_minute=assumed_embedding_chunks_per_minute,
    )
    result["mutated"] = bool(actionable)
    result["completed"] = True
    return result


__all__ = [
    "REPAIR_PENDING_MARKER",
    "apply_notice_repairs",
    "plan_notice_repairs",
]
