from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.maintenance_lock import MaintenanceLockBusy, maintenance_lock  # noqa: E402
from src.services.notices_dense_rebuild import (  # noqa: E402
    ArtifactChangedError,
    BuildVerificationError,
    GracefulStop,
    LOGICAL_COLLECTION,
    NOTICE_REPRESENTATIVE_QUERIES,
    activate_notice_dense_build,
    build_notice_dense_index,
    rollback_notice_dense_pointer,
)
from src.vectorstore.collection_pointer import (  # noqa: E402
    clear_pointer_cache,
    resolve_collection_name,
    write_collection_pointer,
)


class FakeDenseStore:
    def __init__(self) -> None:
        self.collections: dict[str, dict[str, dict]] = {}

    def collection_exists(self, name: str) -> bool:
        return name in self.collections

    def ensure_collection(self, name: str, metadata) -> None:
        self.collections.setdefault(name, {})

    def count(self, name: str) -> int:
        return len(self.collections[name])

    def ids(self, name: str) -> list[str]:
        return list(self.collections[name])

    def upsert(self, name, *, ids, documents, metadatas, embeddings) -> None:
        collection = self.collections.setdefault(name, {})
        for index, chunk_id in enumerate(ids):
            collection[str(chunk_id)] = {
                "document": str(documents[index]),
                "metadata": dict(metadatas[index]),
                "embedding": np.asarray(embeddings[index], dtype=np.float32),
            }

    def embedding_dimensions(self, name: str, *, batch_size: int = 500):
        items = self.collections[name].values()
        return {len(item["embedding"]) for item in items}, len(self.collections[name])

    def query(self, name: str, *, query_embeddings, n_results: int):
        ids = list(self.collections[name])[:n_results]
        return {
            "ids": [list(ids) for _ in query_embeddings],
            "distances": [[0.1 for _ in ids] for _ in query_embeddings],
        }

    def digest(self, name: str) -> str:
        digest = hashlib.sha256()
        for chunk_id, item in sorted(self.collections[name].items()):
            digest.update(chunk_id.encode())
            digest.update(item["document"].encode())
            digest.update(item["embedding"].tobytes())
        return digest.hexdigest()


def _artifact(tmp_path: Path, count: int = 7) -> Path:
    path = tmp_path / "notices.csv"
    pd.DataFrame(
        {
            "chunk_id": [f"notice-{index}" for index in range(count)],
            "chunk_text": [f"공지 본문 {index}" for index in range(count)],
            "title": [f"공지 {index}" for index in range(count)],
            "source": ["notices"] * count,
        }
    ).to_csv(path, index=False)
    return path


def _encode(texts) -> np.ndarray:
    values = list(texts)
    return np.asarray(
        [[float(index + 1), float(len(value)), 0.25, 0.5] for index, value in enumerate(values)],
        dtype=np.float32,
    )


def _build(
    *,
    artifact: Path,
    build_id: str,
    checkpoint_dir: Path,
    store: FakeDenseStore,
    should_stop=lambda: False,
):
    return build_notice_dense_index(
        artifact_path=artifact,
        build_id=build_id,
        batch_size=2,
        checkpoint_dir=checkpoint_dir,
        store=store,
        encode_documents=_encode,
        encode_representative_queries=_encode,
        representative_queries=("대표 질문",),
        should_stop=should_stop,
        enforce_batch_range=False,
    )


def test_interrupted_resume_matches_uninterrupted_id_set_and_hash(tmp_path: Path):
    artifact = _artifact(tmp_path)
    resumed_store = FakeDenseStore()
    stop_checks = 0

    def stop_after_one_batch() -> bool:
        nonlocal stop_checks
        stop_checks += 1
        return stop_checks > 1

    paused = _build(
        artifact=artifact,
        build_id="resumed",
        checkpoint_dir=tmp_path / "resumed-checkpoints",
        store=resumed_store,
        should_stop=stop_after_one_batch,
    )
    assert paused["status"] == "paused"
    assert paused["completed_count"] == 2

    resumed = _build(
        artifact=artifact,
        build_id="resumed",
        checkpoint_dir=tmp_path / "resumed-checkpoints",
        store=resumed_store,
    )
    uninterrupted_store = FakeDenseStore()
    uninterrupted = _build(
        artifact=artifact,
        build_id="uninterrupted",
        checkpoint_dir=tmp_path / "uninterrupted-checkpoints",
        store=uninterrupted_store,
    )

    assert resumed["status"] == uninterrupted["status"] == "verified"
    assert set(resumed_store.ids(resumed["build_collection"])) == set(
        uninterrupted_store.ids(uninterrupted["build_collection"])
    )
    assert resumed_store.digest(resumed["build_collection"]) == uninterrupted_store.digest(
        uninterrupted["build_collection"]
    )
    assert resumed["verification"]["actual_ids_sha256"] == uninterrupted["verification"]["actual_ids_sha256"]


def test_build_never_mutates_active_collection_and_reads_continue(tmp_path: Path):
    artifact = _artifact(tmp_path, count=5)
    store = FakeDenseStore()
    store.collections[LOGICAL_COLLECTION] = {
        "active-1": {"document": "기존 검색 문서", "metadata": {}, "embedding": np.ones(4)}
    }
    active_digest = store.digest(LOGICAL_COLLECTION)
    observed_active_counts: list[int] = []

    def encode_while_searching(texts):
        observed_active_counts.append(store.count(LOGICAL_COLLECTION))
        assert store.digest(LOGICAL_COLLECTION) == active_digest
        return _encode(texts)

    result = build_notice_dense_index(
        artifact_path=artifact,
        build_id="concurrent-safe",
        batch_size=2,
        checkpoint_dir=tmp_path / "checkpoints",
        store=store,
        encode_documents=encode_while_searching,
        encode_representative_queries=_encode,
        representative_queries=("대표 질문",),
        enforce_batch_range=False,
    )

    assert result["status"] == "verified"
    assert observed_active_counts == [1, 1, 1]
    assert store.count(LOGICAL_COLLECTION) == 1
    assert store.digest(LOGICAL_COLLECTION) == active_digest


def test_resume_refuses_changed_source_artifact(tmp_path: Path):
    artifact = _artifact(tmp_path, count=4)
    store = FakeDenseStore()
    stop_checks = 0

    def pause() -> bool:
        nonlocal stop_checks
        stop_checks += 1
        return stop_checks > 1

    _build(
        artifact=artifact,
        build_id="immutable-input",
        checkpoint_dir=tmp_path / "checkpoints",
        store=store,
        should_stop=pause,
    )
    frame = pd.read_csv(artifact)
    frame.loc[len(frame)] = ["notice-new", "변경 본문", "변경 공지", "notices"]
    frame.to_csv(artifact, index=False)

    with pytest.raises(ArtifactChangedError):
        _build(
            artifact=artifact,
            build_id="immutable-input",
            checkpoint_dir=tmp_path / "checkpoints",
            store=store,
        )


def test_verification_rejects_wrong_embedding_dimension(tmp_path: Path):
    artifact = _artifact(tmp_path, count=3)
    store = FakeDenseStore()
    result = _build(
        artifact=artifact,
        build_id="dimension-check",
        checkpoint_dir=tmp_path / "checkpoints",
        store=store,
    )
    first = next(iter(store.collections[result["build_collection"]].values()))
    first["embedding"] = np.ones(3, dtype=np.float32)

    from src.services.notices_dense_rebuild import verify_notice_dense_build

    with pytest.raises(BuildVerificationError, match="embedding_dimension_mismatch"):
        verify_notice_dense_build(
            build_id="dimension-check",
            checkpoint_dir=tmp_path / "checkpoints",
            store=store,
            encode_representative_queries=_encode,
            representative_queries=("대표 질문",),
        )


def test_activation_is_explicit_atomic_and_rollback_keeps_both_collections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    pointer_path = tmp_path / "pointers.json"
    lock_path = tmp_path / "maintenance.lock"
    monkeypatch.setenv("RAG_COLLECTION_POINTER_FILE", str(pointer_path))
    clear_pointer_cache()
    artifact = _artifact(tmp_path, count=4)
    store = FakeDenseStore()
    # The real pre-build notices collection is currently a valid 0-vector,
    # sparse-degraded rollback target.
    store.collections[LOGICAL_COLLECTION] = {}
    checkpoint_dir = tmp_path / "checkpoints"
    built = _build(
        artifact=artifact,
        build_id="switchable",
        checkpoint_dir=checkpoint_dir,
        store=store,
    )
    target = built["build_collection"]

    with pytest.raises(Exception, match="confirmation"):
        activate_notice_dense_build(
            build_id="switchable",
            confirm_build_id="wrong",
            checkpoint_dir=checkpoint_dir,
            pointer_path=pointer_path,
            lock_path=lock_path,
            store=store,
            encode_representative_queries=_encode,
            representative_queries=("대표 질문",),
        )
    assert resolve_collection_name(LOGICAL_COLLECTION, pointer_path) == LOGICAL_COLLECTION

    activated = activate_notice_dense_build(
        build_id="switchable",
        confirm_build_id="switchable",
        checkpoint_dir=checkpoint_dir,
        pointer_path=pointer_path,
        lock_path=lock_path,
        store=store,
        encode_representative_queries=_encode,
        representative_queries=("대표 질문",),
    )
    assert activated["active_collection"] == target
    assert resolve_collection_name(LOGICAL_COLLECTION, pointer_path) == target
    assert store.collection_exists(LOGICAL_COLLECTION)
    assert store.collection_exists(target)

    rolled_back = rollback_notice_dense_pointer(
        confirm_active_collection=target,
        pointer_path=pointer_path,
        lock_path=lock_path,
        store=store,
    )
    assert rolled_back["active_collection"] == LOGICAL_COLLECTION
    assert resolve_collection_name(LOGICAL_COLLECTION, pointer_path) == LOGICAL_COLLECTION
    assert store.collection_exists(target)
    clear_pointer_cache()


def test_activation_refuses_when_shared_maintenance_lock_is_busy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    pointer_path = tmp_path / "pointers.json"
    lock_path = tmp_path / "maintenance.lock"
    monkeypatch.setenv("RAG_COLLECTION_POINTER_FILE", str(pointer_path))
    clear_pointer_cache()
    artifact = _artifact(tmp_path, count=2)
    store = FakeDenseStore()
    store.collections[LOGICAL_COLLECTION] = {
        "old-1": {"document": "기존", "metadata": {}, "embedding": np.ones(4)}
    }
    checkpoint_dir = tmp_path / "checkpoints"
    _build(
        artifact=artifact,
        build_id="locked",
        checkpoint_dir=checkpoint_dir,
        store=store,
    )

    with maintenance_lock(path=lock_path):
        with pytest.raises(MaintenanceLockBusy):
            activate_notice_dense_build(
                build_id="locked",
                confirm_build_id="locked",
                checkpoint_dir=checkpoint_dir,
                pointer_path=pointer_path,
                lock_path=lock_path,
                store=store,
                encode_representative_queries=_encode,
                representative_queries=("대표 질문",),
            )
    clear_pointer_cache()


def test_sigterm_handler_requests_checkpointed_pause(tmp_path: Path):
    artifact = _artifact(tmp_path, count=3)
    store = FakeDenseStore()
    stop = GracefulStop()
    stop._handle(15, None)
    result = _build(
        artifact=artifact,
        build_id="sigterm",
        checkpoint_dir=tmp_path / "checkpoints",
        store=store,
        should_stop=lambda: stop.requested,
    )
    assert result["status"] == "paused"
    assert result["completed_count"] == 0
    assert result["failed_ids"] == []


def test_production_representative_query_contract_has_twenty_queries():
    assert len(NOTICE_REPRESENTATIVE_QUERIES) == 20
    assert len(set(NOTICE_REPRESENTATIVE_QUERIES)) == 20


def test_chroma_logical_name_resolves_new_pointer_without_restarting_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from src.vectorstore import chroma_client

    pointer_path = tmp_path / "pointers.json"
    monkeypatch.setenv("RAG_COLLECTION_POINTER_FILE", str(pointer_path))
    clear_pointer_cache()
    resolved: list[str] = []

    def fake_physical_collection(name: str, create_if_missing: bool = False):
        resolved.append(name)
        return name

    monkeypatch.setattr(chroma_client, "_get_physical_collection", fake_physical_collection)
    assert chroma_client.get_collection(LOGICAL_COLLECTION) == LOGICAL_COLLECTION
    write_collection_pointer(
        LOGICAL_COLLECTION,
        "dongguk_notices__build__next",
        previous_name=LOGICAL_COLLECTION,
        build_id="next",
        source_artifact_sha256="a" * 64,
        path=pointer_path,
    )
    assert chroma_client.get_collection(LOGICAL_COLLECTION) == "dongguk_notices__build__next"
    assert resolved == [LOGICAL_COLLECTION, "dongguk_notices__build__next"]
    clear_pointer_cache()
