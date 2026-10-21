"""Gate reviewed mutation scope using source-and-diff fingerprints, not ordinal IDs."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import sys
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pytest_gremlins.instrumentation.gremlin import Gremlin
from pytest_gremlins.instrumentation.transformer import transform_source

from tests.run_mutations import RUNNER, VERSION


class _RemoveSwitches(ast.NodeTransformer):
    def visit_IfExp(self, node: ast.IfExp) -> ast.AST:  # noqa: N802
        if (
            isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "__gremlin_active__"
        ):
            return self.visit(node.orelse)
        return self.generic_visit(node)


def _code(node: ast.AST) -> str:
    return ast.dump(_RemoveSwitches().visit(copy.deepcopy(node)))


def functions_in(
    node: ast.AST, parents: tuple[str, ...] = ()
) -> Iterator[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        parents = (*parents, node.name)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield ".".join(parents), node
    for child in ast.iter_child_nodes(node):
        yield from functions_in(child, parents)


def describe(
    gremlin: Gremlin,
    source: str,
    path: str,
    context: tuple[ast.Module, list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]]
    | None = None,
) -> dict[str, Any]:
    if context is None:
        tree = ast.parse(source)
        context = tree, list(functions_in(tree))
    tree, owners = context
    matches = [
        (name, node)
        for name, node in owners
        if node.lineno <= gremlin.line_number <= (node.end_lineno or node.lineno)
    ]
    name, owner = (
        min(matches, key=lambda item: (item[1].end_lineno or item[1].lineno) - item[1].lineno)
        if matches
        else ("<module>", tree)
    )
    fingerprint_context = {
        "path": path,
        "function": name,
        "function_source": ast.dump(owner),
        "offset": [
            gremlin.line_number - getattr(owner, "lineno", 1),
            gremlin.original_node.col_offset,
        ],
        "operator": gremlin.operator_name,
        "original": _code(gremlin.original_node),
        "mutated": _code(gremlin.mutated_node),
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_context, sort_keys=True).encode()
    ).hexdigest()
    return {
        "fingerprint": fingerprint,
        "source_hash": hashlib.sha256(ast.dump(owner).encode()).hexdigest(),
        "path": path,
        "function": name,
        "line": gremlin.line_number,
        "operator": gremlin.operator_name,
        "original": ast.unparse(_RemoveSwitches().visit(copy.deepcopy(gremlin.original_node))),
        "mutated": ast.unparse(_RemoveSwitches().visit(copy.deepcopy(gremlin.mutated_node))),
    }


def _validate_versions(run: dict[str, Any], baseline: dict[str, Any]) -> None:
    if run["pytest_gremlins"] != VERSION or baseline["pytest_gremlins"] != VERSION:
        raise ValueError("Mutation version does not match the reviewed baseline")
    if run["runner"] != RUNNER:
        raise ValueError("Report lacks validated pytest execution")


def _validate_report(report: dict[str, Any], baseline: dict[str, Any]) -> None:
    skipped = report.get("skipped_files", {})
    if not isinstance(skipped, dict) or skipped:
        raise ValueError("Mutation report contains skipped files or invalid skip metadata")
    results = report["results"]
    if not results or len(results) != report["summary"]["total"]:
        raise ValueError("Missing or incomplete mutation results")
    actual_counts = Counter(row["status"] for row in results)
    statuses = {"zapped", "survived", "error", "timeout", "pardoned"}
    if actual_counts.keys() - statuses:
        raise ValueError("Unknown mutation outcome")
    if not baseline["scope"]:
        raise ValueError("Mutation gate has no scope")
    for status in statuses:
        if report["summary"].get(status, 0) != actual_counts[status]:
            raise ValueError(f"Inconsistent {status} count")


def _reviewed_exceptions(baseline: dict[str, Any]) -> dict[str, Any]:
    reviewed = {row["fingerprint"]: row for row in baseline["exceptions"]}
    if len(reviewed) != len(baseline["exceptions"]) or any(
        not r.get("reason", "").strip() for r in reviewed.values()
    ):
        raise ValueError("Baseline exceptions must be unique and include a review reason")
    return reviewed


def _outcome(row: dict[str, Any], detail: dict[str, Any], reviewed: dict[str, Any]) -> str | None:
    status = row["status"] if row.get("selected_tests") else "no tests"
    if status == "zapped" or (status == "survived" and detail["fingerprint"] in reviewed):
        return None
    return json.dumps({**detail, "status": status}, sort_keys=True)


def _scope_failures(
    root: Path,
    path: str,
    scope: str | list[str],
    run: dict[str, Any],
    results: list[dict[str, Any]],
    *,
    reviewed: dict[str, Any],
    legacy: dict[tuple[str, str], str],
) -> list[str]:
    source_path = root / path
    source = source_path.read_text()
    if run["sources"].get(path) != hashlib.sha256(source_path.read_bytes()).hexdigest():
        raise ValueError(f"Missing or stale mutation input: {path}")
    rows = [r for r in results if Path(r["file_path"]).resolve() == source_path.resolve()]
    if not rows:
        raise ValueError(f"Mutation scope missing from report: {path}")
    mutants, _ = transform_source(source, rows[0]["file_path"])
    by_id = {r["gremlin_id"]: r for r in rows}
    if len(by_id) != len(rows) or set(by_id) != {g.gremlin_id for g in mutants}:
        raise ValueError(f"Mutation inventory does not match source: {path}")
    tree = ast.parse(source)
    context = tree, list(functions_in(tree))
    failures = []
    for mutant in mutants:
        detail = describe(mutant, source, path, context)
        if scope != "*" and detail["function"] not in scope:
            continue
        if legacy.get((path, detail["function"])) == detail["source_hash"]:
            continue
        failure = _outcome(by_id[mutant.gremlin_id], detail, reviewed)
        if failure is not None:
            failures.append(failure)
    return failures


def check(
    root: Path, report: dict[str, Any], run: dict[str, Any], baseline: dict[str, Any]
) -> list[str]:
    _validate_versions(run, baseline)
    _validate_report(report, baseline)
    reviewed = _reviewed_exceptions(baseline)
    legacy = {
        (entry["path"], entry["function"]): entry["source_hash"]
        for entry in baseline.get("unchanged_legacy_functions", [])
    }
    failures = []
    for path, scope in baseline["scope"].items():
        failures.extend(
            _scope_failures(
                root, path, scope, run, report["results"], reviewed=reviewed, legacy=legacy
            )
        )
    return failures


def main() -> int:
    root = Path.cwd()
    try:
        report = json.loads((root / "coverage/gremlins/gremlins.json").read_text())
        run = json.loads((root / "coverage/gremlins/run.json").read_text())
        baseline = json.loads((root / "tests/mutation_baseline.json").read_text())
        failures = check(root, report, run, baseline)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Mutation gate failed: {exc}", file=sys.stderr)
        return 1
    for failure in failures:
        print(failure)
    print(f"Mutation gate: {len(failures)} unreviewed or inconclusive outcomes")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
