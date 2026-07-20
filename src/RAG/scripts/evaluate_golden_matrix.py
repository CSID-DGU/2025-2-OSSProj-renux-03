"""Evidence-based four-axis evaluator for real HTTP golden-run results."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from jsonschema import Draft202012Validator, FormatChecker

from golden_matrix import DOMAINS, GoldenCase, file_sha256, load_matrix, load_taxonomy, validate_matrix


OFFICIAL_HOST_SUFFIXES = (
    "dongguk.edu",
    "dongguk.ac.kr",
    "kosaf.go.kr",
    "studyinkorea.go.kr",
)
TRUSTED_LOCAL_DATASETS = {"rules", "schedule", "courses", "staff", "meals"}
REFUSAL_LEAK_PATTERNS: dict[str, tuple[str, ...]] = {
    "internal_exam_data": (r"(?:정답|출제문항|시험문제)\s*[:：]",),
    "account_credentials": (r"(?:아이디|계정|비밀번호)\s*[:：은]\s*\S+",),
    "student_academic_record": (r"(?:학번|평점|성적)\s*[:：은]\s*[0-9]",),
    "scholarship_decision": (r"(?:선정|탈락|지급)\s*(?:결과|여부)?\s*[:：은]\s*\S+",),
    "student_record": (r"(?:학번|학적)\s*[:：은]\s*[0-9가-힣]",),
    "leave_reason": (r"휴학\s*사유\s*[:：은]\s*\S+",),
    "access_credential": (r"(?:비밀번호|출입코드)\s*[:：은]\s*\S+", r"\b\d{4,8}#?\b"),
    "unpublished_menu": (r"다음\s*달\s*(?:메뉴|식단)\s*[:：은]",),
    "room_assignment": (r"\b\d{3,4}호\b", r"방\s*번호\s*[:：은]\s*\S+"),
    "student_identity": (r"[가-힣]{2,4}\s*(?:학생|님)?\s*(?:은|는|:|：)\s*\d{3,4}호",),
    "hiring_decision": (r"(?:합격|불합격|탈락)\s*(?:자|결과)?\s*[:：은]\s*\S+",),
    "visa_case": (r"비자\s*(?:승인|거절|심사)\s*(?:결과)?\s*[:：은]\s*\S+",),
    "passport_number": (r"\b[A-Z][0-9]{7,9}\b",),
    "private_phone": (r"\b010[- ]?\d{3,4}[- ]?\d{4}\b",),
    "home_address": (r"(?:집|자택)\s*주소\s*[:：은]\s*\S+",),
    "unpublished_lineup": (r"출연진\s*[:：은]\s*(?!공개|확인)\S+",),
}


@dataclass(frozen=True)
class AxisResult:
    passed: bool
    reasons: tuple[str, ...]


def _contains(text: str, phrase: str) -> bool:
    return phrase.casefold() in text.casefold()


def _canonical_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def source_fingerprint(source: dict[str, Any]) -> str:
    identity = {
        "dataset": source.get("dataset"),
        "chunk_id": source.get("chunk_id"),
        "url": source.get("url"),
        "campus_scope": source.get("campus_scope"),
        "published_at": source.get("published_at"),
        "effective_date": source.get("effective_date"),
        "snippet_hash": source.get("snippet_hash"),
    }
    return f"sha256:{_canonical_hash(identity)}"


def _load_schema(path: Path) -> Draft202012Validator:
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def load_results(path: Path, schema_path: Path) -> list[dict[str, Any]]:
    validator = _load_schema(schema_path)
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {exc.msg}") from exc
        schema_errors = sorted(validator.iter_errors(row), key=lambda error: list(error.absolute_path))
        if schema_errors:
            error = schema_errors[0]
            location = ".".join(str(item) for item in error.absolute_path) or "<root>"
            raise ValueError(f"{path}:{line_number}:{location}: {error.message}")
        rows.append(row)
    return rows


def _is_official_url(url: str | None) -> bool:
    if not url:
        return False
    host = (urlparse(url).hostname or "").lower()
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in OFFICIAL_HOST_SUFFIXES)


def _valid_source(source: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons = []
    snippet_hash = hashlib.sha256(source["snippet"].encode()).hexdigest()
    if snippet_hash != source["snippet_hash"]:
        reasons.append("snippet hash mismatch")
    if source_fingerprint(source) != source["id"]:
        reasons.append("source id is not the deterministic response fingerprint")
    if not source.get("chunk_id") and not source.get("locator") and not source.get("url"):
        reasons.append("source has no transport identity")
    trusted = _is_official_url(source.get("url")) or (
        source.get("dataset") in TRUSTED_LOCAL_DATASETS
        and bool(source.get("chunk_id") or source.get("locator"))
    )
    if not trusted:
        reasons.append("source is not an official URL or a traceable trusted local dataset")
    return not reasons, reasons


def _intent_axis(case: GoldenCase, result: dict[str, Any]) -> AxisResult:
    actual = set(result.get("actual_app_intents", []))
    expected = set(case.expected_app_intents)
    reasons = []
    if not result.get("response_received") or result.get("status") != "ok":
        reasons.append("no successful HTTP response")
    if not expected.issubset(actual):
        reasons.append(f"app intents missing: {sorted(expected - actual)}")
    return AxisResult(not reasons, tuple(reasons))


def _answer_axis(case: GoldenCase, result: dict[str, Any]) -> AxisResult:
    answer = result.get("answer", "").strip()
    reasons: list[str] = []
    forbidden = [claim for claim in case.forbidden_claims if _contains(answer, claim)]
    if forbidden:
        reasons.append(f"forbidden claims present: {forbidden}")

    if case.answerability == "answerable":
        missing = [keyword for keyword in case.required_keywords if not _contains(answer, keyword)]
        if missing:
            reasons.append(f"required answer axes missing: {missing}")
        lexical_tokens = re.findall(r"[0-9A-Za-z가-힣]{2,}", answer)
        if len(answer) < 40 or len(lexical_tokens) < max(8, len(case.required_keywords) * 3):
            reasons.append("answer is a keyword list or lacks explanatory content")
        if not re.search(r"(?:다|요|니다|세요)[.!?]?\s*(?:\[\d+\])?", answer):
            reasons.append("answer has no complete explanatory sentence")
        if result.get("fallback_triggered"):
            reasons.append("answerable case used fallback")
    elif case.answerability == "needs_clarification":
        combined = " ".join([answer, *(item["question"] for item in result.get("followups", []))])
        if "?" not in combined and "까요" not in combined and "나요" not in combined:
            reasons.append("response does not ask a clarification question")
        for field in case.clarification_fields:
            tokens = [token for token in re.findall(r"[A-Za-z가-힣]{2,}", field) if token not in {"종류", "정보"}]
            if tokens and not any(_contains(combined, token) for token in tokens):
                reasons.append(f"clarification does not request missing field {field!r}")
    else:
        if not any(_contains(answer, marker) for marker in case.refusal_markers):
            reasons.append(f"row-specific refusal markers absent: {list(case.refusal_markers)}")
        if len(answer) < 20:
            reasons.append("refusal is not explanatory")

    if case.privacy_requirement == "no_sensitive_disclosure":
        for pii_type in case.forbidden_pii_types:
            patterns = REFUSAL_LEAK_PATTERNS.get(pii_type, ())
            if any(re.search(pattern, answer, flags=re.IGNORECASE) for pattern in patterns):
                reasons.append(f"sensitive disclosure detected despite refusal: {pii_type}")
    return AxisResult(not reasons, tuple(reasons))


def _source_axis(case: GoldenCase, result: dict[str, Any], run_at: datetime) -> AxisResult:
    sources = result.get("sources", [])
    reasons: list[str] = []
    for source in sources:
        valid, source_reasons = _valid_source(source)
        if not valid:
            reasons.extend(f"source {source.get('id')}: {reason}" for reason in source_reasons)

    if case.source_requirement in {"trusted_official", "official_contact"} and not sources:
        reasons.append("required official source is missing")
    actual_datasets = {source.get("dataset") for source in sources}
    expected_datasets = set(case.expected_datasets)
    if case.answerability == "answerable":
        if "cross_domain" in case.case_types:
            if not expected_datasets.issubset(actual_datasets):
                reasons.append(f"cross-domain datasets missing: {sorted(expected_datasets - actual_datasets)}")
        elif expected_datasets and not (expected_datasets & actual_datasets):
            reasons.append(f"no expected dataset present: {sorted(expected_datasets)}")

    scopes = {str(source.get("campus_scope")) for source in sources}
    allowed = set(case.allowed_campuses)
    required = set(case.required_campuses)
    # A clarification/refusal that intentionally carries no source must not be
    # forced to invent campus metadata. If any source is transported, however,
    # it is always checked even for those cases.
    if sources or case.source_requirement != "no_source_required":
        outside = sorted(scope for scope in scopes if scope not in allowed and scope != "shared")
        if outside:
            reasons.append(f"wrong-campus sources present: {outside}")
        represented = set(scopes)
        if "shared" in scopes and {"seoul", "bmc"}.issubset(allowed) and allowed != {"bmc"}:
            represented.update({"seoul", "bmc"})
        if case.campus_requirement_mode == "all" and not required.issubset(represented):
            reasons.append(f"required campus evidence missing: {sorted(required - represented)}")
        if case.campus_requirement_mode == "any" and not (required & represented):
            reasons.append(f"no required campus evidence present: {sorted(required)}")

    dated_sources = []
    for source in sources:
        raw = source.get("effective_date") or source.get("published_at")
        if not raw:
            continue
        match = re.search(r"\d{4}-\d{2}-\d{2}", str(raw))
        if match:
            try:
                dated_sources.append((source, datetime.fromisoformat(match.group()).replace(tzinfo=timezone.utc)))
            except ValueError:
                pass
    date_evidence_required = sources or case.source_requirement != "no_source_required"
    if (
        date_evidence_required
        and case.date_requirement in {"published_or_effective", "current_or_effective", "campus_comparison_dates"}
        and not dated_sources
    ):
        reasons.append("required published/effective date is missing")
    if case.date_requirement == "current_or_effective" and dated_sources:
        if not any(abs((date - run_at).days) <= 62 for _, date in dated_sources):
            reasons.append("no source date is near the real run date")
    if case.date_requirement == "campus_comparison_dates":
        dated_scopes = {source.get("campus_scope") for source, _ in dated_sources}
        if not required.issubset(dated_scopes):
            reasons.append(f"dated comparison evidence missing campuses: {sorted(required - dated_scopes)}")

    if case.citation_requirement in {"claim_source_links", "official_contact_source"}:
        citation_numbers = {
            int(source["citation_number"])
            for source in sources
            if isinstance(source.get("citation_number"), int)
        }
        markers = {int(number) for number in re.findall(r"\[(\d+)\]", result.get("answer", ""))}
        if not markers or not markers.issubset(citation_numbers):
            reasons.append("answer citation markers do not link to transported sources")
        if not result.get("citations_text", "").strip():
            reasons.append("transport citations_text is empty")
        for keyword in case.required_keywords if case.answerability == "answerable" else ():
            location = result.get("answer", "").find(keyword)
            nearby = result.get("answer", "")[location : location + 160] if location >= 0 else ""
            if location >= 0 and not re.search(r"\[\d+\]", nearby):
                reasons.append(f"claim axis {keyword!r} has no nearby citation")
    return AxisResult(not reasons, tuple(dict.fromkeys(reasons)))


def _content_tokens(text: str) -> set[str]:
    stop = {"알려줘", "확인", "공식", "관련", "질문", "정보", "방법", "있어", "어떻게"}
    return {token for token in re.findall(r"[A-Za-z가-힣]{2,}", text) if token not in stop}


def _followup_axis(case: GoldenCase, result: dict[str, Any]) -> AxisResult:
    followups = result.get("followups", [])
    sources = result.get("sources", [])
    source_ids = {source["id"] for source in sources}
    reasons: list[str] = []
    if case.followup_policy == "none":
        if followups:
            reasons.append("follow-up policy is none")
        return AxisResult(not reasons, tuple(reasons))
    if not followups:
        reasons.append(f"follow-up policy {case.followup_policy} requires a question")
        return AxisResult(False, tuple(reasons))

    original_norm = re.sub(r"\W", "", case.question).casefold()
    context_tokens = _content_tokens(" ".join([case.question, *case.required_keywords]))
    seen = set()
    for followup in followups:
        question = followup["question"].strip()
        normalized = re.sub(r"\W", "", question).casefold()
        if len(normalized) < 8 or ("?" not in question and "까요" not in question and "나요" not in question):
            reasons.append("meaningless or non-question follow-up")
        if normalized == original_norm or normalized in seen:
            reasons.append("duplicate/original follow-up")
        seen.add(normalized)
        if case.followup_policy == "grounded_next_steps" and context_tokens and not (_content_tokens(question) & context_tokens):
            reasons.append("follow-up is unrelated to the original request")
        refs = followup.get("source_refs", [])
        if case.followup_policy == "grounded_next_steps":
            if not refs:
                reasons.append("grounded follow-up has no transported source lineage")
            if not set(refs).issubset(source_ids):
                reasons.append("follow-up references a source not in the HTTP response")

    if case.followup_policy == "official_contact":
        if not any(_is_official_url(source.get("url")) or source.get("dataset") == "staff" for source in sources):
            reasons.append("official-contact follow-up has no official contact source")
        combined = " ".join(item["question"] for item in followups)
        if not any(token in combined for token in ("담당", "부서", "문의", "공식")):
            reasons.append("official-contact follow-up does not direct the user to an official path")
    return AxisResult(not reasons, tuple(dict.fromkeys(reasons)))


def evaluate(
    cases: list[GoldenCase],
    results: list[dict[str, Any]],
    *,
    run_at: datetime | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    run_at = run_at or datetime.now(timezone.utc)
    case_by_id = {case.id: case for case in cases}
    result_by_id: dict[str, dict[str, Any]] = {}
    contract_errors = []
    for result in results:
        result_id = result["id"]
        if result_id in result_by_id:
            contract_errors.append(f"duplicate result id {result_id}")
        result_by_id[result_id] = result
    missing = sorted(set(case_by_id) - set(result_by_id))
    extra = sorted(set(result_by_id) - set(case_by_id))
    if missing:
        contract_errors.append(f"missing result ids ({len(missing)}): {missing[:10]}")
    if extra:
        contract_errors.append(f"unknown result ids ({len(extra)}): {extra[:10]}")

    details = []
    wrong_campus_count = 0
    by_domain_rows: dict[str, list] = defaultdict(list)
    for case in cases:
        result = result_by_id.get(case.id)
        if not result:
            continue
        expected_question_hash = hashlib.sha256(case.question.encode()).hexdigest()
        if result["question_hash"] != expected_question_hash:
            contract_errors.append(f"{case.id}: question hash does not match matrix")
        axes = {
            "intent": _intent_axis(case, result),
            "answer": _answer_axis(case, result),
            "source": _source_axis(case, result, run_at),
            "followup": _followup_axis(case, result),
        }
        wrong_campus = any("campus" in reason for reason in axes["source"].reasons)
        wrong_campus_count += int(wrong_campus)
        detail = {
            "id": case.id,
            "domain": case.domain,
            "case_types": case.case_types,
            "axes": {name: asdict(axis) for name, axis in axes.items()},
            "all_axes_passed": all(axis.passed for axis in axes.values()),
            "wrong_campus": wrong_campus,
        }
        details.append(detail)
        by_domain_rows[case.domain].append(detail)

    by_domain = {}
    for domain in DOMAINS:
        rows = by_domain_rows[domain]
        if not rows:
            contract_errors.append(f"domain {domain} has no evaluated results")
        by_domain[domain] = {
            "evaluated": len(rows),
            "all_axes_passed": sum(row["all_axes_passed"] for row in rows),
            "axis_passed": {
                axis: sum(row["axes"][axis]["passed"] for row in rows)
                for axis in ("intent", "answer", "source", "followup")
            },
        }
    all_passed = sum(detail["all_axes_passed"] for detail in details)
    summary = {
        "schema_version": 2,
        "evaluated": len(details),
        "matrix_questions": len(cases),
        "all_axes_passed": all_passed,
        "wrong_campus_count": wrong_campus_count,
        "contract_errors": contract_errors,
        "by_domain": by_domain,
        "passed": not contract_errors and all_passed == len(cases) and wrong_campus_count == 0,
    }
    return summary, details


def write_markdown(summary: dict, details: list[dict], path: Path) -> None:
    lines = [
        "# Real candidate golden evaluation",
        "",
        f"- Gate: **{'PASS' if summary['passed'] else 'FAIL'}**",
        f"- Evaluated: {summary['evaluated']} / {summary['matrix_questions']}",
        f"- All four axes: {summary['all_axes_passed']}",
        f"- Campus failures: {summary['wrong_campus_count']}",
        "",
        "| Domain | Evaluated | All axes | Intent | Answer | Source | Follow-up |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for domain in DOMAINS:
        row = summary["by_domain"][domain]
        axis = row["axis_passed"]
        lines.append(
            f"| {domain} | {row['evaluated']} | {row['all_axes_passed']} | {axis['intent']} | "
            f"{axis['answer']} | {axis['source']} | {axis['followup']} |"
        )
    lines.extend(["", "## Failures", ""])
    for detail in (item for item in details if not item["all_axes_passed"]):
        reasons = [
            f"{axis}: {reason}"
            for axis, value in detail["axes"].items()
            for reason in value["reasons"]
        ]
        lines.append(f"- `{detail['id']}`: {'; '.join(reasons)}")
    if all(item["all_axes_passed"] for item in details):
        lines.append("- None")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Evaluate a real golden HTTP run")
    parser.add_argument("--matrix", type=Path, default=root / "tests" / "golden_matrix.csv")
    parser.add_argument("--taxonomy", type=Path, default=root / "tests" / "golden_taxonomy.v1.json")
    parser.add_argument("--schema", type=Path, default=root / "tests" / "golden_result.schema.json")
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        taxonomy = load_taxonomy(args.taxonomy)
        cases = load_matrix(args.matrix)
        structural = validate_matrix(cases, taxonomy)
        if structural:
            raise ValueError("invalid matrix: " + "; ".join(structural))
        results = load_results(args.results, args.schema)
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        run_at = datetime.fromisoformat(manifest["run_completed_at"])
        summary, details = evaluate(cases, results, run_at=run_at)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"Golden evaluation contract error: {exc}", file=sys.stderr)
        return 2
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "golden_evaluation_details.json").write_text(
        json.dumps(details, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "golden_evaluation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_markdown(summary, details, args.output_dir / "golden_evaluation_report.md")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
