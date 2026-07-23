"""Plan or explicitly apply retryable repairs for normalized notice data."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RAG_ROOT = Path(__file__).resolve().parents[1]
if str(RAG_ROOT) not in sys.path:
    sys.path.insert(0, str(RAG_ROOT))

from src.database import SessionLocal  # noqa: E402
from src.services.notice_repair import apply_notice_repairs, plan_notice_repairs  # noqa: E402


APPLY_CONFIRMATION = "APPLY_NOTICE_REPAIRS"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Mutate normalized files, DB rows, and the notice index")
    parser.add_argument(
        "--confirm",
        default="",
        help=f"Required with --apply; must exactly equal {APPLY_CONFIRMATION}",
    )
    parser.add_argument("--document-key", action="append", default=[], help="Limit work to one document key; repeatable")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--embedding-chunks-per-minute", type=float)
    parser.add_argument("--output", type=Path, help="Optionally write the JSON manifest to this path")
    return parser


def main(argv: list[str] | None = None, *, session_factory=SessionLocal) -> int:
    args = _parser().parse_args(argv)
    keys = args.document_key or None
    try:
        if args.apply:
            if args.confirm != APPLY_CONFIRMATION:
                print(
                    f"Refusing mutation: --confirm must exactly equal {APPLY_CONFIRMATION}",
                    file=sys.stderr,
                )
                return 2
            manifest = apply_notice_repairs(
                session_factory=session_factory,
                document_keys=keys,
                batch_size=args.batch_size,
                assumed_embedding_chunks_per_minute=args.embedding_chunks_per_minute,
            )
        else:
            session = session_factory()
            try:
                manifest = plan_notice_repairs(
                    session,
                    document_keys=keys,
                    assumed_embedding_chunks_per_minute=args.embedding_chunks_per_minute,
                )
            finally:
                session.close()
    except Exception as exc:  # noqa: BLE001
        print(f"Notice repair failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    rendered = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 2 if manifest["counts"]["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
