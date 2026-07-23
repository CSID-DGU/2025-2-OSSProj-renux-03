#!/usr/bin/env python3
"""Build/verify six RAG dense datasets in an explicit isolated Chroma path."""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.staged_dense_rebuild import (  # noqa: E402
    DATASETS,
    GracefulStop,
    MAX_BATCH_SIZE,
    MIN_BATCH_SIZE,
    build_staged_datasets,
    staged_build_status,
    verify_staged_datasets,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--chroma-dir",
        type=Path,
        required=True,
        help="New isolated Chroma path; live/corrupt/artifacts paths are rejected",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Build/resume and verify without activation")
    build.add_argument("--dataset", required=True, choices=(*DATASETS, "all"))
    build.add_argument(
        "--batch-size",
        type=int,
        default=MIN_BATCH_SIZE,
        choices=range(MIN_BATCH_SIZE, MAX_BATCH_SIZE + 1),
        metavar=f"{MIN_BATCH_SIZE}..{MAX_BATCH_SIZE}",
    )

    verify = subparsers.add_parser("verify", help="Verify source/IDs/count/dimension/searches")
    verify.add_argument("--dataset", required=True, choices=(*DATASETS, "all"))

    subparsers.add_parser("status", help="Read checkpoint state without opening Chroma")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        if args.command == "status":
            result = staged_build_status(args.chroma_dir)
        elif args.command == "verify":
            result = verify_staged_datasets(
                chroma_dir=args.chroma_dir,
                selection=args.dataset,
            )
        else:
            with GracefulStop() as stop:
                result = build_staged_datasets(
                    chroma_dir=args.chroma_dir,
                    selection=args.dataset,
                    batch_size=args.batch_size,
                    should_stop=lambda: stop.requested,
                )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get("status") == "paused":
            return 75
        if result.get("status") == "failed":
            return 1
        return 0
    except Exception as exc:
        logging.error("%s: %s", type(exc).__name__, exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
