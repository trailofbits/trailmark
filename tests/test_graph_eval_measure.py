"""Checks for the evaluator's evidence capture, not its target's golden output."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.graph_eval.measure import (
    collect_measurement,
    digest,
    measure_project,
    normalize_paths,
    query_project,
    source_inventory,
)
from tests.graph_eval.validators import validate_files, validate_source


def test_measurement_preserves_sites_owners_proxies_and_public_export(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "sample.py").write_text(
        "def helper(): pass\n"
        "def main(callback):\n"
        "    helper(); helper(); callback()\n"
        "    def nested():\n"
        "        helper()\n"
    )
    measurement = collect_measurement(
        root,
        "python",
        [
            {"id": "neighbors", "operation": "callees_of", "arguments": {"name": "sample:main"}},
            {"id": "invalid", "operation": "annotate", "arguments": {}},
        ],
    )
    assert measurement["status"] == "ok"
    assert measurement["deterministic"] is True
    assert measurement["source_unchanged"] is True
    assert measurement["repeated_graph_hashes"] == [digest(measurement["graph"])] * 2
    graph = measurement["graph"]
    calls = [edge for edge in graph["edges"] if edge["kind"] == "calls"]
    helper_calls = [edge for edge in calls if edge["target"] == "sample:helper"]
    assert len(helper_calls) == 3
    assert {edge["source"] for edge in helper_calls} == {"sample:main", "sample:main.nested"}
    same_line = [edge for edge in helper_calls if edge["location"]["start_line"] == 3]
    assert len(same_line) == 2
    assert same_line[0]["location"]["start_col"] != same_line[1]["location"]["start_col"]
    assert all(edge["location"]["file_path"] == "sample.py" for edge in helper_calls)
    assert "location" not in measurement["public_export"]["edges"][0]
    assert len(measurement["unresolved_calls"]) == 1
    unresolved = measurement["unresolved_calls"][0]
    assert unresolved["raw_target"].endswith("callback")
    assert graph["nodes"][unresolved["target"]]["origin"] == "proxy"
    assert measurement["query_answers"][0]["status"] == "ok"
    assert measurement["query_answers"][1]["status"] == "error"
    assert measurement["raw_graph"]["entrypoints"] == {}
    assert measurement["runtime"]["trailmark_source_sha256"]
    assert measurement["runtime"]["grammar_files"]


@pytest.mark.parametrize(
    ("language", "filename", "source", "expected_kind"),
    [
        (
            "sql",
            "schema.sql",
            "CREATE TABLE account (id integer); CREATE VIEW active AS SELECT id FROM account;",
            "corresponds_to",
        ),
        (
            "graphql",
            "schema.graphql",
            "type Query { account: Account } type Account { id: ID! }",
            "type_uses",
        ),
    ],
)
def test_schema_measurements_preserve_relationships_without_inventing_calls(
    tmp_path: Path,
    language: str,
    filename: str,
    source: str,
    expected_kind: str,
) -> None:
    (tmp_path / filename).write_text(source)
    measurement = collect_measurement(tmp_path, language)
    kinds = [edge["kind"] for edge in measurement["graph"]["edges"]]
    assert "calls" not in kinds
    assert expected_kind in kinds
    assert measurement["deterministic"]
    assert measurement["runtime"]["grammar_files"]


def test_source_inventory_uses_bytes_and_does_not_follow_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    source = root / "source.py"
    source.write_text("print('one')\n")
    outside = tmp_path / "outside.py"
    outside.write_text("secret contents\n")
    (root / "alias.py").symlink_to(outside)
    first = source_inventory(root)
    source.touch()
    assert source_inventory(root) == first
    outside.write_text("changed outside\n")
    assert source_inventory(root) == first
    source.write_text("print('two')\n")
    assert source_inventory(root)["tree_sha256"] != first["tree_sha256"]
    assert next(row for row in first["files"] if row["path"] == "alias.py")["kind"] == "symlink"


def test_only_path_fields_are_normalized(tmp_path: Path) -> None:
    raw = {
        "file_path": str(tmp_path / "a.py"),
        "root_path": str(tmp_path),
        "name": str(tmp_path / "literal.py"),
        "attributes": {"raw_target": "a.b"},
    }
    normalized = normalize_paths(raw, tmp_path)
    assert normalized["file_path"] == "a.py"
    assert normalized["root_path"] == "."
    assert normalized["name"] == raw["name"]
    assert normalized["attributes"] == raw["attributes"]


def test_measurement_worker_uses_installed_interpreter_and_retains_logs(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "simple.py").write_text("def helper(): pass\ndef main(): helper()\n")
    output = tmp_path / "result"
    result = measure_project(source, "python", output)
    assert result["status"] == "ok"
    assert result["worker_command"][:2] == [sys.executable, "-I"]
    assert json.loads((output / "measurement.json").read_text()) == result
    assert (output / "stdout.log").is_file()
    assert (output / "stderr.log").is_file()


def test_worker_timeout_replaces_stale_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "measurement"
    destination.mkdir()
    (destination / "measurement.json").write_text('{"status":"ok","stale":true}')

    def timeout(*args: Any, **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired("worker", 1)

    monkeypatch.setattr("tests.graph_eval.measure.subprocess.run", timeout)
    result = measure_project(tmp_path / "source", "python", destination, timeout=1)
    assert result["status"] == "timeout"
    assert "stale" not in result
    assert json.loads((destination / "measurement.json").read_text())["status"] == "timeout"


def test_failed_worker_is_not_a_successful_empty_graph(tmp_path: Path) -> None:
    result = measure_project(tmp_path / "missing", "python", tmp_path / "result")
    assert result["status"] == "crashed"
    assert result["returncode"] != 0
    assert "graph" not in result
    assert "Traceback" in (tmp_path / "result" / "stderr.log").read_text()


def test_public_navigation_reparse_requires_the_original_graph(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "sample.py").write_text(
        "def leaf(): pass\ndef middle(): leaf()\ndef main(): middle()\n"
    )
    expected_hash = digest(collect_measurement(source, "python")["graph"])
    questions = [
        {"id": "callees", "operation": "callees_of", "arguments": {"name": "sample:main"}},
        {"id": "callers", "operation": "callers_of", "arguments": {"name": "sample:leaf"}},
        {
            "id": "path",
            "operation": "paths_between",
            "arguments": {"src": "sample:main", "dst": "sample:leaf", "max_depth": 3},
        },
    ]
    result = query_project(
        source, "python", tmp_path / "queries", questions, expected_graph_hash=expected_hash
    )
    assert result["status"] == "ok"
    assert result["query_baseline"]["graph_identical"] is True
    answers = {row["id"]: row for row in result["query_answers"]}
    assert {row["id"] for row in answers["callees"]["answer"]} == {"sample:middle"}
    assert {row["id"] for row in answers["callers"]["answer"]} == {"sample:middle"}
    assert answers["path"]["answer"] == [["sample:main", "sample:middle", "sample:leaf"]]
    (source / "sample.py").write_text("def different(): pass\n")
    changed = query_project(
        source, "python", tmp_path / "changed", questions, expected_graph_hash=expected_hash
    )
    assert changed["status"] == "invalid"
    assert changed["query_baseline"]["graph_identical"] is False
    assert "differs" in changed["error"]


def test_python_validator_ignores_quoted_code_and_preserves_nested_ownership(
    tmp_path: Path,
) -> None:
    path = tmp_path / "example.py"
    path.write_text('"def fake(): dangerous()"\ndef outer():\n    def inner():\n        helper()\n')
    result = validate_source(path, "python")
    assert result["status"] == "accepted"
    assert [row["qualified_name"] for row in result["declarations"]] == ["outer", "outer.inner"]
    assert result["calls"] == [
        {
            "owner": "outer.inner",
            "expression": "helper",
            "line": 4,
            "column": 8,
            "end_line": 4,
            "end_column": 16,
        }
    ]
    assert "not binding or execution" in result["scope"]


def test_python_validator_reports_invalid_syntax(tmp_path: Path) -> None:
    path = tmp_path / "broken.py"
    path.write_text("def incomplete(")
    result = validate_source(path, "python")
    assert result["status"] == "rejected"
    assert result["diagnostics"][0]["line"] == 1


def test_optional_validator_absence_is_reported_not_passed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tests.graph_eval.validators.importlib.util.find_spec", lambda _: None)
    for language in ("graphql", "sql"):
        assert validate_source(tmp_path / "source", language)["status"] == "unavailable"
    assert validate_source(tmp_path / "source", "rust")["status"] == "not_configured"


def test_validation_batch_preserves_per_file_outcomes_and_rejects_escape(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "good.py").write_text("def good(): pass\n")
    (root / "bad.py").write_text("def bad(\n")
    (tmp_path / "outside.py").write_text("def outside(): pass\n")
    (root / "link.py").symlink_to(tmp_path / "outside.py")
    destination = tmp_path / "validation"
    result = validate_files(
        root,
        "python",
        ["good.py", "bad.py", "good.py", "../outside.py", "link.py"],
        destination,
    )
    statuses = {row["path"]: row["status"] for row in result["files"]}
    assert statuses == {
        "good.py": "accepted",
        "bad.py": "rejected",
        "../outside.py": "error",
        "link.py": "error",
    }
    assert result["counts"] == {"accepted": 1, "rejected": 1, "error": 2}
    assert json.loads((destination / "validation.json").read_text()) == result


@pytest.mark.parametrize("problem", ["source-change", "nondeterministic"])
def test_measurement_invalidates_failed_controls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, problem: str
) -> None:
    (tmp_path / "source.py").write_text("def helper(): pass\n")
    if problem == "source-change":
        inventories = iter([{"tree_sha256": "before"}, {"tree_sha256": "after"}])
        monkeypatch.setattr(
            "tests.graph_eval.measure.source_inventory", lambda _: next(inventories)
        )
    else:
        from tests.graph_eval import measure

        original = measure.snapshot
        calls = 0

        def changed_snapshot(*args: Any, **kwargs: Any) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            result = original(*args, **kwargs)
            if calls == 3:
                result["unexpected_change"] = True
            return result

        monkeypatch.setattr(measure, "snapshot", changed_snapshot)
    measurement = collect_measurement(tmp_path, "python")
    assert measurement["status"] == "invalid"
    assert measurement["source_unchanged"] is (problem != "source-change")
    assert measurement["deterministic"] is (problem != "nondeterministic")
