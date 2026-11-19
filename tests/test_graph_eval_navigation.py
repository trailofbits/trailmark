"""Public navigation checks preserve scope and unavailable API distinctions."""

from __future__ import annotations

from typing import Any

import pytest

from tests.graph_eval.navigation import plan_queries, score_answers


def _reference(complete: bool = True) -> dict[str, Any]:
    return {
        "inventory_complete": complete,
        "questions": [
            {
                "id": "q1",
                "operation": "callees",
                "subject": "f",
                "target": None,
                "expected": ["g"],
                "question": "Which local helper does f call?",
            },
            {
                "id": "q2",
                "operation": "ownership",
                "subject": "f",
                "target": None,
                "expected": [],
                "question": "Which definitions does f own?",
            },
            {
                "id": "q3",
                "operation": "path",
                "subject": "f",
                "target": "g",
                "expected": ["f", "g"],
                "question": "Does f reach g?",
            },
        ],
    }


def test_public_queries_use_exact_ids_and_preserve_missing_method() -> None:
    plan = plan_queries(
        _reference(), {"mapping": {"f": "m:f", "g": "m:g"}}, prefix="reader-A:", profile="runtime"
    )
    assert len(plan["requests"]) == 2
    assert plan["requests"][0]["arguments"] == {"name": "m:f"}
    assert plan["records"][1]["status"] == "unsupported"
    results = score_answers(
        plan,
        [
            {
                "id": "reader-A:q1",
                "status": "ok",
                "answer": [{"id": "m:g"}, {"id": "unread:outside"}],
            },
            {"id": "reader-A:q3", "status": "ok", "answer": [["m:f", "m:g"]]},
        ],
    )
    assert [record["status"] for record in results] == ["agree", "unsupported", "agree"]


def test_unmatched_targets_block_and_partial_inventories_do_not_imply_absence() -> None:
    reference = _reference(False)
    reference["questions"][0]["expected"] = []
    plan = plan_queries(reference, {"mapping": {"f": "m:f"}}, prefix="B:", profile="formal")
    assert [record["status"] for record in plan["records"]] == ["unknown", "unsupported", "blocked"]


def test_missing_positive_or_api_failure_is_visible() -> None:
    plan = plan_queries(
        _reference(), {"mapping": {"f": "m:f", "g": "m:g"}}, prefix="A:", profile="runtime"
    )
    results = score_answers(plan, [{"id": "A:q1", "status": "ok", "answer": []}])
    assert results[0]["status"] == "discrepancy"
    assert results[2]["status"] == "error"


def test_negative_path_checks_indirect_paths_within_the_declared_scope() -> None:
    reference = _reference()
    reference["questions"][2]["expected"] = []
    plan = plan_queries(
        reference, {"mapping": {"f": "m:f", "g": "m:g", "h": "m:h"}}, prefix="A:", profile="runtime"
    )
    assert plan["requests"][1]["arguments"]["max_depth"] == 20
    answers = [{"id": "A:q3", "status": "ok", "answer": [["m:f", "m:h", "m:g"]]}]
    assert score_answers(plan, answers)[2]["status"] == "discrepancy"
    answers[0]["answer"] = [["m:f", "outside:h", "m:g"]]
    assert score_answers(plan, answers)[2]["status"] == "agree"


def test_paths_beyond_evaluator_bound_are_not_nominated_as_tool_failures() -> None:
    reference = _reference()
    keys = [f"f{index}" for index in range(22)]
    reference["questions"][2].update(subject=keys[0], target=keys[-1], expected=keys)
    mapping = {key: "m:" + key for key in [*keys, "f", "g"]}
    plan = plan_queries(reference, {"mapping": mapping}, prefix="A:", profile="runtime")
    assert plan["records"][2]["status"] == "not_evaluated"
    assert len(plan["requests"]) == 1


def test_unmatched_declaration_prevents_a_false_negative_answer() -> None:
    reference = _reference()
    reference["declarations"] = [{"key": "missing"}]
    reference["questions"][2]["expected"] = []
    plan = plan_queries(
        reference, {"mapping": {"f": "m:f", "g": "m:g"}}, prefix="A:", profile="runtime"
    )
    assert plan["records"][2]["status"] == "blocked"


@pytest.mark.parametrize(("node_count", "status"), [(21, "pending"), (22, "not_evaluated")])
def test_negative_paths_require_a_domain_covered_by_the_search_bound(
    node_count: int, status: str
) -> None:
    reference = _reference()
    reference["questions"][2]["expected"] = []
    mapping = {f"node{index}": f"m:f{index}" for index in range(node_count)}
    mapping.update(f="m:f0", g=f"m:f{node_count - 1}")
    plan = plan_queries(reference, {"mapping": mapping}, prefix="A:", profile="runtime")
    record = plan["records"][2]
    assert record["status"] == status
    path_requests = [
        request for request in plan["requests"] if request["operation"] == "paths_between"
    ]
    if status == "not_evaluated":
        assert path_requests == []
        # An empty answer from a 20-edge search cannot establish that the
        # mapped domain lacks a 21-edge path. It must never turn into agreement.
        scored = score_answers(plan, [{"id": "A:q3", "status": "ok", "answer": []}])
        assert scored[2]["status"] == "not_evaluated"
        assert "20-edge bound" in record["reason"]
    else:
        assert len(path_requests) == 1
        path = [f"m:f{index}" for index in range(node_count)]
        scored = score_answers(plan, [{"id": "A:q3", "status": "ok", "answer": [path]}])
        assert scored[2]["status"] == "discrepancy"
