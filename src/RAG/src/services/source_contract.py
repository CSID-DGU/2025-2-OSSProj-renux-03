"""Stable, transport-safe source identities used by answers and evaluations."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


_CAMPUSES = {"seoul", "bmc", "wise", "shared", "unknown"}
_EFFECTIVE_DATE_KEYS = (
    "effective_date",
    "schedule_start",
    "schedule_end",
    "apply_deadline",
    "updated_at",
    "sort_date",
)


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _metadata(source: Mapping[str, Any]) -> Mapping[str, Any]:
    value = source.get("metadata")
    return value if isinstance(value, Mapping) else {}


def _metadata_value(metadata: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _clean(metadata.get(key))
        if value is not None:
            return value
    return None


def source_identity(source: Mapping[str, Any]) -> dict[str, Any]:
    """Return the canonical fields that make one cited source distinguishable."""
    metadata = _metadata(source)
    snippet = str(source.get("snippet") or "")
    campus_scope = (_clean(metadata.get("campus_scope")) or "unknown").lower()
    if campus_scope not in _CAMPUSES:
        campus_scope = "unknown"
    effective_date = _metadata_value(metadata, *_EFFECTIVE_DATE_KEYS)
    if effective_date is None:
        effective_date = _clean(source.get("sort_date"))
    return {
        "dataset": _clean(source.get("source")) or "",
        "chunk_id": _clean(source.get("chunk_id")) or _clean(metadata.get("chunk_id")),
        "url": _clean(source.get("url")) or _clean(metadata.get("url")),
        "campus_scope": campus_scope,
        "published_at": _clean(source.get("published_at")) or _clean(metadata.get("published_at")),
        "effective_date": effective_date,
        "snippet_hash": hashlib.sha256(snippet.encode("utf-8")).hexdigest(),
    }


def source_reference(source: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        source_identity(source),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def normalized_source_contract(source: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one API source into the golden-result source contract."""
    metadata = _metadata(source)
    identity = source_identity(source)
    source_type = _clean(metadata.get("source_type")) or identity["dataset"]
    locator = _metadata_value(
        metadata,
        "document_key",
        "notice_id",
        "source_file",
        "filename",
        "schedule_id",
        "staff_id",
    )
    supplied_reference = _clean(source.get("source_ref"))
    computed_reference = source_reference(source)
    if supplied_reference is not None and supplied_reference != computed_reference:
        raise ValueError("source_ref does not match the transported source content")
    return {
        "id": computed_reference,
        "dataset": identity["dataset"],
        "source_type": source_type,
        "chunk_id": identity["chunk_id"],
        "url": identity["url"],
        "campus_scope": identity["campus_scope"],
        "published_at": identity["published_at"],
        "effective_date": identity["effective_date"],
        "snippet": str(source.get("snippet") or ""),
        "snippet_hash": identity["snippet_hash"],
        "citation_number": source.get("citation_number"),
        "locator": locator,
    }


__all__ = ["normalized_source_contract", "source_identity", "source_reference"]
