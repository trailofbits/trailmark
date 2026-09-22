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


def describe(gremlin: Gremlin, source: str, path: str) -> dict[str, Any]:
    tree = ast.parse(source)
    owners = [
        (name, node)
        for name, node in functions_in(tree)
        if node.lineno <= gremlin.line_number <= (node.end_lineno or node.lineno)
    ]
    name, owner = (
        min(owners, key=lambda item: (item[1].end_lineno or item[1].lineno) - item[1].lineno)
        if owners
        else ("<module>", tree)
    )
    context = {
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
    fingerprint = hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()
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


def check(
    root: Path, report: dict[str, Any], run: dict[str, Any], baseline: dict[str, Any]
) -> list[str]:
    if run["pytest_gremlins"] != VERSION or baseline["pytest_gremlins"] != VERSION:
        raise ValueError("Mutation version does not match the reviewed baseline")
    if run["runner"] != RUNNER:
        raise ValueError("Report lacks validated pytest execution")
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
    reviewed = {row["fingerprint"]: row for row in baseline["exceptions"]}
    if len(reviewed) != len(baseline["exceptions"]) or any(
        not r.get("reason", "").strip() for r in reviewed.values()
    ):
        raise ValueError("Baseline exceptions must be unique and include a review reason")
    legacy = {
        (entry["path"], entry["function"]): entry["source_hash"]
        for entry in baseline.get("unchanged_legacy_functions", [])
    }
    failures = []
    for path, scope in baseline["scope"].items():
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
        for mutant in mutants:
            detail = describe(mutant, source, path)
            if scope != "*" and detail["function"] not in scope:
                continue
            if legacy.get((path, detail["function"])) == detail["source_hash"]:
                continue
            row = by_id[mutant.gremlin_id]
            status = row["status"]
            if not row.get("selected_tests"):
                status = "no tests"
            exception = reviewed.get(detail["fingerprint"])
            if status == "zapped" or (status == "survived" and exception is not None):
                continue
            failures.append(json.dumps({**detail, "status": status}, sort_keys=True))
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
