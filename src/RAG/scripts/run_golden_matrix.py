"""Run the complete golden matrix against a real HTTP RAG candidate.

This file does not score or synthesize results.  Every result field is either a
literal HTTP response value or a deterministic hash/normalization of it.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from golden_matrix import TAXONOMY_VERSION, file_sha256, load_matrix, load_taxonomy, validate_matrix


RUNNER_VERSION = "real-http-v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_hash(value: Any) -> str:
    return _sha256_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())


def _git(repo_root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=repo_root, stderr=subprocess.DEVNULL, text=True, timeout=10
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


def _data_manifest(rag_root: Path) -> tuple[str, list[dict[str, Any]]]:
    candidates = [rag_root / "artifacts" / "vectorizers" / "manifest.json"]
    candidates.extend(sorted((rag_root / "artifacts" / "chunks").glob("*.parquet")))
    files = []
    for path in candidates:
        if path.is_file():
            files.append(
                {
                    "path": path.relative_to(rag_root).as_posix(),
                    "sha256": file_sha256(path),
                    "bytes": path.stat().st_size,
                }
            )
    return _canonical_hash(files), files


def _candidate_config(rag_root: Path) -> dict[str, Any]:
    if str(rag_root) not in sys.path:
        sys.path.insert(0, str(rag_root))
    from src import config  # noqa: PLC0415

    return {
        "llm_provider": config.LLM_PROVIDER,
        "query_analysis_model": config.OPENAI_MODEL,
        "answer_model": config.OPENAI_CHAT_MODEL if config.LLM_PROVIDER == "openai" else config.OLLAMA_CHAT_MODEL,
        "embedding_model": config.EMBED_MODEL_NAME,
        "embedding_revision": config.EMBED_MODEL_REVISION,
        "reranker_enabled": config.RERANKER_ENABLED,
        "reranker_model": config.RERANKER_MODEL if config.RERANKER_ENABLED else None,
        "top_k": config.DEFAULT_TOP_K,
    }


def _request_json(url: str, payload: dict[str, Any] | None, timeout: float, headers: dict[str, str]) -> tuple[int, dict]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_headers = {"Accept": "application/json", **headers}
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=request_headers, method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return response.status, json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw)
        except json.JSONDecodeError:
            detail = {"error": raw[:1000]}
        return exc.code, detail


def _metadata_value(metadata: dict, *keys: str) -> str | None:
    for key in keys:
        value = metadata.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _normalize_source(source: dict[str, Any]) -> dict[str, Any]:
    metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
    snippet = str(source.get("snippet") or "")
    snippet_hash = _sha256_bytes(snippet.encode("utf-8"))
    dataset = str(source.get("source") or "").strip()
    campus_scope = str(metadata.get("campus_scope") or "unknown").strip().lower()
    if campus_scope not in {"seoul", "bmc", "wise", "shared", "unknown"}:
        campus_scope = "unknown"
    chunk_id = str(source.get("chunk_id") or metadata.get("chunk_id") or "").strip() or None
    url = str(source.get("url") or metadata.get("url") or "").strip() or None
    published_at = str(source.get("published_at") or metadata.get("published_at") or "").strip() or None
    effective_date = _metadata_value(
        metadata, "effective_date", "schedule_start", "schedule_end", "apply_deadline", "updated_at", "sort_date"
    ) or (str(source.get("sort_date") or "").strip() or None)
    source_type = str(metadata.get("source_type") or dataset).strip()
    locator = _metadata_value(
        metadata, "document_key", "notice_id", "source_file", "filename", "schedule_id", "staff_id"
    )
    identity = {
        "dataset": dataset,
        "chunk_id": chunk_id,
        "url": url,
        "campus_scope": campus_scope,
        "published_at": published_at,
        "effective_date": effective_date,
        "snippet_hash": snippet_hash,
    }
    return {
        "id": f"sha256:{_canonical_hash(identity)}",
        "dataset": dataset,
        "source_type": source_type,
        "chunk_id": chunk_id,
        "url": url,
        "campus_scope": campus_scope,
        "published_at": published_at,
        "effective_date": effective_date,
        "snippet": snippet,
        "snippet_hash": snippet_hash,
        "citation_number": source.get("citation_number"),
        "locator": locator,
    }


def _result_from_response(case, status: int, response: dict, latency_ms: int) -> dict[str, Any]:
    if status < 200 or status >= 300:
        return {
            "schema_version": 2,
            "status": "http_error",
            "id": case.id,
            "question_hash": _sha256_bytes(case.question.encode("utf-8")),
            "response_received": False,
            "http_status": status,
            "latency_ms": latency_ms,
            "request_id": None,
            "actual_app_intents": [],
            "answer": "",
            "citations_text": "",
            "sources": [],
            "followups": [],
            "fallback_triggered": False,
            "fallback_reason": None,
            "grounded": None,
            "grounding_score": None,
            "error": json.dumps(response, ensure_ascii=False)[:2000],
        }
    sources = [_normalize_source(item) for item in response.get("sources", []) if isinstance(item, dict)]
    # The current transport exposes suggested questions as strings only.  Empty
    # source_refs truthfully records that no grounding lineage was transported.
    followups = [
        {"question": str(question), "source_refs": []}
        for question in response.get("suggested_questions", [])
        if str(question).strip()
    ]
    return {
        "schema_version": 2,
        "status": "ok",
        "id": case.id,
        "question_hash": _sha256_bytes(case.question.encode("utf-8")),
        "response_received": True,
        "http_status": status,
        "latency_ms": latency_ms,
        "request_id": str(response.get("request_id") or "") or None,
        "actual_app_intents": [str(item) for item in response.get("route", [])],
        "answer": str(response.get("answer") or ""),
        "citations_text": str(response.get("citations") or ""),
        "sources": sources,
        "followups": followups,
        "fallback_triggered": bool(response.get("fallback_triggered", False)),
        "fallback_reason": response.get("fallback_reason"),
        "grounded": response.get("grounded"),
        "grounding_score": response.get("grounding_score"),
        "error": None,
    }


def _run_case(case, ask_url: str, timeout: float, headers: dict[str, str]) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        status, response = _request_json(
            ask_url,
            {"question": case.question, "sessionId": f"golden-{uuid.uuid4()}"},
            timeout,
            headers,
        )
        return _result_from_response(case, status, response, round((time.perf_counter() - started) * 1000))
    except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        return _result_from_response(
            case,
            0,
            {"error": f"{type(exc).__name__}: {exc}"},
            round((time.perf_counter() - started) * 1000),
        )


def main(argv: list[str] | None = None) -> int:
    rag_root = Path(__file__).resolve().parents[1]
    repo_root = rag_root.parents[1]
    parser = argparse.ArgumentParser(description="Run real HTTP responses for every golden case")
    parser.add_argument("--base-url", default=os.getenv("RAG_GOLDEN_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--matrix", type=Path, default=rag_root / "tests" / "golden_matrix.csv")
    parser.add_argument("--taxonomy", type=Path, default=rag_root / "tests" / "golden_taxonomy.v1.json")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--case-id", action="append", default=[], help="Troubleshooting only; subset runs are never complete")
    parser.add_argument("--header", action="append", default=[], help="Extra HTTP header as Name=Value; values are not stored")
    args = parser.parse_args(argv)

    try:
        taxonomy = load_taxonomy(args.taxonomy)
        all_cases = load_matrix(args.matrix)
        errors = validate_matrix(all_cases, taxonomy)
        if errors:
            raise ValueError("; ".join(errors))
        headers = dict(item.split("=", 1) for item in args.header)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Runner contract error: {exc}", file=sys.stderr)
        return 2

    selected_ids = set(args.case_id)
    cases = [case for case in all_cases if not selected_ids or case.id in selected_ids]
    if selected_ids - {case.id for case in all_cases}:
        print(f"Unknown case IDs: {sorted(selected_ids - {case.id for case in all_cases})}", file=sys.stderr)
        return 2
    base_url = args.base_url.rstrip("/")
    ask_url = f"{base_url}/ask"
    run_id = str(uuid.uuid4())
    started_at = _utc_now()
    try:
        ready_status, ready_payload = _request_json(f"{base_url}/ready", None, min(args.timeout, 30), headers)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        ready_status, ready_payload = 0, {"error": f"{type(exc).__name__}: {exc}"}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as executor:
        futures = [executor.submit(_run_case, case, ask_url, args.timeout, headers) for case in cases]
        results = [future.result() for future in futures]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    results_path = args.output_dir / "results.jsonl"
    results_bytes = (
        "\n".join(json.dumps(result, ensure_ascii=False, sort_keys=True) for result in results) + "\n"
    ).encode("utf-8")
    results_path.write_bytes(results_bytes)
    data_hash, data_files = _data_manifest(rag_root)
    git_diff = _git(repo_root, "diff", "--binary", "HEAD")
    failures = [result["id"] for result in results if result["status"] != "ok"]
    manifest = {
        "schema_version": 1,
        "runner_version": RUNNER_VERSION,
        "run_id": run_id,
        "run_started_at": started_at,
        "run_completed_at": _utc_now(),
        "endpoint": ask_url,
        "endpoint_ready_status": ready_status,
        "endpoint_ready_payload_hash": _canonical_hash(ready_payload),
        "header_names": sorted(headers),
        "matrix_sha256": file_sha256(args.matrix),
        "taxonomy_version": TAXONOMY_VERSION,
        "taxonomy_sha256": file_sha256(args.taxonomy),
        "results_sha256": _sha256_bytes(results_bytes),
        "result_count": len(results),
        "expected_result_count": len(all_cases),
        "selected_case_ids": sorted(selected_ids),
        "complete": not selected_ids and len(results) == len(all_cases) and not failures,
        "failed_case_ids": failures,
        "git": {
            "commit_sha": _git(repo_root, "rev-parse", "HEAD"),
            "dirty": bool(_git(repo_root, "status", "--porcelain")),
            "diff_sha256": _sha256_bytes(git_diff.encode("utf-8")),
        },
        "candidate_config": _candidate_config(rag_root),
        "data_manifest_hash": data_hash,
        "data_files": data_files,
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output_dir": str(args.output_dir), **manifest}, ensure_ascii=False, indent=2))
    return 0 if manifest["complete"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
