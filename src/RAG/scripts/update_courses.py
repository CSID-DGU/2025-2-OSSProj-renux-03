"""학과 교과과정을 다시 수집하고 courses 인덱스를 갱신합니다."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.crawlers.dongguk_department_curriculum_content import main as crawl_courses
from src.database import init_db
from src.pipelines.ingest import ingest_courses


def main() -> None:
    init_db()
    crawl_courses()
    chunks_df, _, _ = ingest_courses(refresh_from_csv=True)
    print(f"courses 인덱스 갱신 완료: {len(chunks_df)} chunks")


if __name__ == "__main__":
    main()
