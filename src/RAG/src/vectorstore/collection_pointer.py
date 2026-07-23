"""Atomic logical-to-physical Chroma collection pointers.

Search code keeps using stable logical names (for example
``dongguk_notices``), while a staged build receives an immutable physical
name.  Replacing this small JSON file is the only activation operation; in-
flight requests may finish on the previous collection and new requests resolve
the new one.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any

from src.config import ARTIFACT_DIR


POINTER_FILE_ENV = "RAG_COLLECTION_POINTER_FILE"
_POINTER_CACHE_LOCK = threading.Lock()
_pointer_cache: dict[str, tuple[tuple[int, int] | None, dict[str, Any]]] = {}


def default_pointer_path() -> Path:
    configured = os.getenv(POINTER_FILE_ENV)
    return Path(configured) if configured else ARTIFACT_DIR / "collection_pointers.json"


def _empty_state() -> dict[str, Any]:
    return {"schema_version": 1, "collections": {}}


def read_pointer_state(path: Path | None = None) -> dict[str, Any]:
    pointer_path = (path or default_pointer_path()).resolve()
    cache_key = str(pointer_path)
    try:
        stat = pointer_path.stat()
        signature: tuple[int, int] | None = (stat.st_mtime_ns, stat.st_size)
    except FileNotFoundError:
        signature = None

    with _POINTER_CACHE_LOCK:
        cached = _pointer_cache.get(cache_key)
        if cached is not None and cached[0] == signature:
            return json.loads(json.dumps(cached[1]))

    if signature is None:
        state = _empty_state()
    else:
        try:
            state = json.loads(pointer_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"collection pointer file is unreadable: {pointer_path}") from exc
        if not isinstance(state, dict) or state.get("schema_version") != 1:
            raise ValueError(f"unsupported collection pointer schema: {pointer_path}")
        if not isinstance(state.get("collections"), dict):
            raise ValueError(f"invalid collection pointer map: {pointer_path}")

    with _POINTER_CACHE_LOCK:
        _pointer_cache[cache_key] = (signature, state)
    return json.loads(json.dumps(state))


def resolve_collection_name(logical_name: str, path: Path | None = None) -> str:
    state = read_pointer_state(path)
    record = state["collections"].get(logical_name)
    if not isinstance(record, dict):
        return logical_name
    active = str(record.get("active") or "").strip()
    return active or logical_name


def write_collection_pointer(
    logical_name: str,
    active_name: str,
    *,
    previous_name: str,
    build_id: str,
    source_artifact_sha256: str,
    path: Path | None = None,
    rollback_of: str | None = None,
) -> dict[str, Any]:
    """Atomically publish one pointer record.

    Callers must hold the shared maintenance lock.  Keeping locking outside
    this module lets activation validate the build and pointer preconditions in
    the same critical section.
    """
    pointer_path = (path or default_pointer_path()).resolve()
    state = read_pointer_state(pointer_path)
    record: dict[str, Any] = {
        "active": active_name,
        "previous": previous_name,
        "build_id": build_id,
        "source_artifact_sha256": source_artifact_sha256,
        "switched_at": datetime.now(timezone.utc).isoformat(),
    }
    if rollback_of:
        record["rollback_of"] = rollback_of
    state["collections"][logical_name] = record

    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{pointer_path.name}.",
        suffix=".tmp",
        dir=pointer_path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, pointer_path)
        directory_fd = os.open(pointer_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise

    with _POINTER_CACHE_LOCK:
        _pointer_cache.pop(str(pointer_path), None)
    return read_pointer_state(pointer_path)


def clear_pointer_cache() -> None:
    with _POINTER_CACHE_LOCK:
        _pointer_cache.clear()


__all__ = [
    "POINTER_FILE_ENV",
    "clear_pointer_cache",
    "default_pointer_path",
    "read_pointer_state",
    "resolve_collection_name",
    "write_collection_pointer",
]
