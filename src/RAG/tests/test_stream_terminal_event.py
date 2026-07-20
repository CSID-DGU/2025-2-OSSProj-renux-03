from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import api.rag_service as rag_service  # noqa: E402


def _payload(event: str) -> dict:
    data_line = next(line for line in event.splitlines() if line.startswith("data: "))
    return json.loads(data_line.removeprefix("data: "))


async def _collect(body, request_id: str = "request-1") -> list[str]:
    return [
        event
        async for event in rag_service._stream_with_terminal_event(body, request_id)
    ]


def test_successful_stream_emits_done_as_the_final_event():
    async def body():
        yield 'data: {"type":"metadata"}\n\n'
        yield 'data: {"type":"text","content":"answer"}\n\n'
        yield rag_service._completion_stream_event(
            request_id="request-1",
            grounded=True,
            grounding_score=0.9,
            suggested_questions=[],
            fallback_reason=None,
            sources=[],
        )

    events = asyncio.run(_collect(body()))
    payloads = [_payload(event) for event in events]

    assert [payload["type"] for payload in payloads] == ["metadata", "text", "completion", "done"]
    assert payloads[-1]["request_id"] == "request-1"


def test_plain_eof_without_completion_is_not_success():
    async def body():
        yield 'data: {"type":"metadata"}\n\n'
        yield 'data: {"type":"text","content":"answer"}\n\n'

    payloads = [_payload(event) for event in asyncio.run(_collect(body()))]
    assert [payload["type"] for payload in payloads] == ["metadata", "text"]
    assert all(payload["type"] != "done" for payload in payloads)


def test_partial_stream_error_emits_error_without_done():
    async def body():
        yield 'data: {"type":"text","content":"partial"}\n\n'
        raise RuntimeError("upstream failed")

    events = asyncio.run(_collect(body()))
    payloads = [_payload(event) for event in events]

    assert [payload["type"] for payload in payloads] == ["text", "error"]
    assert all(payload["type"] != "done" for payload in payloads)
    assert payloads[-1]["content"]


def test_cancellation_after_partial_body_emits_no_done():
    async def body():
        yield 'data: {"type":"text","content":"partial"}\n\n'
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_collect(body()))
