"""Scheduler refreshes must invalidate the same process-local RAG cache."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services import scheduler  # noqa: E402


def test_scheduler_runtime_refresh_delegates_to_rag_service(monkeypatch):
    calls: list[list[str]] = []

    class FakeRagService:
        @staticmethod
        def refresh_runtime_dataset_state(targets):
            calls.append(targets)
            return {"counts": {"notices": 7}, "dense_counts": {"notices": 7}}

    monkeypatch.setitem(sys.modules, "api.rag_service", FakeRagService)

    scheduler._refresh_runtime_dataset_state("notices")

    assert calls == [["notices"]]
