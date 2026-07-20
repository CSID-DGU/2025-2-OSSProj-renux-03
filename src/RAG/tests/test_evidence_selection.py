from __future__ import annotations

import inspect
import asyncio
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import api.rag_service as rag_service  # noqa: E402
import src.services.evidence_selector as evidence_selector  # noqa: E402
import src.services.langchain_chat as langchain_chat  # noqa: E402
from src.services.evidence_selector import (  # noqa: E402
    EvidenceGroupDecision,
    EvidenceSelectionDecision,
)


DATASETS = ["notices", "rules", "schedule", "courses", "staff", "meals"]


def _frame(dataset: str, count: int = 5, query: str = "question") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "chunk_id": f"{dataset}-{index}",
                "chunk_text": f"{dataset} evidence {index}",
                "dataset": dataset,
                "source": dataset,
                "title": f"{dataset} title {index}",
                "hybrid_score": 0.9 - index * 0.1,
                "matched_query": query,
            }
            for index in range(count)
        ]
    )


def test_routerless_route_and_both_endpoints_do_not_call_router():
    assert rag_service._routerless_retrieval_route() == DATASETS
    ask_source = inspect.getsource(rag_service.ask)
    stream_source = inspect.getsource(rag_service.ask_stream)

    assert "route_query(" not in ask_source
    assert "route_query(" not in stream_source
    assert "_routerless_retrieval_route()" in ask_source
    assert "_routerless_retrieval_route()" in stream_source
    assert "_select_evidence_for_answer" in ask_source
    assert "_select_evidence_for_answer" in stream_source


def test_balanced_shortlist_collects_all_six_datasets_with_equal_quota():
    shortlist = rag_service._build_balanced_shortlist([_frame(dataset) for dataset in DATASETS])

    assert set(shortlist["dataset"]) == set(DATASETS)
    assert len(shortlist) == 18
    assert shortlist.groupby("dataset").size().to_dict() == {dataset: 3 for dataset in DATASETS}
    assert shortlist[shortlist["dataset_rank"] == 1]["dataset"].tolist() == DATASETS


def test_irrelevant_documents_are_excluded_by_structured_decision():
    shortlist = rag_service._build_balanced_shortlist([_frame("rules", count=3)])
    decision = EvidenceSelectionDecision(
        groups=[EvidenceGroupDecision(document_ids=["c2"], distinction="direct evidence")]
    )

    groups = rag_service._normalize_evidence_groups(decision, set(shortlist["candidate_id"]))
    selected = rag_service._materialize_evidence_groups(shortlist, groups)

    assert groups == [["c2"]]
    assert selected["candidate_id"].tolist() == ["c2"]


def test_duplicate_evidence_stays_in_one_group_and_single_answer():
    shortlist = rag_service._build_balanced_shortlist([_frame("rules", count=3)])
    decision = EvidenceSelectionDecision(
        groups=[EvidenceGroupDecision(document_ids=["c1", "c2"], distinction="same proposition")]
    )

    groups = rag_service._normalize_evidence_groups(decision, set(shortlist["candidate_id"]))
    selected = rag_service._materialize_evidence_groups(shortlist, groups)

    assert selected["evidence_group"].nunique() == 1
    assert rag_service._multiple_evidence_response_instructions(1) is None


def test_distinct_evidence_groups_are_equal_sections_capped_at_five():
    shortlist = rag_service._build_balanced_shortlist([_frame("rules", count=6)], per_dataset=6)
    decision = EvidenceSelectionDecision(
        groups=[
            EvidenceGroupDecision(document_ids=[f"c{index}"], distinction=f"distinct {index}")
            for index in range(1, 7)
        ]
    )

    groups = rag_service._normalize_evidence_groups(decision, set(shortlist["candidate_id"]))
    selected = rag_service._materialize_evidence_groups(shortlist, groups)
    instructions = rag_service._multiple_evidence_response_instructions(len(groups))

    assert len(groups) == 5
    assert selected["evidence_group"].tolist() == [1, 2, 3, 4, 5]
    assert instructions is not None
    assert "## 확인된 정보 1" in instructions
    assert "동등한 위상" in instructions


def test_selector_failure_uses_safe_single_group_fallback(monkeypatch):
    shortlist = rag_service._build_balanced_shortlist([_frame(dataset, count=1) for dataset in DATASETS])

    async def failed_selector(*_args, **_kwargs):
        return None

    monkeypatch.setattr(rag_service, "select_evidence_groups", failed_selector)
    selected, did_fallback = asyncio.run(
        rag_service._select_evidence_for_answer("question", shortlist, [])
    )

    assert did_fallback is True
    assert selected["evidence_group"].nunique() == 1
    assert selected["dataset"].tolist() == DATASETS
    fallback_context = rag_service._build_selected_evidence_context(selected)
    assert all(f"문서 {index}" in fallback_context for index in range(1, 7))


def test_openai_selector_timeout_is_observable_and_returns_fallback_signal(monkeypatch):
    class BrokenSelector:
        async def ainvoke(self, _messages):
            raise TimeoutError("timed out")

    monkeypatch.setattr(evidence_selector, "_structured_selector", lambda: BrokenSelector())
    usage: list[dict] = []
    decision = asyncio.run(
        evidence_selector.select_evidence_groups(
            "question",
            [{"candidate_id": "c1", "dataset": "rules", "text": "evidence"}],
            usage,
        )
    )

    assert decision is None
    assert usage[-1]["stage"] == "evidence_selection"
    assert usage[-1]["failed"] is True


def test_openai_selector_disables_sdk_retries(monkeypatch):
    captured: dict = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def with_structured_output(self, *_args, **_kwargs):
            return object()

    evidence_selector._structured_selector.cache_clear()
    monkeypatch.setattr(evidence_selector, "ChatOpenAI", FakeChatOpenAI)
    try:
        evidence_selector._structured_selector()
    finally:
        evidence_selector._structured_selector.cache_clear()

    assert captured["timeout"] == evidence_selector.RAG_EVIDENCE_TIMEOUT_SECONDS
    assert captured["max_retries"] == 0


def test_invalid_structured_response_returns_fallback_signal(monkeypatch):
    class InvalidSelector:
        async def ainvoke(self, _messages):
            return {"raw": None, "parsed": None, "parsing_error": ValueError("invalid JSON")}

    monkeypatch.setattr(evidence_selector, "_structured_selector", lambda: InvalidSelector())
    usage: list[dict] = []
    decision = asyncio.run(
        evidence_selector.select_evidence_groups(
            "question",
            [{"candidate_id": "c1", "dataset": "rules", "text": "evidence"}],
            usage,
        )
    )

    assert decision is None
    assert usage[-1]["stage"] == "evidence_selection"
    assert usage[-1]["failed"] is True


def test_system_prompt_preserves_multiple_evidence_groups():
    prompt = langchain_chat._get_system_prompt("rag")

    assert "[근거 그룹]이 둘 이상" in prompt
    assert "동일 그룹 안에서만" in prompt
    assert "각 그룹을 별도의 동등한 섹션으로 유지" in prompt


def test_source_metadata_excludes_evidence_selection_bookkeeping():
    internal_fields = {
        "candidate_id",
        "dataset_rank",
        "retrieval_fusion_score",
        "evidence_group",
        "citation_number",
        "selector_fallback",
    }
    row = pd.Series(
        {
            "title": "public title",
            "url": "https://example.edu/document",
            **{field: 1 for field in internal_fields},
        }
    )

    metadata = rag_service._source_metadata(row)

    assert metadata["title"] == "public title"
    assert metadata["url"] == "https://example.edu/document"
    assert internal_fields.isdisjoint(metadata)
    assert "metadata=_source_metadata(row)" in inspect.getsource(rag_service.ask)
    assert "metadata=_source_metadata(row)" in inspect.getsource(rag_service.ask_stream)


def test_new_accuracy_path_contains_no_domain_specific_exception_terms():
    selector_source = Path(rag_service.__file__).parents[1] / "src" / "services" / "evidence_selector.py"
    source = selector_source.read_text(encoding="utf-8")

    assert "일반휴학" not in source
    assert "창업휴학" not in source
