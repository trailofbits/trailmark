"""Mutation quality gates must fail closed on missing or inconclusive results."""

from __future__ import annotations

import ast
import hashlib
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from pytest_gremlins.instrumentation.transformer import transform_source

from tests.mutation_gate import _ast_dump, check, describe
from tests.run_mutations import RUNNER, VERSION

pytestmark = pytest.mark.mutation_infrastructure


@pytest.fixture
def campaign(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    source = "def bounded(value):\n    return value > 0\n"
    target = tmp_path / "target.py"
    target.write_text(source)
    mutants, _ = transform_source(source, str(target))
    results = [
        {
            "gremlin_id": g.gremlin_id,
            "file_path": str(target),
            "line_number": g.line_number,
            "operator": g.operator_name,
            "description": g.description,
            "status": "zapped",
            "selected_tests": ["test_target.py::test_boundary"],
        }
        for g in mutants
    ]
    report = {"results": results, "summary": {"total": len(results), "zapped": len(results)}}
    run = {
        "pytest_gremlins": VERSION,
        "runner": RUNNER,
        "shard": [1, 1],
        "sources": {"target.py": hashlib.sha256(source.encode()).hexdigest()},
    }
    baseline = {"pytest_gremlins": VERSION, "scope": {"target.py": "*"}, "exceptions": []}
    return report, run, baseline


def test_passes_complete_killed_campaign(tmp_path: Path, campaign: tuple) -> None:
    assert check(tmp_path, *campaign) == []


@pytest.mark.parametrize("skipped", [{"other.py": "ValueError: cannot instrument"}, [], None])
def test_rejects_skipped_or_invalid_instrumentation(
    tmp_path: Path, campaign: tuple, skipped: Any
) -> None:
    campaign[0]["skipped_files"] = skipped
    with pytest.raises(ValueError, match="skipped files"):
        check(tmp_path, *campaign)


@pytest.mark.parametrize(
    ("document", "field", "previous"),
    [
        (1, "pytest_gremlins", "1.9.0"),
        (2, "pytest_gremlins", "1.9.0"),
        (1, "runner", "trailmark-full-pytest-v2"),
        (1, "runner", "trailmark-full-pytest-v3"),
    ],
)
def test_rejects_previous_adapter_reports(
    tmp_path: Path, campaign: tuple, document: int, field: str, previous: str
) -> None:
    campaign[document][field] = previous
    with pytest.raises(ValueError, match="version|validated pytest"):
        check(tmp_path, *campaign)


@pytest.mark.parametrize("status", ["survived", "timeout", "error", "pardoned"])
def test_rejects_non_kills(tmp_path: Path, campaign: tuple, status: str) -> None:
    report, run, baseline = campaign
    report["results"][0]["status"] = status
    report["summary"] = {
        "total": len(report["results"]),
        **Counter(r["status"] for r in report["results"]),
    }
    assert len(check(tmp_path, report, run, baseline)) == 1


def test_rejects_result_without_tests(tmp_path: Path, campaign: tuple) -> None:
    campaign[0]["results"][0]["selected_tests"] = []
    failures = check(tmp_path, *campaign)
    assert len(failures) == 1 and '"status": "no tests"' in failures[0]


def test_requires_complete_inventory_even_if_summary_matches(
    tmp_path: Path, campaign: tuple
) -> None:
    campaign[0]["results"].pop()
    campaign[0]["summary"]["total"] -= 1
    campaign[0]["summary"]["zapped"] -= 1
    with pytest.raises(ValueError, match="inventory"):
        check(tmp_path, *campaign)


@pytest.mark.parametrize("shard", [(1, 3), (2, 3), (3, 3), (99, 99)])
def test_gate_requires_exact_shard_inventory(
    tmp_path: Path, campaign: tuple, shard: tuple[int, int]
) -> None:
    report, run, baseline = campaign
    report["results"] = report["results"][shard[0] - 1 :: shard[1]]
    report["summary"] = {"total": len(report["results"]), "zapped": len(report["results"])}
    run["shard"] = list(shard)
    assert check(tmp_path, report, run, baseline, shard=shard) == []
    with pytest.raises(ValueError, match="partition"):
        check(tmp_path, report, run, baseline)
    if report["results"]:
        report["results"].pop()
        report["summary"]["total"] -= 1
        report["summary"]["zapped"] -= 1
        with pytest.raises(ValueError, match="inventory"):
            check(tmp_path, report, run, baseline, shard=shard)


@pytest.mark.parametrize(
    "corruption", ["duplicate", "other shard", "wrong metadata", "missing metadata"]
)
def test_gate_rejects_overlapping_or_mismatched_shards(
    tmp_path: Path, campaign: tuple, corruption: str
) -> None:
    report, run, baseline = campaign
    original = list(report["results"])
    report["results"] = original[::2]
    run["shard"] = [1, 2]
    if corruption == "missing metadata":
        del run["shard"]
    elif corruption == "wrong metadata":
        run["shard"] = [2, 2]
    else:
        report["results"].append(original[0 if corruption == "duplicate" else 1])
    report["summary"] = {"total": len(report["results"]), "zapped": len(report["results"])}
    with pytest.raises(ValueError, match="inventory|partition"):
        check(tmp_path, report, run, baseline, shard=(1, 2))


def test_rejects_stale_source_and_empty_report(tmp_path: Path, campaign: tuple) -> None:
    (tmp_path / "target.py").write_text("def bounded(value):\n    return value > 1\n")
    with pytest.raises(ValueError, match="stale"):
        check(tmp_path, *campaign)
    campaign[0]["results"] = []
    with pytest.raises(ValueError, match="Missing"):
        check(tmp_path, *campaign)


def test_review_applies_only_to_exact_survivor(tmp_path: Path, campaign: tuple) -> None:
    report, run, baseline = campaign
    source = (tmp_path / "target.py").read_text()
    mutants, _ = transform_source(source, str(tmp_path / "target.py"))
    detail = describe(mutants[0], source, "target.py")
    baseline["exceptions"] = [{**detail, "reason": "Reviewed example for gate testing."}]
    report["results"][0]["status"] = "survived"
    report["summary"]["zapped"] -= 1
    report["summary"]["survived"] = 1
    assert check(tmp_path, report, run, baseline) == []
    report["results"][0]["status"] = "timeout"
    report["summary"]["survived"] = 0
    report["summary"]["timeout"] = 1
    assert len(check(tmp_path, report, run, baseline)) == 1


def test_fingerprints_are_stable_across_line_moves_but_distinguish_mutations() -> None:
    source = "def bounded(value):\n    return value > 0\n"
    moved = "# New unrelated header.\n\n" + source
    original, _ = transform_source(source, "target.py")
    relocated, _ = transform_source(moved, "target.py")
    first = [describe(g, source, "target.py")["fingerprint"] for g in original]
    second = [describe(g, moved, "target.py")["fingerprint"] for g in relocated]
    assert first == second
    assert len(set(first)) == len(first)


def test_fingerprint_format_is_portable_across_supported_python_versions() -> None:
    # Imports gain is_lazy in 3.15, type parameters gain default_value in 3.13,
    # and ast.dump changes its handling of empty fields and Load contexts.
    source = (
        "from dataclasses import dataclass\n\n"
        "@dataclass(frozen=True)\n"
        "class Record[T]:\n"
        "    value: T\n\n"
        "def bounded[T](value: T):\n"
        "    return value is None\n"
    )
    mutants, _ = transform_source(source, "target.py")
    mutant = next(g for g in mutants if g.line_number == 3)
    detail = describe(mutant, source, "target.py")
    assert detail["fingerprint"] == (
        "5cb55843d8dce16029f51d4caa56c9444e0bb99108b1fb77fad6486fa6d95b31"
    )
    assert detail["source_hash"] == (
        "f9d6283db108408ebddf571e1179a55b6e663712ea97959a7fc8cf9c3d6ecf44"
    )


@pytest.mark.parametrize(
    ("original", "changed", "minimum"),
    [
        ("import example", "lazy import example", (3, 15)),
        ("type Record[T] = list[T]", "type Record[T = int] = list[T]", (3, 13)),
    ],
)
def test_new_ast_fields_retain_semantic_changes(
    original: str, changed: str, minimum: tuple[int, int]
) -> None:
    if sys.version_info < minimum:
        pytest.skip("Syntax requires a newer Python version")
    assert _ast_dump(ast.parse(original)) != _ast_dump(ast.parse(changed))


@pytest.mark.parametrize("review", ["exceptions", "unchanged_legacy_functions"])
def test_reviews_expire_after_real_source_edits(
    tmp_path: Path, campaign: tuple, review: str
) -> None:
    report, run, baseline = campaign
    target = tmp_path / "target.py"
    source = target.read_text()
    mutants, _ = transform_source(source, str(target))
    baseline[review] = [
        {**describe(mutants[0], source, "target.py"), "reason": "Reviewed original source."}
    ]
    report["results"][0]["status"] = "survived"
    report["summary"]["zapped"] -= 1
    report["summary"]["survived"] = 1
    assert check(tmp_path, report, run, baseline) == []

    changed = source.replace("> 0", "> 1")
    target.write_text(changed)
    run["sources"]["target.py"] = hashlib.sha256(target.read_bytes()).hexdigest()
    assert len(check(tmp_path, report, run, baseline)) == 1


def test_legacy_exclusion_expires_when_function_changes(tmp_path: Path, campaign: tuple) -> None:
    report, run, baseline = campaign
    source = (tmp_path / "target.py").read_text()
    mutants, _ = transform_source(source, str(tmp_path / "target.py"))
    detail = describe(mutants[0], source, "target.py")
    baseline["unchanged_legacy_functions"] = [
        {
            "path": "target.py",
            "function": "bounded",
            "source_hash": detail["source_hash"],
        }
    ]
    report["results"][0]["status"] = "survived"
    report["summary"]["zapped"] -= 1
    report["summary"]["survived"] = 1
    assert check(tmp_path, report, run, baseline) == []
    baseline["unchanged_legacy_functions"][0]["source_hash"] = "previous function contents"
    assert len(check(tmp_path, report, run, baseline)) == 1
