"""Public query batches must preserve identity, failure evidence, and reader separation."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from tests.graph_eval import campaign, public_queries
from tests.graph_eval.measure import digest as graph_digest
from tests.graph_eval.util import digest, read_json, write_json


def _prepared_batch(
    root: Path, monkeypatch: pytest.MonkeyPatch, query_status: str = "ok"
) -> tuple[dict[str, Any], list[dict[str, Any]], list[list[dict[str, Any]]]]:
    cases = [
        {"id": ident, "project": "demo", "profile": "runtime", "input_hash": digest(ident)}
        for ident in ("ordinary", "stress")
    ]
    manifest = {
        "cases": cases,
        "projects": [
            {
                "id": "demo",
                "language": "python",
                "source_hash": digest([]),
                "roots": {"measurement-sources": str(root / "source")},
            }
        ],
        "tool_python": sys.executable,
        "limits": {"measurement_timeout": 20},
    }
    original = {"status": "ok", "graph": {"nodes": {}, "edges": []}}
    write_json(root / "measurements/demo/seal.json", {"sha256": "original"})
    questions = [
        {
            "id": "calls",
            "operation": "callees",
            "subject": "f",
            "target": None,
            "expected": ["h"],
            "question": "Which helper does the caller invoke?",
        },
        {
            "id": "path",
            "operation": "path",
            "subject": "f",
            "target": "h",
            "expected": ["f", "h"],
            "question": "How does the caller reach the helper?",
        },
        {
            "id": "owns",
            "operation": "ownership",
            "subject": "f",
            "target": None,
            "expected": [],
            "question": "Which declarations are directly owned?",
        },
    ]
    states = {}
    for case in cases:
        states[case["id"]] = []
        for role in ("reader-A", "reader-B"):
            path = root / "cases" / case["id"] / role / "response.json"
            write_json(path, {"inventory_complete": True, "questions": questions})
            states[case["id"]].append({"status": "ok", "output_path": str(path)})
        write_json(root / "cases" / case["id"] / "reference-seal.json", states[case["id"]])
    monkeypatch.setattr(campaign, "verify_reference_states", lambda _root, case: states[case["id"]])
    monkeypatch.setattr(campaign, "verify_measurement", lambda *_: original)
    monkeypatch.setattr(public_queries, "source_inventory", lambda _: [])
    monkeypatch.setattr(
        public_queries,
        "compare_reference",
        lambda *_: {"mapping": {"f": "demo:main", "h": "demo:helper"}},
    )
    batches = []

    def observe(
        _source: Path, _language: str, output: Path, requests: list[dict[str, Any]], **kwargs: Any
    ) -> dict[str, Any]:
        assert kwargs["expected_graph_hash"] == graph_digest(original["graph"])
        batches.append(requests)
        result = {
            "status": query_status,
            "query_baseline": {"graph_identical": query_status == "ok"},
            "query_answers": [
                {
                    "id": request["id"],
                    "status": "ok",
                    "answer": [{"id": "demo:helper"}]
                    if request["operation"] == "callees_of"
                    else [["demo:main", "demo:helper"]],
                }
                for request in requests
            ],
        }
        write_json(output / "measurement.json", result)
        return result

    monkeypatch.setattr(public_queries, "query_project", observe)
    return manifest, cases, batches


def test_public_query_batches_include_sibling_cases_without_id_collisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, cases, batches = _prepared_batch(tmp_path, monkeypatch)
    public_queries.run_navigation(tmp_path, manifest, cases[:1], 2)
    assert len(batches) == 1
    assert len(batches[0]) == 8
    assert len({request["id"] for request in batches[0]}) == 8
    for case in cases:
        result = public_queries.verify_public_navigation(tmp_path, case)
        assert result["status"] == "completed"
        assert Counter(record["status"] for record in result["records"]) == {
            "agree": 4,
            "unsupported": 2,
        }
        assert len(result["answers"]) == 4
        assert set(result["plans"]) == {"reader-A", "reader-B"}
        assert all(record["id"].startswith("reader-") for record in result["records"])
    public_queries.run_navigation(tmp_path, manifest, cases[1:], 1)
    assert len(batches) == 1


def test_invalid_query_graph_preserves_answers_without_scoring_agreement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, cases, _ = _prepared_batch(tmp_path, monkeypatch, "invalid")
    public_queries.run_navigation(tmp_path, manifest, cases, 1)
    result = public_queries.verify_public_navigation(tmp_path, cases[0])
    assert result["status"] == "query_failed"
    assert len(result["answers"]) == 4
    assert Counter(record["status"] for record in result["records"]) == {
        "error": 4,
        "unsupported": 2,
    }


@pytest.mark.parametrize("tamper", ["case", "batch"])
def test_navigation_tampering_is_rejected_before_cache_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tamper: str
) -> None:
    manifest, cases, batches = _prepared_batch(tmp_path, monkeypatch)
    public_queries.run_navigation(tmp_path, manifest, cases, 1)
    path = tmp_path / "cases" / cases[0]["id"] / "public-navigation.json"
    evidence = read_json(path)
    if tamper == "batch":
        path = Path(evidence["query_run"]["directory"]) / "observation.json"
    write_json(path, {"tampered": True})
    with pytest.raises(ValueError, match="Frozen public"):
        public_queries.run_navigation(tmp_path, manifest, cases, 1)
    assert len(batches) == 1


def test_navigation_refuses_an_unsealed_source_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, cases, batches = _prepared_batch(tmp_path, monkeypatch)

    def changed(*_: Any) -> list[dict[str, Any]]:
        raise ValueError("Frozen source references changed")

    monkeypatch.setattr(campaign, "verify_reference_states", changed)
    with pytest.raises(ValueError, match="Frozen source"):
        public_queries.run_navigation(tmp_path, manifest, cases, 1)
    assert batches == []
