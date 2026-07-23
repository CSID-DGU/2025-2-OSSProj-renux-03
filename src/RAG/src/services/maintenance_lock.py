"""Process-shared lock for scheduler, admin, and index maintenance work."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
from typing import Iterator, TextIO

from src.config import ARTIFACT_DIR


MAINTENANCE_LOCK_ENV = "RAG_MAINTENANCE_LOCK_FILE"


class MaintenanceLockBusy(RuntimeError):
    pass


def default_maintenance_lock_path() -> Path:
    configured = os.getenv(MAINTENANCE_LOCK_ENV)
    return Path(configured) if configured else ARTIFACT_DIR / ".rag-maintenance.lock"


@contextmanager
def maintenance_lock(
    *,
    path: Path | None = None,
    blocking: bool = False,
) -> Iterator[TextIO]:
    """Hold the common maintenance lock for one critical operation."""
    lock_path = (path or default_maintenance_lock_path()).resolve()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    operation = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
    try:
        try:
            fcntl.flock(handle.fileno(), operation)
        except BlockingIOError as exc:
            raise MaintenanceLockBusy(f"RAG maintenance lock is busy: {lock_path}") from exc
        yield handle
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


__all__ = [
    "MAINTENANCE_LOCK_ENV",
    "MaintenanceLockBusy",
    "default_maintenance_lock_path",
    "maintenance_lock",
]
