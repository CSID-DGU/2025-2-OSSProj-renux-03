from __future__ import annotations

from datetime import datetime

from rapidfuzz import fuzz

from src.rag.bm25 import BM25ChunkIndex
from src.rag.embedder import BaseEmbedder
from src.rag.query_intent import QueryIntent, classify_query_intent
from src.rag.vector_store import BaseVectorStore


class Retriever:
    def __init__(self, embedder: BaseEmbedder, vector_store: BaseVectorStore, collection_name: str) -> None:
        self.embedder = embedder
        self.vector_store = vector_store
        self.collection_name = collection_name
        self.bm25_index = BM25ChunkIndex()

    def search(
        self,
        query: str,
        top_k: int,
        filters: dict | None = None,
        date_range: list[str] | None = None,
        intent: QueryIntent | None = None,
    ) -> list[dict]:
        intent = intent or classify_query_intent(query, explicit_category=(filters or {}).get("category"))
        search_query = intent.search_query or query
        effective_filters = self._merge_filters(filters, intent.hard_filters)
        candidate_top_k = max(top_k * 12, top_k)

        vector_hits = self._vector_search(search_query, candidate_top_k, effective_filters, date_range)
        bm25_hits = self.bm25_index.search(
            search_query,
            top_k=candidate_top_k,
            filters=effective_filters,
            date_range=date_range,
        )

        hits = self._merge_and_score(query, vector_hits, bm25_hits, intent)
        hits.sort(key=lambda item: (item["score"], self._published_at_sort_key(item)), reverse=True)
        return self._deduplicate_documents(hits)[:top_k]

    def _vector_search(
        self,
        query: str,
        top_k: int,
        filters: dict | None,
        date_range: list[str] | None,
    ) -> list[dict]:
        try:
            result = self.vector_store.search(
                collection_name=self.collection_name,
                query_embedding=self.embedder.embed_query(query),
                top_k=top_k,
                where=filters,
            )
        except Exception:
            if not filters:
                raise
            result = self.vector_store.search(
                collection_name=self.collection_name,
                query_embedding=self.embedder.embed_query(query),
                top_k=top_k,
                where=None,
            )

        hits: list[dict] = []
        ids = (result.get("ids") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]

        for idx, doc_id in enumerate(ids):
            metadata = metadatas[idx] or {}
            if not self._metadata_matches(metadata, filters):
                continue
            if date_range and not self._within_date_range(metadata.get("published_at"), date_range):
                continue
            distance = distances[idx] if idx < len(distances) else 1.0
            vector_score = self._distance_to_score(distance)
            hits.append(
                {
                    "id": doc_id,
                    "score": float(vector_score),
                    "vector_score": float(vector_score),
                    "content": documents[idx],
                    "metadata": metadata,
                }
            )
        return hits

    def _merge_and_score(
        self,
        query: str,
        vector_hits: list[dict],
        bm25_hits: list[dict],
        intent: QueryIntent,
    ) -> list[dict]:
        merged: dict[str, dict] = {}
        for hit in vector_hits:
            key = str(hit.get("id"))
            merged[key] = {**hit, "vector_score": float(hit.get("vector_score") or hit.get("score") or 0.0)}

        max_bm25 = max((float(hit.get("bm25_score") or hit.get("score") or 0.0) for hit in bm25_hits), default=0.0)
        for hit in bm25_hits:
            key = str(hit.get("id"))
            normalized_bm25 = (float(hit.get("bm25_score") or hit.get("score") or 0.0) / max_bm25) if max_bm25 else 0.0
            if key in merged:
                merged[key]["bm25_score"] = normalized_bm25
            else:
                merged[key] = {**hit, "vector_score": 0.0, "bm25_score": normalized_bm25}

        weights = self._score_weights(intent.score_profile)
        scored: list[dict] = []
        for hit in merged.values():
            metadata = hit.get("metadata") or {}
            content = str(hit.get("content") or "")
            title = str(metadata.get("title") or "")
            category = str(metadata.get("category") or "")
            sub_category = str(metadata.get("sub_category") or "")
            department = str(metadata.get("department") or "")
            haystack = f"{title} {category} {sub_category} {department} {content}"

            title_score = fuzz.token_set_ratio(query, title) / 100.0 if title else 0.0
            lexical_score = fuzz.token_set_ratio(query, haystack) / 100.0
            metadata_score = self._metadata_preference_score(metadata, intent)
            keyword_score = self._keyword_score(haystack, intent.keywords)
            recency_score = self._recency_score(metadata.get("published_at"))
            bm25_score = float(hit.get("bm25_score") or 0.0)
            vector_score = float(hit.get("vector_score") or 0.0)

            score = (
                vector_score * weights["vector"]
                + bm25_score * weights["bm25"]
                + max(title_score, keyword_score, lexical_score * 0.5) * weights["title"]
                + metadata_score * weights["metadata"]
                + recency_score * weights["recency"]
            )
            if intent.score_profile == "academic_policy":
                if metadata.get("document_type") == "academic":
                    score += 0.15
                elif metadata.get("document_type") == "notice":
                    score -= 0.05
            if intent.score_profile in {"notice_recency", "latest_notice", "academic_notice"}:
                if metadata.get("document_type") == "notice":
                    score += 0.05
            hit["score"] = float(score)
            scored.append(hit)
        return scored

    def _merge_filters(self, filters: dict | None, hard_filters: dict | None) -> dict | None:
        merged = dict(filters or {})
        for key, value in (hard_filters or {}).items():
            merged.setdefault(key, value)
        return merged or None

    def _metadata_matches(self, metadata: dict, filters: dict | None) -> bool:
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

    def _score_weights(self, profile: str) -> dict[str, float]:
        profiles = {
            "default": {"vector": 0.45, "bm25": 0.30, "title": 0.15, "metadata": 0.05, "recency": 0.05},
            "notice_recency": {"vector": 0.30, "bm25": 0.25, "title": 0.15, "metadata": 0.05, "recency": 0.25},
            "latest_notice": {"vector": 0.15, "bm25": 0.10, "title": 0.10, "metadata": 0.15, "recency": 0.50},
            "exact_type": {"vector": 0.10, "bm25": 0.35, "title": 0.25, "metadata": 0.25, "recency": 0.05},
            "academic_notice": {"vector": 0.35, "bm25": 0.25, "title": 0.15, "metadata": 0.10, "recency": 0.15},
            "academic_policy": {"vector": 0.25, "bm25": 0.35, "title": 0.20, "metadata": 0.15, "recency": 0.05},
            "schedule": {"vector": 0.30, "bm25": 0.30, "title": 0.15, "metadata": 0.15, "recency": 0.10},
        }
        return profiles.get(profile, profiles["default"])

    def _metadata_preference_score(self, metadata: dict, intent: QueryIntent) -> float:
        score = 0.0
        if intent.preferred_document_types and metadata.get("document_type") in intent.preferred_document_types:
            score += 0.6
        if intent.preferred_categories and metadata.get("category") in intent.preferred_categories:
            score += 0.4
        return min(score, 1.0)

    def _keyword_score(self, haystack: str, keywords: tuple[str, ...]) -> float:
        if not keywords:
            return 0.0
        return sum(1 for keyword in keywords if keyword and keyword in haystack) / len(keywords)

    def _deduplicate_documents(self, hits: list[dict]) -> list[dict]:
        deduped: list[dict] = []
        seen: set[str] = set()
        for hit in hits:
            metadata = hit.get("metadata") or {}
            key = str(metadata.get("document_id") or metadata.get("url") or hit.get("id"))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(hit)
        return deduped

    def _within_date_range(self, published_at: str | None, date_range: list[str]) -> bool:
        if not published_at or len(date_range) != 2:
            return False
        try:
            target = datetime.strptime(published_at, "%Y-%m-%d").date()
            start = datetime.strptime(date_range[0], "%Y-%m-%d").date()
            end = datetime.strptime(date_range[1], "%Y-%m-%d").date()
            return start <= target <= end
        except ValueError:
            return True

    def _distance_to_score(self, distance: float) -> float:
        try:
            value = float(distance)
        except (TypeError, ValueError):
            return 0.0
        if value < 0:
            return 0.0
        return 1.0 / (1.0 + value)

    def _recency_score(self, published_at: str | None) -> float:
        if not published_at:
            return 0.0
        try:
            published = datetime.strptime(published_at, "%Y-%m-%d").date()
        except ValueError:
            return 0.0
        age_days = max((datetime.now().date() - published).days, 0)
        return max(0.0, 1.0 - min(age_days, 365) / 365.0)

    def _published_at_sort_key(self, hit: dict) -> str:
        metadata = hit.get("metadata") or {}
        published_at = metadata.get("published_at")
        if not published_at:
            return ""
        return str(published_at)
