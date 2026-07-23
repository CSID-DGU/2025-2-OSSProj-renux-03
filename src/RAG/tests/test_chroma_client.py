from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import chromadb
import pytest

from src.vectorstore import chroma_client  # noqa: E402


def test_client_is_initialized_once_under_concurrent_first_access(monkeypatch):
    created = []
    client = object()

    def fake_persistent_client(*, path):
        assert path
        created.append(path)
        time.sleep(0.02)
        return client

    monkeypatch.setattr(chroma_client, "_client_instance", None)
    monkeypatch.setattr(chroma_client.chromadb, "PersistentClient", fake_persistent_client)
    with ThreadPoolExecutor(max_workers=8) as executor:
        instances = list(executor.map(lambda _item: chroma_client.get_client(), range(16)))

    assert all(instance is client for instance in instances)
    assert len(created) == 1


def test_existing_collection_is_read_only_lookup_and_cached(monkeypatch):
    collection = object()

    class FakeClient:
        get_calls = 0
        create_calls = 0

        def get_collection(self, *, name):
            self.get_calls += 1
            assert name == "existing"
            return collection

        def create_collection(self, **_kwargs):
            self.create_calls += 1
            return object()

    client = FakeClient()
    monkeypatch.setattr(chroma_client, "get_client", lambda: client)
    chroma_client.get_collection.cache_clear()
    try:
        assert chroma_client.get_collection("existing") is collection
        assert chroma_client.get_collection("existing") is collection
    finally:
        chroma_client.get_collection.cache_clear()

    assert client.get_calls == 1
    assert client.create_calls == 0


def test_missing_collection_is_not_created_by_read_lookup(monkeypatch):
    class FakeClient:
        create_calls = 0

        def get_collection(self, *, name):
            assert name == "missing-read"
            raise chromadb.errors.NotFoundError("missing")

        def create_collection(self, **_kwargs):
            self.create_calls += 1
            return object()

    client = FakeClient()
    monkeypatch.setattr(chroma_client, "get_client", lambda: client)
    chroma_client.get_collection.cache_clear()
    try:
        with pytest.raises(chromadb.errors.NotFoundError):
            chroma_client.get_collection("missing-read")
    finally:
        chroma_client.get_collection.cache_clear()

    assert client.create_calls == 0


def test_missing_collection_is_created_once_for_ingestion_then_cached(monkeypatch):
    collection = object()

    class FakeClient:
        get_calls = 0
        create_calls = 0

        def get_collection(self, *, name):
            self.get_calls += 1
            if self.create_calls == 0:
                raise chromadb.errors.NotFoundError("missing")
            return collection

        def create_collection(self, *, name, metadata):
            self.create_calls += 1
            assert name == "new"
            assert metadata == chroma_client._COLLECTION_METADATA
            return collection

    client = FakeClient()
    monkeypatch.setattr(chroma_client, "get_client", lambda: client)
    chroma_client.get_collection.cache_clear()
    try:
        assert chroma_client.get_collection("new", True) is collection
        assert chroma_client.get_collection("new", True) is collection
    finally:
        chroma_client.get_collection.cache_clear()

    assert client.create_calls == 1


def test_count_recovers_from_collection_rebuilt_behind_cached_handle(monkeypatch):
    class StaleCollection:
        def count(self):
            raise chromadb.errors.NotFoundError("stale collection id")

    class LiveCollection:
        def count(self):
            return 42

    class FakeClient:
        get_calls = 0

        def get_collection(self, *, name):
            assert name == "rebuilt"
            self.get_calls += 1
            return StaleCollection() if self.get_calls == 1 else LiveCollection()

    client = FakeClient()
    monkeypatch.setattr(chroma_client, "get_client", lambda: client)
    chroma_client.get_collection.cache_clear()
    try:
        assert chroma_client.count_items("rebuilt") == 42
    finally:
        chroma_client.get_collection.cache_clear()

    assert client.get_calls == 2
