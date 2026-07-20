from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi.responses import JSONResponse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import rag_service  # noqa: E402


@pytest.fixture(autouse=True)
def reset_readiness_state():
    rag_service._reset_startup_readiness()
    yield
    rag_service._reset_startup_readiness()


def _successful_dataset(key: str):
    chunks = pd.DataFrame({"chunk_id": [f"{key}-1"]})
    return chunks, object(), object(), chunks["chunk_id"].tolist()


def _prepare_successful_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rag_service, "_validate_required_configuration", lambda: "ok")
    monkeypatch.setattr(rag_service, "init_db", lambda: None)
    monkeypatch.setattr(rag_service, "_ensure_dataset", _successful_dataset)
    monkeypatch.setattr(rag_service, "get_embedder", object)


def _response_payload(response: JSONResponse) -> dict:
    return json.loads(response.body.decode("utf-8"))


def test_health_is_live_while_ready_is_503_before_startup():
    assert rag_service.health() == {"status": "ok"}

    response = rag_service.ready()

    assert isinstance(response, JSONResponse)
    assert response.status_code == 503
    payload = _response_payload(response)
    assert payload["status"] == "not_ready"
    assert payload["startup_complete"] is False


def test_ready_succeeds_after_all_required_components_load(monkeypatch: pytest.MonkeyPatch):
    _prepare_successful_checks(monkeypatch)

    rag_service._run_required_startup_checks()
    payload = rag_service.ready()

    assert isinstance(payload, dict)
    assert payload["status"] == "ready"
    assert payload["startup_complete"] is True
    assert payload["checks"]["database"]["ready"] is True
    assert payload["checks"]["datasets"]["counts"] == {
        key: 1 for key in rag_service._REQUIRED_DATASETS
    }
    assert payload["checks"]["embedder"]["ready"] is True
    assert payload["checks"]["scheduler"]["required"] is False


def test_scheduler_failure_does_not_change_ready_result(monkeypatch: pytest.MonkeyPatch):
    _prepare_successful_checks(monkeypatch)
    rag_service._run_required_startup_checks()
    rag_service._set_readiness_check(
        "scheduler",
        ready=False,
        detail="failed",
        error={"code": "scheduler_start_failed"},
    )

    payload = rag_service.ready()

    assert isinstance(payload, dict)
    assert payload["status"] == "ready"
    assert payload["checks"]["scheduler"]["ready"] is False


def test_ready_reports_database_initialization_failure(monkeypatch: pytest.MonkeyPatch):
    _prepare_successful_checks(monkeypatch)

    def fail_database():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(rag_service, "init_db", fail_database)
    rag_service._run_required_startup_checks()
    response = rag_service.ready()

    assert isinstance(response, JSONResponse)
    assert response.status_code == 503
    check = _response_payload(response)["checks"]["database"]
    assert check["ready"] is False
    assert check["error"]["code"] == "database_initialization_failed"


def test_ready_reports_required_dataset_failure(monkeypatch: pytest.MonkeyPatch):
    _prepare_successful_checks(monkeypatch)
    failed_dataset = rag_service._REQUIRED_DATASETS[0]

    def load_dataset(key: str):
        if key == failed_dataset:
            raise FileNotFoundError("dataset artifact missing")
        return _successful_dataset(key)

    monkeypatch.setattr(rag_service, "_ensure_dataset", load_dataset)
    rag_service._run_required_startup_checks()
    response = rag_service.ready()

    assert isinstance(response, JSONResponse)
    assert response.status_code == 503
    check = _response_payload(response)["checks"]["datasets"]
    assert check["ready"] is False
    assert check["errors"][failed_dataset]["code"] == "required_dataset_warmup_failed"


def test_ready_reports_embedder_failure(monkeypatch: pytest.MonkeyPatch):
    _prepare_successful_checks(monkeypatch)

    def fail_embedder():
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(rag_service, "get_embedder", fail_embedder)
    rag_service._run_required_startup_checks()
    response = rag_service.ready()

    assert isinstance(response, JSONResponse)
    assert response.status_code == 503
    check = _response_payload(response)["checks"]["embedder"]
    assert check["ready"] is False
    assert check["error"]["code"] == "embedder_warmup_failed"
