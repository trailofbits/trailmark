"""Measure mutation pools end to end; never use elapsed time as a pytest assertion."""

from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from tests.compare_mutations import compare, load_campaign
from tests.mutation_controls import input_fingerprint


def archive_report(directory: Path) -> None:
    """Keep full test selections while avoiding multi-gigabyte duplicate reports."""
    path = directory / "gremlins.json"
    with path.open("rb") as source, gzip.open(path.with_suffix(".json.gz"), "wb") as destination:
        shutil.copyfileobj(source, destination)
    path.unlink()


def benchmark(output: Path, *, repeat: int, workers: int) -> dict[str, Any]:
    """Run one unfiltered reference and alternating, uncached unsharded measurements."""
    root = Path.cwd()
    output = output.resolve()
    if output.is_relative_to(root) and not output.is_relative_to(root / "coverage"):
        raise ValueError("Put benchmark output outside the checkout or under coverage/")
    output.mkdir(parents=True, exist_ok=False)
    os.environ["TRAILMARK_MUTATION_TESTING"] = "1"
    os.environ["TRAILMARK_REQUIRE_BINARY_TOOLS"] = "1"
    if os.environ.get("PYTEST_ADDOPTS"):
        raise ValueError("Unset PYTEST_ADDOPTS for the controlled benchmark")
    fingerprint = input_fingerprint(root)
    timings: list[dict[str, Any]] = []

    def execute(command: list[str], destination: Path) -> float:
        started = time.perf_counter()
        with destination.open("w") as log:
            completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False)  # noqa: S603
        if completed.returncode:
            raise ValueError(f"Benchmark command failed; see {destination}")
        if input_fingerprint(root) != fingerprint:
            raise ValueError("Benchmark inputs changed")
        return time.perf_counter() - started

    def campaign(name: str, *, group: str, selection: str = "coverage") -> Path:
        directory = output / name
        command = [
            sys.executable,
            "-m",
            "tests.run_mutations",
            f"--test-group={group}",
            f"--test-selection={selection}",
            f"--workers={workers}",
            f"--report-dir={directory}",
        ]
        if selection == "full":
            command.append("--test-order=collection")
        execute(command, output / f"{name}.log")
        return directory

    reference = campaign("reference", group="full", selection="full")
    _, reference_rows = load_campaign(reference)
    archive_report(reference)
    for iteration in range(repeat):
        order = ("full", "fast", "stock") if iteration % 2 == 0 else ("stock", "fast", "full")
        for group in order:
            name = f"{iteration + 1}-{group}"
            print(f"Measuring {name}", flush=True)
            clean = [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests",
                "-m",
                "mutation_fast and not mutation_infrastructure"
                if group == "fast"
                else "not mutation_infrastructure",
            ]
            clean_seconds = execute(clean, output / f"{name}-clean.log")
            started = time.perf_counter()
            if group != "stock":
                directory = campaign(name, group=group)
                comparison = compare(reference, directory)
                (output / f"{name}-comparison.json").write_text(
                    json.dumps(comparison, indent=2) + "\n"
                )
                if comparison["lost_kills"]:
                    raise ValueError(f"Lost kills in {name}; inspect its comparison report")
                archive_report(directory)
            else:
                directory = output / name
                command = [
                    *clean,
                    "--gremlins",
                    "--gremlin-executor=subprocess",
                    f"--gremlin-workers={workers}",
                    "--gremlin-targets=src/trailmark",
                    "--gremlin-report=json",
                    f"--gremlins-html-dir={directory}",
                ]
                execute(command, output / f"{name}.log")
                # The pinned plugin hardcodes its JSON path independently of html-dir.
                directory.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(
                    root / "coverage/gremlins/gremlins.json", directory / "gremlins.json"
                )
                stock = json.loads((directory / "gremlins.json").read_text())
                by_id = {row["gremlin_id"]: row for row in stock["results"]}
                if len(by_id) != len(reference_rows) or set(by_id) != {
                    row["gremlin_id"] for row in reference_rows.values()
                }:
                    raise ValueError("Stock mutation inventory differs")
                # Persist stock outcomes for a comparison after every arm has finished.
                (output / f"{name}-outcomes.json").write_text(
                    json.dumps({key: row["status"] for key, row in by_id.items()}) + "\n"
                )
                archive_report(directory)
                (root / "coverage/gremlins/gremlins.json").unlink()
            timings.append(
                {
                    "repeat": iteration + 1,
                    "group": group,
                    "clean_baseline_seconds": clean_seconds,
                    "campaign_and_validation_seconds": time.perf_counter() - started,
                    "total_seconds": clean_seconds + time.perf_counter() - started,
                }
            )
            (output / "timings.json").write_text(json.dumps(timings, indent=2) + "\n")
        _, candidate_rows = load_campaign(output / f"{iteration + 1}-fast")
        fast_by_id = {row["gremlin_id"]: row for row in candidate_rows.values()}
        stock_rows = json.loads((output / f"{iteration + 1}-stock-outcomes.json").read_text())
        differences = [
            {"gremlin_id": key, "stock": status, "fast": fast_by_id[key]["status"]}
            for key, status in stock_rows.items()
            if status != fast_by_id[key]["status"]
        ]
        (output / f"{iteration + 1}-stock-comparison.json").write_text(
            json.dumps(differences, indent=2) + "\n"
        )
        if any(row["stock"] == "zapped" and row["fast"] != "zapped" for row in differences):
            raise ValueError("Fast pool lost a stock-plugin kill")
    medians = {
        group: statistics.median(row["total_seconds"] for row in timings if row["group"] == group)
        for group in ("full", "fast", "stock")
    }
    pairs = [
        {row["group"]: row["total_seconds"] for row in timings if row["repeat"] == index}
        for index in range(1, repeat + 1)
    ]
    reduction = 1 - medians["fast"] / medians["full"]
    return {
        "inputs": fingerprint,
        "workers": workers,
        "repeat": repeat,
        "median_seconds": medians,
        "reduction": reduction,
        "release_eligible": repeat >= 3
        and workers == 4
        and reduction >= 0.20
        and all(pair["fast"] < pair["full"] for pair in pairs),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if args.repeat < 1 or args.workers < 1:
        parser.error("Repeat and workers must be positive")
    try:
        result = benchmark(args.output_dir, repeat=args.repeat, workers=args.workers)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"Mutation benchmark failed: {exc}\n")
    (args.output_dir / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return int(not result["release_eligible"])


if __name__ == "__main__":
    raise SystemExit(main())
