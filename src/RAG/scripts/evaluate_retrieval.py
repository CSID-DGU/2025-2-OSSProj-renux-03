from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.rag.embedder import SentenceTransformerEmbedder
from src.rag.query_intent import classify_query_intent
from src.rag.retriever import Retriever
from src.rag.vector_store import ChromaVectorStore


DEFAULT_CASES_PATH = Path(__file__).resolve().parents[1] / "evals" / "retrieval_cases.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Dongttok RAG retrieval against fixed representative queries.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--collection", default="dongguk_documents")
    parser.add_argument("--preview", type=int, default=3, help="Number of top hits to print per case.")
    args = parser.parse_args()

    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    retriever = Retriever(
        embedder=SentenceTransformerEmbedder(),
        vector_store=ChromaVectorStore(),
        collection_name=args.collection,
    )

    passed = 0
    intent_passed = 0
    top1_hits = 0
    top3_hits = 0
    mrr_total = 0.0
    rows: list[dict[str, Any]] = []
    for case in cases:
        query = case["query"]
        top_k = int(case.get("top_k", 5))
        intent = classify_query_intent(query)
        hits = retriever.search(query, top_k=top_k, intent=intent)
        match_rank = _first_match_rank(hits, case.get("expected") or {})
        ok = match_rank is not None
        expected_intent = case.get("expected_intent")
        intent_ok = expected_intent is None or intent.name == expected_intent
        passed += int(ok)
        intent_passed += int(intent_ok)
        top1_hits += int(match_rank == 1)
        top3_hits += int(match_rank is not None and match_rank <= 3)
        mrr_total += 0.0 if match_rank is None else 1.0 / match_rank
        rows.append(
            {
                "name": case["name"],
                "ok": ok,
                "match_rank": match_rank,
                "intent": intent.name,
                "expected_intent": expected_intent,
                "intent_ok": intent_ok,
                "top": [_summarize_hit(hit) for hit in hits[: args.preview]],
            }
        )

    for row in rows:
        status = "PASS" if row["ok"] else "FAIL"
        rank_label = row["match_rank"] if row["match_rank"] is not None else "-"
        if row["expected_intent"] is None:
            intent_label = row["intent"]
        else:
            intent_state = "OK" if row["intent_ok"] else "MISS"
            intent_label = f"{row['intent']} expected={row['expected_intent']} ({intent_state})"
        print(f"[{status}] {row['name']} rank={rank_label} intent={intent_label}")
        for idx, hit in enumerate(row["top"], start=1):
            print(f"  {idx}. {hit}")

    total = len(cases)
    print("\nSummary")
    print(f"  Retrieval hit rate: {passed}/{total} ({passed / total:.1%})")
    print(f"  Top-1 accuracy: {top1_hits}/{total} ({top1_hits / total:.1%})")
    print(f"  Top-3 accuracy: {top3_hits}/{total} ({top3_hits / total:.1%})")
    print(f"  MRR: {mrr_total / total:.4f}")
    print(f"  Intent accuracy: {intent_passed}/{total} ({intent_passed / total:.1%})")
    return 0 if passed == total else 1


def _first_match_rank(hits: list[dict[str, Any]], expected: dict[str, Any]) -> int | None:
    for idx, hit in enumerate(hits, start=1):
        if _matches(hit, expected):
            return idx
    return None


def _matches(hit: dict[str, Any], expected: dict[str, Any]) -> bool:
    metadata = hit.get("metadata") or {}
    checks = []

    if values := expected.get("document_type"):
        checks.append(metadata.get("document_type") in values)
    if values := expected.get("category"):
        checks.append(metadata.get("category") in values)
    if values := expected.get("sub_category"):
        checks.append(metadata.get("sub_category") in values)
    if values := expected.get("department"):
        checks.append(metadata.get("department") in values)
    if values := expected.get("url_contains"):
        url = str(metadata.get("source_url") or metadata.get("url") or "")
        checks.append(any(value in url for value in values))
    if values := expected.get("title_contains"):
        title = str(metadata.get("title") or "")
        checks.append(any(value in title for value in values))

    return all(checks) if checks else False


def _summarize_hit(hit: dict[str, Any]) -> str:
    metadata = hit.get("metadata") or {}
    return " | ".join(
        str(part or "")
        for part in [
            metadata.get("document_type"),
            metadata.get("category"),
            metadata.get("sub_category"),
            metadata.get("title"),
            metadata.get("source_url") or metadata.get("url"),
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
