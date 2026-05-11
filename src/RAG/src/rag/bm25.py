from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Iterable

from src.config import PROCESSED_DATA_DIR


TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")


class BM25ChunkIndex:
    def __init__(self, chunks_dir: Path | None = None) -> None:
        self.chunks_dir = chunks_dir or PROCESSED_DATA_DIR
        self._static = False
        self._signature: tuple[tuple[str, float, int], ...] = ()
        self._chunks: list[dict] = []
        self._tokenized: list[list[str]] = []
        self._doc_freq: Counter[str] = Counter()
        self._avgdl = 0.0

    @classmethod
    def from_chunks(cls, chunks: Iterable[dict]) -> "BM25ChunkIndex":
        index = cls()
        index._static = True
        index._rebuild(list(chunks))
        return index

    def search(
        self,
        query: str,
        top_k: int,
        filters: dict | None = None,
        date_range: list[str] | None = None,
    ) -> list[dict]:
        self._ensure_loaded()
        query_tokens = tokenize(query)
        if not query_tokens or not self._chunks:
            return []

        scored: list[dict] = []
        for idx, chunk in enumerate(self._chunks):
            metadata = chunk.get("metadata") or {}
            if not _metadata_matches(metadata, filters):
                continue
            if date_range and not _within_date_range(metadata.get("published_at"), date_range):
                continue

            if len(query_tokens) >= 2 and self._match_count(query_tokens, idx) < 2:
                continue
            score = self._score(query_tokens, idx)
            if score <= 0:
                continue
            scored.append(
                {
                    "id": chunk.get("id", ""),
                    "score": float(score),
                    "content": chunk.get("content", ""),
                    "metadata": metadata,
                    "bm25_score": float(score),
                }
            )

        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored[:top_k]

    def _ensure_loaded(self) -> None:
        if self._static:
            return
        signature = self._current_signature()
        if signature == self._signature:
            return
        chunks: list[dict] = []
        for path_str, _, _ in signature:
            path = Path(path_str)
            with path.open("r", encoding="utf-8") as file:
                for line in file:
                    if not line.strip():
                        continue
                    try:
                        chunks.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        self._signature = signature
        self._rebuild(chunks)

    def _current_signature(self) -> tuple[tuple[str, float, int], ...]:
        if not self.chunks_dir.exists():
            return ()
        paths = sorted(self.chunks_dir.glob("*_chunks.jsonl"))
        return tuple((str(path), path.stat().st_mtime, path.stat().st_size) for path in paths)

    def _rebuild(self, chunks: list[dict]) -> None:
        self._chunks = chunks
        self._tokenized = []
        self._doc_freq = Counter()

        total_length = 0
        for chunk in chunks:
            metadata = chunk.get("metadata") or {}
            searchable_text = " ".join(
                str(value or "")
                for value in (
                    metadata.get("title"),
                    metadata.get("category"),
                    metadata.get("sub_category"),
                    metadata.get("department"),
                    chunk.get("content"),
                )
            )
            tokens = tokenize(searchable_text)
            self._tokenized.append(tokens)
            total_length += len(tokens)
            self._doc_freq.update(set(tokens))

        self._avgdl = total_length / len(chunks) if chunks else 0.0

    def _score(self, query_tokens: list[str], doc_index: int) -> float:
        tokens = self._tokenized[doc_index]
        if not tokens:
            return 0.0

        term_counts = Counter(tokens)
        doc_len = len(tokens)
        total_docs = len(self._tokenized)
        k1 = 1.5
        b = 0.75
        score = 0.0
        for token in query_tokens:
            freq = term_counts.get(token, 0)
            if freq == 0:
                continue
            df = self._doc_freq.get(token, 0)
            idf = math.log(1 + (total_docs - df + 0.5) / (df + 0.5))
            denom = freq + k1 * (1 - b + b * doc_len / max(self._avgdl, 1.0))
            score += idf * (freq * (k1 + 1)) / denom
        return score

    def _match_count(self, query_tokens: list[str], doc_index: int) -> int:
        token_set = set(self._tokenized[doc_index])
        return sum(1 for token in set(query_tokens) if token in token_set)


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text or "") if len(token) > 1]


def _metadata_matches(metadata: dict, filters: dict | None) -> bool:
    if not filters:
        return True
    for key, expected in filters.items():
        actual = metadata.get(key)
        if isinstance(expected, dict):
            if "$eq" in expected and actual != expected["$eq"]:
                return False
            if "$in" in expected and actual not in expected["$in"]:
                return False
            continue
        if isinstance(expected, (list, tuple, set)):
            if actual not in expected:
                return False
            continue
        if actual != expected:
            return False
    return True


def _within_date_range(published_at: str | None, date_range: list[str]) -> bool:
    if not published_at or len(date_range) != 2:
        return True
    return date_range[0] <= published_at <= date_range[1]
