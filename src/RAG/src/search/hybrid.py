"""Dense retrieval with lightweight lexical reranking."""
from __future__ import annotations

from typing import Dict

import pandas as pd
from rapidfuzz import fuzz

from src.config import DEFAULT_TOP_K
from src.models.embedding import encode_texts
from src.vectorstore.chroma_client import get_collection


def hybrid_search(
    collection_name: str,
    chunks_df: pd.DataFrame,
    query: str,
    lexical_artifact=None,
    sparse_matrix=None,
    top_k: int = DEFAULT_TOP_K,
    alpha: float = 0.7,
    where_filter: Dict | None = None,
) -> pd.DataFrame:
    if chunks_df.empty:
        return chunks_df.copy()

    limit = max(top_k * 4, top_k)
    collection = get_collection(collection_name)
    query_embedding = encode_texts([query])

    vec_results = collection.query(
        query_embeddings=query_embedding,
        n_results=limit,
        where=where_filter,
        include=["documents", "metadatas", "distances"],
    )

    vec_ids = (vec_results.get("ids") or [[]])[0]
    vec_dists = (vec_results.get("distances") or [[]])[0]

    if not vec_ids:
        return chunks_df.iloc[:0].copy()

    df_indexed = chunks_df.set_index("chunk_id")
    rows = []
    for cid, dist in zip(vec_ids, vec_dists):
        if cid not in df_indexed.index:
            continue
        row = df_indexed.loc[cid].copy()
        dense_score = 1 - dist
        lexical_score = fuzz.token_set_ratio(query, str(row.get("chunk_text", ""))) / 100.0
        row["dense_score"] = dense_score
        row["lexical_score"] = lexical_score
        row["hybrid_score"] = alpha * dense_score + (1.0 - alpha) * lexical_score
        rows.append(row)

    if not rows:
        return chunks_df.iloc[:0].copy()

    result_df = pd.DataFrame(rows).reset_index()
    result_df.sort_values(by="hybrid_score", ascending=False, inplace=True)
    return result_df.head(top_k).reset_index(drop=True)


def hybrid_search_with_meta(
    collection_name: str,
    chunks_df: pd.DataFrame,
    query: str,
    lexical_artifact=None,
    sparse_matrix=None,
    top_k: int = DEFAULT_TOP_K,
    alpha: float = 0.7,
    where_filter: Dict | None = None,
) -> pd.DataFrame:
    return hybrid_search(
        collection_name=collection_name,
        chunks_df=chunks_df,
        lexical_artifact=lexical_artifact,
        sparse_matrix=sparse_matrix,
        query=query,
        top_k=top_k,
        alpha=alpha,
        where_filter=where_filter,
    )


__all__ = ["hybrid_search", "hybrid_search_with_meta"]
