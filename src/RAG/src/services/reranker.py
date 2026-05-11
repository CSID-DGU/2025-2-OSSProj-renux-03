from __future__ import annotations

import math

import pandas as pd
from rapidfuzz import fuzz
from sklearn.metrics.pairwise import cosine_similarity

from src.models.embedding import encode_texts


def rerank_hits(query: str, hits: pd.DataFrame, top_n: int) -> pd.DataFrame:
    if hits.empty:
        return hits

    texts = hits["chunk_text"].fillna("").astype(str).tolist()
    query_vec = encode_texts([query])
    doc_vecs = encode_texts(texts)
    semantic_scores = cosine_similarity(query_vec, doc_vecs).ravel()

    reranked = hits.copy().reset_index(drop=True)
    reranked["semantic_rerank"] = semantic_scores
    reranked["lexical_rerank"] = [
        fuzz.token_set_ratio(query, text) / 100.0
        for text in texts
    ]

    if "published_at" in reranked.columns:
        published = pd.to_datetime(reranked["published_at"], errors="coerce")
        if published.notna().any():
            latest = published.max()
            age_days = (latest - published).dt.days.fillna(3650)
            reranked["recency_rerank"] = age_days.apply(lambda days: math.exp(-max(days, 0) / 365.0))
        else:
            reranked["recency_rerank"] = 0.0
    else:
        reranked["recency_rerank"] = 0.0

    hybrid = reranked["hybrid_score"] if "hybrid_score" in reranked.columns else 0.0
    reranked["rerank_score"] = (
        0.5 * reranked["semantic_rerank"]
        + 0.2 * reranked["lexical_rerank"]
        + 0.2 * hybrid
        + 0.1 * reranked["recency_rerank"]
    )
    reranked.sort_values(by="rerank_score", ascending=False, inplace=True)
    return reranked.head(top_n).reset_index(drop=True)
