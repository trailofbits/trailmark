"""Corruption and metamorphic controls for the evaluation harness itself."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from tests.graph_eval.compare import compare_reference, validate_adjudication_claims
from tests.graph_eval.prompts import reference_prompt
from tests.graph_eval.report import append_adjudication, generate_report, read_adjudications
from tests.graph_eval.schema import (
    ADJUDICATION_SCHEMA,
    REFERENCE_SCHEMA,
    validate_adjudication,
    validate_output,
    validate_reference,
)


def _site(line: int, path: str = "main.py", end_line: int | None = None) -> dict[str, Any]:
    return {
        "path": path,
        "line": line,
        "end_line": end_line or line,
        "column": None,
        "end_column": None,
    }


def _node(
    name: str, line: int, path: str = "main.py", kind: str = "function", end_line: int | None = None
) -> dict[str, Any]:
    return {"name": name, "kind": kind, "origin": "source", "location": _site(line, path, end_line)}


def _edge(source: str, target: str, line: int | None, kind: str = "calls") -> dict[str, Any]:
    return {
        "source": source,
        "target": target,
        "kind": kind,
        "confidence": "certain",
        "location": _site(line) if line else None,
        "attributes": {},
    }


def _reference() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "case_id": "python-control",
        "status": "complete",
        "inventory_complete": True,
        "declarations": [
            {
                "key": "f",
                "name": "run",
                "role": "function",
                "graph_kind": "function",
                "owner": "file:main.py",
                "location": _site(1, end_line=8),
                "evidence": "run calls helper at line 3",
            },
            {
                "key": "h",
                "name": "helper",
                "role": "function",
                "graph_kind": "function",
                "owner": "file:lib.py",
                "location": _site(1, "lib.py", 2),
                "evidence": "lib.helper declaration",
            },
        ],
        "assertions": [
            {
                "id": "call",
                "subject": "f",
                "kind": "calls",
                "site": _site(3),
                "target_key": "h",
                "target_name": "helper",
                "expectation": "present",
                "resolution": "resolved",
                "allowed_confidence": ["certain"],
                "context": "runtime",
                "evidence": "return helper()",
            },
        ],
        "questions": [
            {
                "id": "callees",
                "operation": "callees",
                "subject": "f",
                "target": None,
                "expected": ["h"],
                "question": "Which in-scope functions does run call?",
                "evidence": "return helper()",
            },
            {
                "id": "callers",
                "operation": "callers",
                "subject": "h",
                "target": None,
                "expected": ["f"],
                "question": "Which in-scope functions call helper?",
                "evidence": "run calls helper",
            },
            {
                "id": "path",
                "operation": "path",
                "subject": "f",
                "target": "h",
                "expected": ["f", "h"],
                "question": "How does run reach helper?",
                "evidence": "Direct call at line 3",
            },
        ],
        "notes": [],
    }


def _measurement() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "ok",
        "language": "python",
        "graph": {
            "nodes": {
                "main": _node("main", 1, kind="module", end_line=10),
                "lib": _node("lib", 1, "lib.py", "module", 2),
                "main:run": _node("run", 1, end_line=8),
                "lib:helper": _node("helper", 1, "lib.py", end_line=2),
            },
            "edges": [
                _edge("main", "main:run", None, "contains"),
                _edge("lib", "lib:helper", None, "contains"),
                _edge("main:run", "lib:helper", 3),
            ],
        },
        "diagnostics": [],
        "query_answers": [],
    }


def _case() -> dict[str, Any]:
    return {
        "id": "python-control",
        "language": "python",
        "profile": "runtime",
        "primary": [{"path": "main.py", "start_line": 1, "end_line": 10}],
        "context": [{"path": "lib.py", "start_line": 1, "end_line": 2}],
    }


def _compare(
    measurement: dict[str, Any],
    reference: dict[str, Any] | None = None,
    case: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return compare_reference(reference or _reference(), measurement, case or _case())


def test_clean_graph_agrees_without_claiming_precision_or_recall() -> None:
    result = _compare(_measurement())
    assert result["discrepancies"] == []
    assert result["metrics"]["precision"] is None
    assert result["metrics"]["recall"] is None
    assert result["metrics"]["call_sites"] == {"retained": 1, "missing": 0, "unscored": 0}


def test_dropped_call_is_detected() -> None:
    measurement = _measurement()
    measurement["graph"]["edges"].pop()
    result = _compare(measurement)
    assert result["assertions"][0]["issues"] == ["missing_relationship"]
    assert result["metrics"]["call_sites"]["missing"] == 1
    assert all(row["status"] == "discrepancy" for row in result["questions"])


def test_spurious_call_is_reviewed_only_for_complete_primary_inventory() -> None:
    measurement = _measurement()
    measurement["graph"]["edges"].append(_edge("main:run", "lib:helper", 5))
    assert _compare(measurement)["extras"][0]["issues"] == ["extra_relationship"]
    reference = _reference()
    reference["inventory_complete"] = False
    assert _compare(measurement, reference)["extras"] == []


def test_incorrect_namesake_target_is_not_accepted_by_short_name() -> None:
    measurement = _measurement()
    measurement["graph"]["nodes"]["other:helper"] = _node("helper", 1, "other.py")
    measurement["graph"]["edges"][-1]["target"] = "other:helper"
    row = _compare(measurement)["assertions"][0]
    assert row["issues"] == ["wrong_target"]
    assert row["call_site_retained"] is True


@pytest.mark.parametrize(
    ("field", "value", "issue"),
    [
        ("source", "lib:helper", "wrong_owner"),
        ("kind", "references", "wrong_kind"),
        ("confidence", "uncertain", "wrong_confidence"),
    ],
)
def test_corrupted_call_metadata_is_detected(field: str, value: str, issue: str) -> None:
    measurement = _measurement()
    measurement["graph"]["edges"][-1][field] = value
    assert issue in _compare(measurement)["assertions"][0]["issues"]


def test_wrong_declaration_owner_and_kind_are_detected() -> None:
    measurement = _measurement()
    measurement["graph"]["nodes"]["main:run"]["kind"] = "class"
    measurement["graph"]["edges"][0]["source"] = "lib"
    assert set(_compare(measurement)["declarations"][0]["issues"]) == {"wrong_owner", "wrong_kind"}


def test_proxy_retains_call_site_even_when_target_resolution_fails() -> None:
    measurement = _measurement()
    ident = "proxy.unresolved:helper"
    measurement["graph"]["nodes"][ident] = {"name": "helper", "kind": "proxy", "origin": "proxy"}
    measurement["graph"]["edges"][-1]["target"] = ident
    measurement["graph"]["edges"][-1]["attributes"] = {"raw_target": "helper"}
    result = _compare(measurement)
    assert result["assertions"][0]["issues"] == ["unresolved_target"]
    assert result["metrics"]["call_sites"]["retained"] == 1
    assert result["metrics"]["call_sites"]["missing"] == 0
    assert result["metrics"]["targets"]["unresolved"] == 1


def test_multiple_same_site_edges_are_ambiguous_even_if_one_target_matches() -> None:
    measurement = _measurement()
    measurement["graph"]["edges"].append(_edge("main:run", "main:run", 3))
    row = _compare(measurement)["assertions"][0]
    assert row["status"] == "ambiguous"
    assert row["call_site_retained"] is None
    assert len(row["candidates"]) == 2


def test_same_file_namespaced_declarations_are_not_arbitrarily_matched() -> None:
    measurement = _measurement()
    measurement["graph"]["nodes"]["lib:Other.helper"] = _node("helper", 1, "lib.py", end_line=2)
    measurement["graph"]["edges"].append(_edge("lib", "lib:Other.helper", None, "contains"))
    result = _compare(measurement)
    assert result["declarations"][1]["status"] == "ambiguous"
    assert "h" not in result["mapping"]
    assert "unmatched_target" in result["assertions"][0]["issues"]


def test_two_predicted_sites_cannot_reuse_one_observed_edge() -> None:
    reference = _reference()
    second = copy.deepcopy(reference["assertions"][0])
    second["id"] = "second-call-on-same-line"
    reference["assertions"].append(second)
    result = _compare(_measurement(), reference)
    assert result["metrics"]["call_sites"] == {"retained": 0, "missing": 0, "unscored": 2}
    assert all("ambiguous_claim_mapping" in row["issues"] for row in result["assertions"])


def test_same_line_column_anchors_preserve_repeated_sites() -> None:
    measurement, reference = _measurement(), _reference()
    second = copy.deepcopy(reference["assertions"][0])
    second["id"] = "second"
    reference["assertions"].append(second)
    measurement["graph"]["edges"].append(_edge("main:run", "lib:helper", 3))
    for index, bounds in enumerate(((0, 8), (8, 16))):
        reference["assertions"][index]["site"].update(column=bounds[0], end_column=bounds[1])
        measurement["graph"]["edges"][index + 2]["location"].update(
            column=bounds[0], end_column=bounds[1]
        )
    assert _compare(measurement, reference)["metrics"]["call_sites"]["retained"] == 2
    measurement["graph"]["edges"].pop()
    result = _compare(measurement, reference)
    assert result["metrics"]["call_sites"] == {"retained": 1, "missing": 1, "unscored": 0}


def test_negative_assertion_detects_spurious_call_and_does_not_guess_unlocated_site() -> None:
    reference, measurement = _reference(), _measurement()
    reference["assertions"][0]["expectation"] = "absent"
    assert _compare(measurement, reference)["assertions"][0]["issues"] == [
        "unexpected_relationship"
    ]
    measurement["graph"]["edges"][-1]["location"] = None
    row = _compare(measurement, reference)["assertions"][0]
    assert row["status"] == "unlocated"


def test_suppressed_expected_diagnostic_is_detected() -> None:
    reference = _reference()
    diagnostic = copy.deepcopy(reference["assertions"][0])
    diagnostic.update(
        id="coverage",
        kind="diagnostic",
        target_key=None,
        target_name="unsupported_syntax",
        resolution="either",
    )
    reference["assertions"].append(diagnostic)
    measurement = _measurement()
    measurement["diagnostics"] = [{"kind": "unsupported_syntax", "location": _site(3)}]
    assert _compare(measurement, reference)["assertions"][-1]["status"] == "agree"
    measurement["diagnostics"] = []
    assert _compare(measurement, reference)["assertions"][-1]["issues"] == ["missing_diagnostic"]


def test_bounded_primary_ranges_exclude_context_and_crossing_anchors() -> None:
    measurement, case = _measurement(), _case()
    case["primary"][0].update(start_line=3, end_line=3)
    measurement["graph"]["nodes"]["main:wide"] = _node("wide", 2, end_line=4)
    measurement["graph"]["nodes"]["lib:extra"] = _node("extra", 1, "lib.py")
    measurement["graph"]["edges"].append(_edge("main:run", "lib:helper", 4))
    assert _compare(measurement, case=case)["extras"] == []
    measurement["graph"]["nodes"]["main:local"] = _node("local", 3)
    assert _compare(measurement, case=case)["extras"][0]["node_id"] == "main:local"


def test_duplicate_declaration_mapping_is_ambiguous() -> None:
    reference = _reference()
    duplicate = copy.deepcopy(reference["declarations"][0])
    duplicate["key"] = "other_run"
    reference["declarations"].append(duplicate)
    result = _compare(_measurement(), reference)
    assert "f" not in result["mapping"]
    assert result["declarations"][0]["status"] == "ambiguous"
    assert result["assertions"][0]["status"] == "blocked"


def test_unknown_expectations_are_unscored_and_failed_measurements_have_no_zero_score() -> None:
    reference = _reference()
    reference["assertions"][0]["expectation"] = "unknown"
    assert _compare(_measurement(), reference)["metrics"]["call_sites"]["unscored"] == 1
    failed = _compare({"status": "timeout"})
    assert failed["status"] == "measurement_failed"
    assert "call_sites" not in failed["metrics"]


def test_partial_navigation_scores_positive_subsets_without_claiming_negative_completeness() -> (
    None
):
    reference, measurement = _reference(), _measurement()
    reference.update(status="partial", inventory_complete=False)
    measurement["graph"]["edges"].append(_edge("main:run", "main:run", 7))
    result = _compare(measurement, reference)
    assert result["questions"][0]["status"] == "agree"
    assert result["questions"][0]["answer_scope"] == "positive_subset"
    reference["questions"][0]["expected"] = []
    assert _compare(measurement, reference)["questions"][0]["status"] == "unknown"


@pytest.mark.parametrize("profile", ["runtime", "formal", "schema", "sql"])
def test_callee_and_caller_questions_ignore_noncall_relations_in_every_profile(
    profile: str,
) -> None:
    measurement, case = _measurement(), _case()
    case["profile"] = profile
    measurement["graph"]["edges"].extend(
        [
            _edge("main:run", "main:run", 5, "references"),
            _edge("lib:helper", "lib:helper", 6, "type_uses"),
        ]
    )
    questions = _compare(measurement, case=case)["questions"]
    assert [row["status"] for row in questions] == ["agree", "agree", "agree"]


@pytest.mark.parametrize("profile", ["formal", "schema", "sql"])
@pytest.mark.parametrize("relation", ["references", "type_uses", "corresponds_to"])
def test_structural_relations_remain_paths_and_dependencies_without_becoming_calls(
    profile: str, relation: str
) -> None:
    measurement, reference, case = _measurement(), _reference(), _case()
    case["profile"] = profile
    measurement["graph"]["edges"][-1]["kind"] = relation
    reference["assertions"][0]["kind"] = relation
    reference["questions"][0]["expected"] = []
    reference["questions"][1]["expected"] = []
    questions = _compare(measurement, reference, case)["questions"]
    assert [row["status"] for row in questions] == ["agree", "agree", "agree"]
    reference["questions"][0].update(operation="dependencies", expected=["h"])
    assert _compare(measurement, reference, case)["questions"][0]["status"] == "agree"


def test_graph_order_and_irrelevant_namesake_addition_do_not_change_results() -> None:
    measurement = _measurement()
    baseline = _compare(measurement)
    measurement["graph"]["nodes"] = dict(reversed(list(measurement["graph"]["nodes"].items())))
    measurement["graph"]["edges"].reverse()
    measurement["graph"]["nodes"]["unrelated:helper"] = _node("helper", 1, "unrelated.py")
    changed = _compare(measurement)
    assert changed["mapping"] == baseline["mapping"]
    assert changed["metrics"] == baseline["metrics"]
    assert changed["discrepancies"] == baseline["discrepancies"]


def test_source_path_relocation_preserves_results_without_basename_matching() -> None:
    measurement, reference, case = _measurement(), _reference(), _case()
    baseline = _compare(measurement)["metrics"]
    for node in measurement["graph"]["nodes"].values():
        node["location"]["path"] = "nested/" + node["location"]["path"]
    for edge in measurement["graph"]["edges"]:
        if edge["location"]:
            edge["location"]["path"] = "nested/" + edge["location"]["path"]
    for declaration in reference["declarations"]:
        declaration["location"]["path"] = "nested/" + declaration["location"]["path"]
        declaration["owner"] = declaration["owner"].replace("file:", "file:nested/")
    reference["assertions"][0]["site"]["path"] = "nested/main.py"
    case["primary"][0]["path"] = "nested/main.py"
    assert _compare(measurement, reference, case)["metrics"] == baseline
    reference["declarations"][1]["location"]["path"] = "wrong/lib.py"
    assert _compare(measurement, reference, case)["declarations"][1]["status"] == "missing"


@pytest.mark.parametrize("mutation", ["extra", "missing", "bad_path", "unknown_key", "partial"])
def test_reference_validation_is_strict(mutation: str) -> None:
    reference = _reference()
    if mutation == "extra":
        reference["declarations"][0]["tool_id"] = "invented"
    elif mutation == "missing":
        del reference["questions"][0]["evidence"]
    elif mutation == "bad_path":
        reference["assertions"][0]["site"]["path"] = "../hidden.py"
    elif mutation == "unknown_key":
        reference["assertions"][0]["subject"] = "not_declared"
    else:
        reference["status"] = "partial"
    with pytest.raises(ValueError):
        validate_reference(reference)


def test_every_schema_object_requires_all_fields_and_forbids_extras() -> None:
    def inspect(schema: dict[str, Any]) -> None:
        if schema["type"] == "object":
            assert schema["additionalProperties"] is False
            assert set(schema["required"]) == set(schema["properties"])
            for child in schema["properties"].values():
                inspect(child)
        elif schema["type"] == "array":
            inspect(schema["items"])

    inspect(REFERENCE_SCHEMA)
    inspect(ADJUDICATION_SCHEMA)
    validate_output(_reference(), REFERENCE_SCHEMA)
    prompt = reference_prompt(_case(), "1: def run():")
    assert "source_read" in prompt and "source_search" in prompt
    assert "1: def run():" in prompt


def _adjudication(category: str = "confirmed_defect") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "case_id": "python-control",
        "decisions": [
            {
                "claim_id": "assertion:call",
                "category": category,
                "evidence": "main.py:3 calls lib.helper",
                "expected": "run -> helper at main.py:3",
                "observed": "No call edge",
                "reproduction": "trailmark parse /source --language python",
            },
        ],
        "notes": [],
    }


def test_adjudication_requires_concrete_reproduction_and_preserves_append_history(
    tmp_path: Path,
) -> None:
    invalid = _adjudication()
    invalid["decisions"][0]["reproduction"] = ""
    with pytest.raises(ValueError, match="reproduction"):
        validate_adjudication(invalid)
    reference = _reference()
    frozen = json.dumps(reference, sort_keys=True)
    journal = tmp_path / "decisions.jsonl"
    first = append_adjudication(journal, _adjudication())
    second = append_adjudication(journal, _adjudication("expectation_error"))
    assert second["previous_hash"] == first["hash"]
    assert len(read_adjudications(journal)) == 2
    assert json.dumps(reference, sort_keys=True) == frozen
    tampered = journal.read_text().replace("No call edge", "Invented", 1)
    journal.write_text(tampered)
    with pytest.raises(ValueError, match="integrity"):
        read_adjudications(journal)


def test_adjudication_requires_exact_claim_membership() -> None:
    adjudication = _adjudication("unresolved")
    validate_adjudication_claims(adjudication, {"assertion:call"})
    with pytest.raises(ValueError, match="missing"):
        validate_adjudication_claims(adjudication, {"assertion:call", "question:path"})
    with pytest.raises(ValueError, match="extra"):
        validate_adjudication_claims(adjudication, set())


def test_report_separates_adjudicated_and_provisional_and_keeps_all_coverage_statuses() -> None:
    measurement = _measurement()
    measurement["graph"]["edges"].pop()
    comparison = _compare(measurement)
    report = generate_report(
        {
            "baseline": "abc",
            "cases": [_case(), {"id": "not-run", "language": "rust"}],
            "frontend_status": {"python": "pilot", "rust": "not_evaluated"},
        },
        [
            {
                "case_id": "python-control",
                "status": "partial",
                "comparisons": [comparison],
                "readers": [{"status": "ok"}, {"status": "timeout"}],
                "measurement_status": "ok",
                "adjudication": _adjudication(),
                "replay": {"status": "not_run"},
            }
        ],
    )
    assert "## Adjudicated findings" in report
    assert "## Provisional and unresolved discrepancies" in report
    assert "confirmed_defect" in report
    assert "question:path" in report
    assert "| rust | not_evaluated |" in report
    assert "| not-run | rust | not_run |" in report
    assert "ok/complete, timeout" in report
    assert "Precision and recall are not reported" in report


def test_report_aggregates_recorded_attempt_resources_without_price_estimates() -> None:
    report = generate_report(
        {"cases": [_case()]},
        [
            {
                "case_id": "python-control",
                "readers": [
                    {
                        "status": "ok",
                        "reference_status": "partial",
                        "worker": {
                            "attempts": [
                                {
                                    "elapsed_seconds": 2,
                                    "usage": {"input_tokens": 10, "output_tokens": 3},
                                },
                                {
                                    "elapsed_seconds": 3,
                                    "usage": {"input_tokens": 20, "output_tokens": 4},
                                },
                            ]
                        },
                    },
                ],
                "judge_job": {
                    "worker": {
                        "attempts": [
                            {
                                "elapsed_seconds": 7,
                                "usage": {"input_tokens": 5, "output_tokens": 2},
                            },
                        ]
                    }
                },
            }
        ],
    )
    assert "ok/partial" in report
    assert "Jobs: 2; attempts: 3" in report
    assert "Summed job wall seconds: 12" in report
    assert "median job wall seconds: 6" in report
    assert "'input_tokens': 35" in report and "'output_tokens': 9" in report
    assert "not a monetary cost estimate" in report


def test_report_language_stratum_agreement_and_public_navigation_are_separate() -> None:
    case = {**_case(), "stratum": "stress"}
    report = generate_report(
        {"cases": [case]},
        [
            {
                "case_id": case["id"],
                "status": "compared",
                "reader_agreement": {
                    "shared_declaration_anchors": 2,
                    "shared_claims": 1,
                    "claims_only_a": 3,
                    "claims_only_b": 4,
                },
                "public_navigation": [
                    {
                        "claim_id": "reader-A:public-question:path",
                        "operation": "path",
                        "status": "unsupported",
                    }
                ],
            }
        ],
    )
    assert "## Outcomes by language" in report and "| python | 1 |" in report
    assert "## Outcomes by stratum" in report and "| stress | 1 |" in report
    assert "| python-control | 2 | 1 | 3 | 4 |" in report
    assert "| python-control | reader-A:public-question:path | path | unsupported |" in report
