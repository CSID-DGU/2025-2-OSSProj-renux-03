#!/usr/bin/env python3
"""Build, verify, activate, or roll back the notices dense index safely."""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.notices_dense_rebuild import (  # noqa: E402
    GracefulStop,
    MAX_BATCH_SIZE,
    MIN_BATCH_SIZE,
    activate_notice_dense_build,
    build_notice_dense_index,
    load_checkpoint,
    load_notice_chunk_snapshot,
    rollback_notice_dense_pointer,
    verify_notice_dense_build,
)
from src.vectorstore.collection_pointer import read_pointer_state  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint-dir", type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Build/resume and verify a staged collection")
    build.add_argument("--build-id", help="Stable resume identifier; deterministic when omitted")
    build.add_argument("--artifact", type=Path, help="Notice parquet/CSV; defaults to the configured artifact")
    build.add_argument(
        "--batch-size",
        type=int,
        default=MIN_BATCH_SIZE,
        choices=range(MIN_BATCH_SIZE, MAX_BATCH_SIZE + 1),
        metavar=f"{MIN_BATCH_SIZE}..{MAX_BATCH_SIZE}",
    )

    verify = subparsers.add_parser("verify", help="Re-run ID/count/dimension/20-query verification")
    verify.add_argument("--build-id", required=True)

    activate = subparsers.add_parser("activate", help="Atomically switch the logical pointer")
    activate.add_argument("--build-id", required=True)
    activate.add_argument(
        "--confirm-build-id",
        required=True,
        help="Must exactly repeat --build-id; activation never happens from build alone",
    )
    activate.add_argument("--pointer-file", type=Path)
    activate.add_argument("--lock-file", type=Path)

    rollback = subparsers.add_parser("rollback", help="Atomically return to the previous collection")
    rollback.add_argument("--confirm-active-collection", required=True)
    rollback.add_argument("--pointer-file", type=Path)
    rollback.add_argument("--lock-file", type=Path)

    status = subparsers.add_parser("status", help="Show pointer state and optional build checkpoint")
    status.add_argument("--build-id")
    status.add_argument("--pointer-file", type=Path)
    status.add_argument("--artifact", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    checkpoint_dir = args.checkpoint_dir
    try:
        if args.command == "build":
            with GracefulStop() as stop:
                result = build_notice_dense_index(
                    artifact_path=args.artifact,
                    build_id=args.build_id,
                    batch_size=args.batch_size,
                    checkpoint_dir=checkpoint_dir,
                    should_stop=lambda: stop.requested,
                )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 75 if result["status"] == "paused" else 0
        if args.command == "verify":
            result = verify_notice_dense_build(
                build_id=args.build_id,
                checkpoint_dir=checkpoint_dir,
            )
        elif args.command == "activate":
            result = activate_notice_dense_build(
                build_id=args.build_id,
                confirm_build_id=args.confirm_build_id,
                checkpoint_dir=checkpoint_dir,
                pointer_path=args.pointer_file,
                lock_path=args.lock_file,
            )
        elif args.command == "rollback":
            result = rollback_notice_dense_pointer(
                confirm_active_collection=args.confirm_active_collection,
                pointer_path=args.pointer_file,
                lock_path=args.lock_file,
            )
        else:
            result = {
                "pointer": read_pointer_state(args.pointer_file),
                "artifact": (
                    {
                        "path": str(snapshot.path),
                        "sha256": snapshot.artifact_sha256,
                        "count": snapshot.count,
                        "ids_sha256": snapshot.expected_ids_sha256,
                    }
                    if (snapshot := load_notice_chunk_snapshot(args.artifact))
                    else None
                ),
                "checkpoint": (
                    load_checkpoint(args.build_id, checkpoint_dir)
                    if args.build_id
                    else None
                ),
            }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # CLI boundary: checkpoint already contains detail.
        logging.error("%s: %s", type(exc).__name__, exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
