"""모든 갱신 잡이 실행 결과를 DB에 남긴다.

`_record_run`은 메모리 딕셔너리라 프로세스가 재시작하면 사라진다. courses·meals 잡은
그것밖에 쓰지 않아서, 실패해도 `ingestion_runs`에 아무 흔적이 없었다. 그 사이 교과목
색인은 45개 학과에 멈춰 있었는데(수집 CSV에는 71개) 아무도 알아채지 못했다.

"실행 기록 0건"을 "한 번도 안 돌았다"로 읽었던 것도 이 때문이다 — 실제로는 기록하는
경로가 없었을 뿐이다.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_SCHEDULER = Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_SOURCE = _SCHEDULER.read_text(encoding="utf-8")


def _job_body(name: str) -> str:
    match = re.search(rf"^def {name}\(.*?(?=^def |\Z)", _SOURCE, re.M | re.S)
    assert match is not None, f"{name}을 찾지 못했습니다"
    return match.group(0)


# notices는 파이프라인(notices_sync)이 직접 IngestionRun을 남기므로 잡 본문에는 없다.
_JOBS_RECORDING_IN_JOB = ["refresh_rules_job", "refresh_schedule_job", "refresh_courses_job", "refresh_meals_job"]


@pytest.mark.parametrize("잡", _JOBS_RECORDING_IN_JOB)
def test_갱신_잡은_실행을_DB에_기록한다(잡):
    body = _job_body(잡)
    assert "_start_ingestion_run" in body, f"{잡}이 실행 시작을 남기지 않습니다"
    assert "_finish_ingestion_run" in body, f"{잡}이 실행 종료를 남기지 않습니다"


@pytest.mark.parametrize("잡", _JOBS_RECORDING_IN_JOB)
def test_실패_경로도_기록을_닫는다(잡):
    """예외 처리 블록에서 run을 닫지 않으면 running 상태로 영원히 남는다."""
    body = _job_body(잡)
    except_block = body.split("except Exception", 1)
    assert len(except_block) == 2, f"{잡}에 예외 처리가 없습니다"
    assert "_finish_ingestion_run" in except_block[1], f"{잡}의 실패 경로가 기록을 닫지 않습니다"


def test_메모리_기록만으로는_충분하지_않다():
    """_record_run이 재시작에 살아남지 않는다는 사실을 코드로 고정한다."""
    import src.services.scheduler as scheduler

    scheduler._record_run("검증용", "ok", "메시지")
    assert scheduler._LAST_RUNS["검증용"]["last_status"] == "ok"
    # 프로세스 안에서만 유효한 저장소임을 드러낸다.
    assert isinstance(scheduler._LAST_RUNS, dict)
