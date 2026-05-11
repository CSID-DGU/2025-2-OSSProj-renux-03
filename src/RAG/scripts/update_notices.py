"""동국대 공지를 크롤링해 CSV/Chroma 인덱스에 신규 행을 추가합니다."""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.crawlers.dongguk_notices import TARGET_BOARDS, crawl_notices
from src.pipelines.notices_sync import sync_notices
from src.pipelines.ingest import ingest_notices, reindex_from_db
from src.database import init_db


def _run_once(boards: list[str], max_pages: int | None, delay: float) -> None:
    start_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{start_ts}] 🕐 크롤링 시작")
    try:
        notices_df = crawl_notices(boards=boards, max_pages=max_pages, delay=delay)
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ 크롤링 실패: {exc}")
        return

    try:
        added = sync_notices(notices_df)
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ 동기화 실패: {exc}")
        return
    end_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{end_ts}] ✅ 신규 공지 {added}건 반영 완료.")

    print("(CSV와 Chroma 컬렉션이 최신 상태입니다.)")


def main() -> None:
    init_db() # DB 초기화
    parser = argparse.ArgumentParser(description="Dongguk notice crawler + incremental index updater")
    parser.add_argument(
        "--max-pages",
        type=int,
        default=5,
        help="Number of pages per board to fetch (default: 5)",
    )
    parser.add_argument("--delay", type=float, default=0.2, help="Delay between detail requests in seconds")
    parser.add_argument(
        "--boards",
        nargs="*",
        default=None,
        help="Specific board names to crawl (default: all configured boards)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Ignore --max-pages and crawl the entire board (could take long).",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=0,
        help="Repeat crawl every N minutes (0 = run once).",
    )

    args = parser.parse_args()

    boards = args.boards or TARGET_BOARDS
    max_pages = None if args.full else args.max_pages

    if args.interval <= 0:
        _run_once(boards, max_pages, args.delay)
        return

    interval_seconds = args.interval * 60
    try:
        while True:
            _run_once(boards, max_pages, args.delay)
            print(f"⏳ {args.interval}분 후 다음 작업을 실행합니다. (종료: Ctrl+C)")
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        print("🛑 스케줄 반복을 종료합니다.")


if __name__ == "__main__":
    main()
