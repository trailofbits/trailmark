"""Canonical real-parser metamorphic controls and deliberate graph corruptions."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from tests.graph_eval.compare import compare_reference
from tests.graph_eval.measure import measure_project
from tests.graph_eval.util import write_json


def _location(line: int, end_line: int | None = None) -> dict[str, Any]:
    return {
        "path": "main.py",
        "line": line,
        "end_line": end_line or line,
        "column": None,
        "end_column": None,
    }


def _reference(
    case_id: str,
    entry: str,
    leaf: str,
    entry_line: int,
    leaf_line: int,
    extra_line: int | None = None,
) -> dict[str, Any]:
    declarations = []
    for key, name, line in (("entry", entry, entry_line), ("leaf", leaf, leaf_line)):
        declarations.append(
            {
                "key": key,
                "name": name,
                "role": "function",
                "graph_kind": "function",
                "owner": "file:main.py",
                "location": _location(line, line + 1),
                "evidence": f"main.py:{line} declares {name}",
            }
        )
    if extra_line:
        declarations.append(
            {
                "key": "unreachable",
                "name": "unrelated",
                "role": "function",
                "graph_kind": "function",
                "owner": "file:main.py",
                "location": _location(extra_line, extra_line + 1),
                "evidence": "A declaration with no calls or callers",
            }
        )
    return {
        "schema_version": 1,
        "case_id": case_id,
        "status": "complete",
        "inventory_complete": True,
        "declarations": declarations,
        "assertions": [
            {
                "id": "entry-to-leaf",
                "subject": "entry",
                "kind": "calls",
                "site": _location(entry_line + 1),
                "target_key": "leaf",
                "target_name": leaf,
                "expectation": "present",
                "resolution": "resolved",
                "allowed_confidence": ["certain"],
                "context": "runtime",
                "evidence": f"return {leaf}()",
            }
        ],
        "questions": [
            {
                "id": "callees",
                "operation": "callees",
                "subject": "entry",
                "target": None,
                "expected": ["leaf"],
                "question": "Which function does the entry call?",
                "evidence": f"return {leaf}()",
            },
            {
                "id": "callers",
                "operation": "callers",
                "subject": "leaf",
                "target": None,
                "expected": ["entry"],
                "question": "Which function calls the leaf?",
                "evidence": f"{entry} calls {leaf}",
            },
            {
                "id": "path",
                "operation": "path",
                "subject": "entry",
                "target": "leaf",
                "expected": ["entry", "leaf"],
                "question": "How does the entry reach the leaf?",
                "evidence": f"{entry} directly calls {leaf}",
            },
        ],
        "notes": ["Hand-authored canonical control, not an agent-derived pilot reference."],
    }


def _case(case_id: str, source: str) -> dict[str, Any]:
    return {
        "id": case_id,
        "language": "python",
        "profile": "runtime",
        "primary": [{"path": "main.py", "start_line": 1, "end_line": len(source.splitlines())}],
        "context": [],
    }


def _has_issue(comparison: dict[str, Any], issue: str) -> bool:
    return any(issue in claim.get("issues", []) for claim in comparison["discrepancies"])


def run_controls(destination: Path, python: Path) -> dict[str, Any]:
    """Measure tiny source variants with a frozen interpreter; use no model jobs."""
    destination.mkdir(parents=True, exist_ok=True)
    baseline_source = "def f():\n    return g()\n\ndef g():\n    return 1\n"
    variants = [
        ("baseline", baseline_source, "f", "g", 1, 4, None),
        (
            "alpha_rename",
            "def start():\n    return finish()\n\ndef finish():\n    return 1\n",
            "start",
            "finish",
            1,
            4,
            None,
        ),
        (
            "definition_order",
            "def g():\n    return 1\n\ndef f():\n    return g()\n",
            "f",
            "g",
            4,
            1,
            None,
        ),
        (
            "unreachable_helper",
            baseline_source + "\ndef unrelated():\n    return 0\n",
            "f",
            "g",
            1,
            4,
            7,
        ),
    ]
    rows = []
    baseline: tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]] | None = None
    for name, source, entry, leaf, entry_line, leaf_line, extra_line in variants:
        root = destination / name / "source"
        root.mkdir(parents=True, exist_ok=True)
        (root / "main.py").write_text(source)
        case = _case(name, source)
        reference = _reference(name, entry, leaf, entry_line, leaf_line, extra_line)
        measurement = measure_project(
            root, "python", destination / name / "measurement", python=python
        )
        comparison = compare_reference(reference, measurement, case)
        write_json(destination / name / "reference.json", reference)
        write_json(destination / name / "comparison.json", comparison)
        okay = (
            comparison["status"] == "compared"
            and not comparison["discrepancies"]
            and comparison["metrics"]["call_sites"]["retained"] == 1
            and comparison["metrics"]["targets"]["resolved"] == 1
        )
        rows.append(
            {
                "id": name,
                "type": "real_parser_metamorphic",
                "status": "passed" if okay else "failed",
                "measurement_status": measurement["status"],
                "discrepancy_count": len(comparison["discrepancies"]),
                "elapsed_seconds": measurement.get("elapsed_seconds"),
            }
        )
        if name == "baseline" and okay:
            baseline = measurement, comparison, reference, case
    if baseline is None:
        rows.append(
            {
                "id": "corrupted_graphs",
                "type": "comparator_corruption",
                "status": "not_run",
                "reason": "Real-parser baseline failed",
            }
        )
    else:
        measurement, comparison, reference, case = baseline
        entry_id, leaf_id = comparison["mapping"]["entry"], comparison["mapping"]["leaf"]
        edge_index = next(
            index
            for index, edge in enumerate(measurement["graph"]["edges"])
            if edge["kind"] == "calls" and edge["source"] == entry_id and edge["target"] == leaf_id
        )
        for name in (
            "drop_call",
            "spurious_call",
            "wrong_namesake",
            "wrong_owner",
            "wrong_kind",
            "ambiguous_site",
            "proxy_retention",
            "suppressed_diagnostic",
        ):
            mutated, expected = copy.deepcopy(measurement), copy.deepcopy(reference)
            edge = mutated["graph"]["edges"][edge_index]
            issue = ""
            if name == "drop_call":
                mutated["graph"]["edges"].pop(edge_index)
                issue = "missing_relationship"
            elif name == "spurious_call":
                extra = copy.deepcopy(edge)
                extra["location"] = _location(1)
                mutated["graph"]["edges"].append(extra)
                issue = "extra_relationship"
            elif name == "wrong_namesake":
                namesake = copy.deepcopy(mutated["graph"]["nodes"][leaf_id])
                namesake["location"] = {**_location(1), "path": "other.py"}
                mutated["graph"]["nodes"]["other:g"] = namesake
                edge["target"] = "other:g"
                issue = "wrong_target"
            elif name == "wrong_owner":
                edge["source"] = leaf_id
                issue = "wrong_owner"
            elif name == "wrong_kind":
                edge["kind"] = "references"
                issue = "wrong_kind"
            elif name == "ambiguous_site":
                mutated["graph"]["edges"].append(copy.deepcopy(edge))
                issue = "ambiguous_site"
            elif name == "proxy_retention":
                edge["target"] = "proxy.unresolved:g"
                mutated["graph"]["nodes"][edge["target"]] = {
                    "name": "g",
                    "kind": "proxy",
                    "origin": "proxy",
                }
                issue = "unresolved_target"
            else:
                expected["assertions"].append(
                    {
                        **copy.deepcopy(expected["assertions"][0]),
                        "id": "expected-diagnostic",
                        "kind": "diagnostic",
                        "target_key": None,
                        "target_name": "unsupported_syntax",
                        "resolution": "either",
                        "evidence": "Synthetic warning-suppression control",
                    }
                )
                mutated["diagnostics"] = []
                issue = "missing_diagnostic"
            observed = compare_reference(expected, mutated, case)
            okay = _has_issue(observed, issue)
            if name == "proxy_retention":
                okay = okay and observed["metrics"]["call_sites"]["retained"] == 1
            folder = destination / name
            write_json(folder / "measurement.json", mutated)
            write_json(folder / "reference.json", expected)
            write_json(folder / "comparison.json", observed)
            rows.append(
                {
                    "id": name,
                    "type": "comparator_corruption",
                    "status": "passed" if okay else "failed",
                    "expected_detection": issue,
                }
            )
    report = {
        "schema_version": 1,
        "status": "ok" if all(row["status"] == "passed" for row in rows) else "failed",
        "python": str(python.resolve()),
        "model_jobs": 0,
        "controls": rows,
    }
    write_json(destination / "controls.json", report)
    return report
