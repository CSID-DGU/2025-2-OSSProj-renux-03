from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.models import embedding  # noqa: E402


def test_query_embedding_is_reused_across_routerless_dataset_searches(monkeypatch):
    class FakeEmbedder:
        calls = 0

        def encode(self, texts, **_kwargs):
            self.calls += 1
            return np.asarray([[float(len(texts[0])), 1.0]], dtype=np.float32)

    fake = FakeEmbedder()
    monkeypatch.setattr(embedding, "get_embedder", lambda: fake)
    embedding.clear_query_embedding_cache()
    try:
        first = embedding.encode_queries(["같은 질문"])
        second = embedding.encode_queries(["같은 질문"])
    finally:
        embedding.clear_query_embedding_cache()

    assert fake.calls == 1
    assert np.array_equal(first, second)
    assert first is not second
