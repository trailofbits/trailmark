"""Report provisional comparisons and append independent decisions separately."""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any

from tests.graph_eval.schema import validate_adjudication


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def append_adjudication(
    path: Path, adjudication: dict[str, Any], *, provenance: dict[str, str] | None = None
) -> dict[str, Any]:
    """Append a hash-linked decision event; never edit a frozen reference."""
    validate_adjudication(adjudication)
    history = read_adjudications(path)
    payload = {
        "sequence": len(history) + 1,
        "previous_hash": history[-1]["hash"] if history else None,
        "adjudication": adjudication,
    }
    if provenance is not None:
        payload["provenance"] = provenance
    record = {**payload, "hash": hashlib.sha256(_canonical(payload)).hexdigest()}
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "ab") as stream:
        stream.write(_canonical(record) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    return record


def read_adjudications(path: Path) -> list[dict[str, Any]]:
    """Reject a malformed or reordered journal rather than hiding its history."""
    if not path.exists():
        return []
    records = []
    previous = None
    for line in path.read_text().splitlines():
        record = json.loads(line)
        payload = {key: value for key, value in record.items() if key != "hash"}
        if (
            record.get("sequence") != len(records) + 1
            or record.get("previous_hash") != previous
            or record.get("hash") != hashlib.sha256(_canonical(payload)).hexdigest()
        ):
            raise ValueError("Adjudication journal integrity check failed")
        validate_adjudication(record["adjudication"])
        records.append(record)
        previous = record["hash"]
    return records


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _resources(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    jobs = []
    for result in case_results:
        jobs.extend(result.get("readers", []))
        for key in ("judge_job", "adjudicator_job"):
            if result.get(key):
                jobs.append(result[key])
    durations = []
    tokens: Counter[str] = Counter()
    attempt_count = 0
    for job in jobs:
        worker = job.get("worker", job)
        attempts = worker.get("attempts", [])
        wall = [
            item["elapsed_seconds"]
            for item in attempts
            if isinstance(item.get("elapsed_seconds"), (int, float))
        ]
        if wall:
            durations.append(sum(wall))
        attempt_count += len(attempts)
        for attempt in attempts:
            for name, value in attempt.get("usage", {}).items():
                if isinstance(value, int) and not isinstance(value, bool):
                    tokens[name] += value
    return {
        "jobs": len(jobs),
        "attempts": attempt_count,
        "jobs_with_wall_time": len(durations),
        "summed_job_wall_seconds": round(sum(durations), 3),
        "median_job_wall_seconds": round(median(durations), 3) if durations else None,
        "tokens": dict(sorted(tokens.items())),
    }


def generate_report(campaign_manifest: dict[str, Any], case_results: list[dict[str, Any]]) -> str:
    """Render all cases, with adjudicated findings separate from nominations."""
    by_id = {item["case_id"]: item for item in case_results}
    cases = campaign_manifest.get("cases", [])
    rows = []
    findings = []
    provisional = []
    category_counts: Counter[str] = Counter()
    totals: Counter[str] = Counter()
    groups: dict[str, dict[str, Counter[str]]] = {"Language": {}, "Stratum": {}}
    for case in cases:
        case_id = case.get("id", case.get("case_id"))
        result = by_id.get(case_id, {})
        status = result.get("status", "not_run")
        comparisons = result.get("comparisons", [])
        claims = {
            claim["claim_id"]: claim
            for comparison in comparisons
            for claim in comparison.get("discrepancies", [])
        }
        adjudication = result.get("adjudication")
        if adjudication:
            validate_adjudication(adjudication, case_id)
        decisions = {item["claim_id"]: item for item in (adjudication or {}).get("decisions", [])}
        for claim_id in sorted(set(claims) | set(decisions)):
            decision = decisions.get(claim_id)
            if decision is None:
                provisional.append((case_id, claim_id, "unadjudicated", claims[claim_id]))
                continue
            category = decision["category"]
            category_counts[category] += 1
            if category in {"confirmed_defect", "documented_limitation", "capability_need"}:
                findings.append((case_id, claim_id, category, decision, result.get("replay")))
            elif category == "unresolved":
                provisional.append((case_id, claim_id, category, decision))
        readers = result.get("readers", [])
        reader_labels = []
        for index, item in enumerate(readers):
            label = str(item.get("status", "unknown"))
            reference_status = item.get("reference_status")
            if reference_status is None and index < len(comparisons):
                reference_status = comparisons[index].get("reference_status")
            if reference_status:
                label += f"/{reference_status}"
            reader_labels.append(label)
        reader_status = ", ".join(reader_labels) or "not_run"
        measurement = result.get("measurement_status", "not_run")
        rows.append(
            (
                case_id,
                case.get("language", "unknown"),
                status,
                reader_status,
                measurement,
                len(claims),
                len(decisions),
            )
        )
        totals[status] += 1
        for label, field in (("Language", "language"), ("Stratum", "stratum")):
            group = groups[label].setdefault(case.get(field, "unspecified"), Counter())
            group[status] += 1
    # Results absent from the manifest are retained visibly as orchestration errors.
    known_ids = {case.get("id", case.get("case_id")) for case in cases}
    for case_id in sorted(set(by_id) - known_ids):
        rows.append(
            (
                case_id,
                by_id[case_id].get("language", "unknown"),
                "unregistered_result",
                "unknown",
                "unknown",
                "?",
                "?",
            )
        )
    lines = [
        "# Trailmark graph evaluation",
        "",
        "Source readers produced independent hypotheses. Deterministic comparisons nominate "
        "discrepancies; adjudication records source-backed decisions without changing references.",
        "",
        "Precision and recall are not reported: complete, independently adjudicated inventories "
        "and unambiguous matches have not been established.",
        "",
        f"Baseline: `{_cell(campaign_manifest.get('baseline', 'unspecified'))}`. "
        f"Cases: {len(cases)}. Status counts: {_cell(dict(sorted(totals.items())))}.",
        "",
        "## Coverage",
        "",
        "`pilot` means selected for evaluation; case outcomes show which work completed. "
        "Decision counts are claims, not deduplicated defect counts.",
        "",
        "| Language | Status |",
        "| --- | --- |",
    ]
    for language, status in sorted(campaign_manifest.get("frontend_status", {}).items()):
        lines.append(f"| {_cell(language)} | {_cell(status)} |")
    for label, values in groups.items():
        lines.extend(
            [
                "",
                f"## Outcomes by {label.lower()}",
                "",
                f"| {label} | Cases | Status counts |",
                "| --- | ---: | --- |",
            ]
        )
        for name, counts in sorted(values.items()):
            lines.append(
                f"| {_cell(name)} | {sum(counts.values())} | "
                f"{_cell(dict(sorted(counts.items())))} |"
            )
    lines.extend(
        [
            "",
            "## Case outcomes",
            "",
            "| Case | Language | Status | Readers | Measurement | Provisional claims | Decisions |",
            "| --- | --- | --- | --- | --- | ---: | ---: |",
        ]
    )
    lines.extend("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows)
    lines.extend(
        [
            "",
            "## Independent reader agreement",
            "",
            "Exact source anchors and claim fields describe agreement between hypotheses. "
            "They do not establish graph accuracy; an omission is not a negative claim.",
            "",
            "| Case | Shared declarations | Shared claims | Claims only A | Claims only B |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for result in case_results:
        agreement = result.get("reader_agreement", result.get("agreement"))
        if agreement:
            lines.append(
                "| "
                + " | ".join(
                    _cell(value)
                    for value in (
                        result["case_id"],
                        agreement.get("shared_declaration_anchors", "unrecorded"),
                        agreement.get("shared_claims", "unrecorded"),
                        agreement.get("claims_only_a", "unrecorded"),
                        agreement.get("claims_only_b", "unrecorded"),
                    )
                )
                + " |"
            )
    lines.extend(
        [
            "",
            "## Public query navigation",
            "",
            "These records score actual public query answers "
            "independently of enriched-graph navigation.",
            "",
            "| Case | Claim | Operation | Status |",
            "| --- | --- | --- | --- |",
        ]
    )
    for result in case_results:
        navigation = result.get("public_navigation", result.get("navigation", []))
        if isinstance(navigation, dict):
            navigation = navigation.get("records", [])
        for record in navigation:
            lines.append(
                "| "
                + " | ".join(
                    _cell(value)
                    for value in (
                        result["case_id"],
                        record.get("claim_id", record.get("id", "unknown")),
                        record.get("operation", "unknown"),
                        record.get("status", "unknown"),
                    )
                )
                + " |"
            )
    resources = _resources(case_results)
    lines.extend(
        [
            "",
            "## Recorded resource use",
            "",
            f"Jobs: {resources['jobs']}; attempts: {resources['attempts']}. "
            f"Jobs with recorded wall time: {resources['jobs_with_wall_time']}.",
            "",
            f"Summed job wall seconds: {resources['summed_job_wall_seconds']}; "
            f"median job wall seconds: {resources['median_job_wall_seconds']}.",
            "",
            f"Recorded token totals: `{_cell(resources['tokens'])}`.",
            "",
            "Summed job time includes retries and may overlap across concurrent jobs. "
            "These figures are recorded usage, not a monetary cost estimate.",
        ]
    )
    lines.extend(
        [
            "",
            "## Adjudicated findings",
            "",
            "These are adjudicator classifications. A confirmed-defect label requires a "
            "concrete reproduction; execution evidence is reported separately.",
            "",
        ]
    )
    if not findings:
        lines.append("No adjudicated findings are available.")
    for case_id, claim_id, category, decision, replay in findings:
        lines.extend(
            [
                f"### {_cell(case_id)} / {_cell(claim_id)}",
                "",
                f"Category: **{category}**. Replay evidence: `{_cell(replay or 'not recorded')}`.",
                "",
                f"Expected: {decision['expected']}",
                "",
                f"Observed: {decision['observed']}",
                "",
                f"Evidence: {decision['evidence']}",
                "",
                f"Reproduction: {decision['reproduction']}",
                "",
            ]
        )
    lines.extend(["", "## Provisional and unresolved discrepancies", ""])
    if not provisional:
        lines.append(
            "No pending discrepancy claims were recorded. "
            "Failed or unrun cases remain visible above."
        )
    for case_id, claim_id, status, claim in provisional:
        details = claim.get("issues", claim.get("evidence", "Evidence pending"))
        lines.append(f"- `{_cell(case_id)}` / `{_cell(claim_id)}` ({status}): {_cell(details)}")
    lines.extend(
        [
            "",
            "## Decision categories",
            "",
            "All classifications, including corrected expectations and comparison errors, "
            "remain in the append-only adjudication artifacts.",
            "",
        ]
    )
    lines.extend(f"- {category}: {count}" for category, count in sorted(category_counts.items()))
    lines.extend(
        [
            "",
            "Call-site retention, target resolution and bounded graph navigation are "
            "separate comparison dimensions. Navigation comparisons use the enriched graph; "
            "public query API responses are retained separately. No untested frontend is "
            "counted as covered.",
            "",
        ]
    )
    return "\n".join(lines)
