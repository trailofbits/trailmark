"""Compare complete mutation campaigns without treating missing evidence as success."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from pytest_gremlins.instrumentation.transformer import transform_source

from tests.mutation_inventory import inventory


def read_report(path: Path) -> Any:
    """Accept archived reports without requiring a second uncompressed copy."""
    if path.exists():
        return json.loads(path.read_text())
    with gzip.open(path.with_suffix(path.suffix + ".gz"), "rt") as source:
        return json.load(source)


def load_campaign(directory: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Load one campaign, or a complete set of its disjoint shard directories."""
    paths = (
        [directory]
        if (directory / "run.json").is_file()
        else sorted(path.parent for path in directory.rglob("run.json"))
    )
    if not paths:
        raise ValueError("Missing campaign reports")
    context = None
    shards = set()
    results = {}
    total_shards = 0
    for path in paths:
        run = json.loads((path / "run.json").read_text())
        index, count = run["shard"]
        if not 1 <= index <= count or (index, count) in shards:
            raise ValueError("Duplicate or invalid campaign shard")
        shards.add((index, count))
        total_shards = count
        current = {
            key: run[key]
            for key in (
                "inputs",
                "sources",
                "targets",
                "tests",
                "runner",
                "pytest_gremlins",
                "test_order",
                "test_selection",
                "test_group",
                "workers",
                "kill_confirmation",
            )
        }
        if context is not None and current != context:
            raise ValueError("Mismatched campaign shards")
        context = current
        if run["baseline_validation"] not in {"local", "shared"} or run[
            "kill_confirmation"
        ] not in {"baseline-orders", "failure-prefix"}:
            raise ValueError("Missing validated baseline or confirmation")
        report = read_report(path / "gremlins.json")
        inventory = json.loads((path / "inventory.json").read_text())
        rows = report["results"]
        by_id = {row["gremlin_id"]: row for row in rows}
        expected = {row["gremlin_id"]: row for row in inventory}
        if (
            len(by_id) != len(rows)
            or len(expected) != len(inventory)
            or by_id.keys() != expected.keys()
            or report.get("skipped_files")
        ):
            raise ValueError("Incomplete or duplicate mutation inventory")
        counts = Counter(row["status"] for row in rows)
        if report["summary"]["total"] != len(rows) or any(
            report["summary"].get(status, 0) != count for status, count in counts.items()
        ):
            raise ValueError("Inconsistent mutation summary")
        for identifier, row in by_id.items():
            if row["status"] not in {"zapped", "survived"} or not row["selected_tests"]:
                raise ValueError("Inconclusive mutation outcome")
            detail = expected[identifier]
            fingerprint = detail["fingerprint"]
            if fingerprint in results:
                raise ValueError("Duplicate mutation fingerprint")
            results[fingerprint] = {
                **detail,
                "status": row["status"],
                "killing_test": row.get("killing_test"),
            }
    if shards != {(index, total_shards) for index in range(1, total_shards + 1)}:
        raise ValueError("Incomplete campaign shards")
    assert context is not None
    return context, results


def compare(
    reference: Path,
    candidate: Path,
    *,
    root: Path | None = None,
    require_full_reference: bool = True,
) -> dict[str, Any]:
    """Report every difference, rejecting incomparable sources and test inputs."""
    before, full = load_campaign(reference)
    after, fast = load_campaign(candidate)
    for key in ("inputs", "sources", "targets", "tests", "runner", "pytest_gremlins", "workers"):
        if before[key] != after[key]:
            raise ValueError(f"Incomparable campaign {key}")
    if before["test_group"] != "full" or before["test_selection"] not in {"full", "coverage"}:
        raise ValueError("Reference must use the full test pool")
    if require_full_reference and before["test_selection"] != "full":
        raise ValueError("Reference must use the full test pool without coverage filtering")
    if full.keys() != fast.keys() or not full:
        raise ValueError("Campaign mutation inventories differ or are empty")
    root = (root or Path.cwd()).resolve()
    mutations = []
    for name, expected_hash in before["sources"].items():
        path = (root / name).resolve()
        if (
            not path.is_relative_to(root)
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash
        ):
            raise ValueError(f"Missing or changed campaign source: {name}")
        discovered, _ = transform_source(path.read_text(), str(path))
        mutations.extend(discovered)
    actual = {row["fingerprint"] for row in inventory(mutations, root)}
    if actual != full.keys():
        raise ValueError("Campaign inventory does not match application sources")
    differences = [
        {
            "fingerprint": key,
            "path": row["path"],
            "function": row["function"],
            "line": row["line"],
            "reference": row["status"],
            "candidate": fast[key]["status"],
        }
        for key, row in sorted(full.items())
        if row["status"] != fast[key]["status"]
    ]
    return {
        "mutants": len(full),
        "reference_kills": sum(row["status"] == "zapped" for row in full.values()),
        "candidate_kills": sum(row["status"] == "zapped" for row in fast.values()),
        "lost_kills": sum(row["reference"] == "zapped" for row in differences),
        "differences": differences,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument(
        "--allow-coverage-reference",
        action="store_true",
        help="Compare against coverage-selected full-pool results instead of an unfiltered audit",
    )
    args = parser.parse_args()
    try:
        result = compare(
            args.reference,
            args.candidate,
            require_full_reference=not args.allow_coverage_reference,
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"Mutation comparison failed: {exc}\n")
    print(json.dumps(result, indent=2))
    return int(bool(result["lost_kills"]))


if __name__ == "__main__":
    raise SystemExit(main())
