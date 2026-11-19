"""Public navigation checks preserve scope and unavailable API distinctions."""

from __future__ import annotations

from typing import Any

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
