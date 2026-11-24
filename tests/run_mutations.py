"""Run pytest-gremlins with complete pytest semantics and baseline controls.

Run as ``uv run python -m tests.run_mutations --targets=src/trailmark/...``.
Use upstream's full-pytest bootstrap and import handling, adding coverage-driven
selection, component ordering, shared baselines, and failure-prefix controls.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import subprocess
import sys
from collections import defaultdict
from collections.abc import Callable, Sequence
from importlib.metadata import version
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from pytest_gremlins import plugin
from pytest_gremlins.instrumentation.gremlin import Gremlin
from pytest_gremlins.node_id_file import command_with_node_ids_file

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.mutation_bootstrap import baseline_environment as _baseline_environment
from tests.mutation_bootstrap import record_source_collection_exits, record_test_execution
from tests.mutation_bootstrap import test_command as _test_command
from tests.mutation_confirmation import FailurePrefixControls
from tests.mutation_controls import baseline_receipt, input_fingerprint, validate_baselines
from tests.mutation_coverage import collect_plan, validate_plan
from tests.mutation_coverage import select_tests as coverage_tests
from tests.mutation_order import prioritize_tests, relative_node_ids

VERSION = "1.14.3"
RUNNER = "trailmark-pytest-coverage-v9"


def parse_shard(value: str) -> tuple[int, int]:
    try:
        index, count = map(int, value.split("/"))
        if 1 <= index <= count:
            return index, count
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("Shard must be INDEX/TOTAL with 1 <= INDEX <= TOTAL")


def select_shard(gremlins: list[Gremlin], shard: tuple[int, int]) -> list[Gremlin]:
    """Deal each file's mutants round-robin, independent of discovery order or root."""
    index, count = shard
    by_file: dict[str, list[Gremlin]] = defaultdict(list)
    for gremlin in gremlins:
        by_file[gremlin.file_path].append(gremlin)
    return [g for path in sorted(by_file) for g in by_file[path][index - 1 :: count]]


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--targets", default="src/trailmark")
    scope.add_argument(
        "--reviewed", action="store_true", help="Mutate every file in mutation_baseline.json"
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--shard", type=parse_shard, default=(1, 1), help="Run partition INDEX/TOTAL (one-based)"
    )
    parser.add_argument("--test-order", choices=["component", "collection"], default="component")
    parser.add_argument("--test-selection", choices=["coverage", "full"], default="coverage")
    controls = parser.add_mutually_exclusive_group()
    controls.add_argument(
        "--baseline-shard",
        type=parse_shard,
        help="Validate only partition INDEX/TOTAL of the instrumented test orders",
    )
    controls.add_argument(
        "--baselines",
        type=Path,
        help="Reuse a complete set of matching shared baseline receipts",
    )
    parser.add_argument("tests", nargs="*", default=["tests"])
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    if version("pytest-gremlins") != VERSION:
        parser.error(
            f"Revalidate the mutation runner adapter before changing pytest-gremlins {VERSION}"
        )
    if os.environ.get("TRAILMARK_UPDATE_SNAPSHOTS") == "1":
        parser.error("Snapshot updates are disabled in mutation testing")
    if args.baseline_shard and args.shard != (1, 1):
        parser.error("--baseline-shard cannot be combined with --shard")
    if args.test_selection == "coverage" and args.baseline_shard not in {None, (1, 1)}:
        parser.error("Coverage selection uses a single --baseline-shard=1/1")
    if args.reviewed:
        baseline_path = Path(__file__).with_name("mutation_baseline.json")
        args.targets = ",".join(json.loads(baseline_path.read_text())["scope"])
        if not args.targets:
            parser.error("The reviewed mutation scope is empty")
    return args


def _source_hashes(targets: str) -> dict[str, str]:
    source_paths = []
    for target in targets.split(","):
        path = Path(target)
        source_paths.extend(path.rglob("*.py") if path.is_dir() else [path])
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths}


def _check_instrumented_orders(
    directory: Path,
    instrumented_asts: dict[str, ast.Module],
    args: argparse.Namespace,
    fingerprint: str,
    plans: list[dict[str, Any]],
) -> dict[str, object]:
    env = _baseline_environment(directory)
    session = plugin._get_session()
    if session is None:
        raise pytest.UsageError("Missing mutation session for baseline validation")
    if session.skipped_files:
        raise pytest.UsageError(f"Mutation instrumentation skipped files: {session.skipped_files}")
    command = _test_command(directory, session)
    tests = list(session.test_node_ids)
    orders = sorted(
        {
            tuple(prioritize_tests(path, tests) if args.test_order == "component" else tests)
            for path in instrumented_asts
        }
    )
    if args.test_selection == "coverage":
        orders = [tuple(tests)]
    context = {
        "inputs": fingerprint,
        "pytest_gremlins": VERSION,
        "runner": RUNNER,
        "targets": args.targets,
        "tests": args.tests,
        "test_order": args.test_order,
        "test_selection": args.test_selection,
        "addopts": session.preserved_addopts,
    }
    if args.test_selection == "coverage":
        sources = {
            Path(path).resolve().relative_to(Path.cwd()).as_posix() for path in instrumented_asts
        }
        try:
            if args.baselines is None:
                envelope = collect_plan(
                    directory,
                    [*command[2:], "-p", "no:gremlins"],
                    tests,
                    [str(Path(path).resolve()) for path in instrumented_asts],
                    instrumented=True,
                )
            else:
                envelope = json.loads((args.baselines / "coverage-plan.json").read_text())
            if not isinstance(envelope, dict):
                raise ValueError("Coverage selection evidence is not an object")
            context["coverage_plan"] = envelope["digest"]
            if args.baselines is None:
                envelope["context"] = context
            validate_plan(envelope, context, tests, sources)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise pytest.UsageError(f"Invalid shared coverage selection: {exc}") from exc
        plans.append(envelope)
    if args.baselines is not None:
        try:
            validate_baselines(args.baselines, context, orders)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise pytest.UsageError(f"Invalid shared mutation baselines: {exc}") from exc
        print(f"Reusing validated instrumented baseline orders: {len(orders)}", flush=True)
        return {}
    shard = args.baseline_shard or (1, 1)
    if args.test_selection == "coverage":
        return baseline_receipt(context, orders, shard)
    for index in range(shard[0] - 1, len(orders), shard[1]):
        print(f"Checking instrumented baseline order {index + 1}/{len(orders)}", flush=True)
        control = subprocess.run(  # noqa: S603
            command_with_node_ids_file(command, orders[index], directory),
            env=env,
            check=False,
            timeout=180,
        )
        if control.returncode:
            raise pytest.UsageError(
                "Instrumented baseline failed; mutation results would be invalid"
            )
    return baseline_receipt(context, orders, shard)


def _instrumented_writer(
    args: argparse.Namespace,
    fingerprint: str,
    receipts: list[dict[str, object]],
    plans: list[dict[str, Any]],
) -> Callable[
    [dict[str, ast.Module], Path, dict[str, list[str]] | None, dict[str, str] | None], Path
]:
    write_sources = plugin._write_instrumented_sources

    def write_and_check(
        instrumented_asts: dict[str, ast.Module],
        rootdir: Path,
        duplicates: dict[str, list[str]] | None = None,
        unwritable: dict[str, str] | None = None,
    ) -> Path:
        directory = write_sources(instrumented_asts, rootdir, duplicates, unwritable)
        if unwritable:
            raise pytest.UsageError(f"Mutation instrumentation skipped files: {unwritable}")
        record_source_collection_exits(directory)
        record_test_execution(directory)
        receipts.append(
            _check_instrumented_orders(directory, instrumented_asts, args, fingerprint, plans)
        )
        return directory

    return write_and_check


class _RequireCompleteInstrumentation:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.empty_shard = False

    @pytest.hookimpl(trylast=True)
    def pytest_collection_finish(self) -> None:
        session = plugin._get_session()
        # No writer runs when every target fails instrumentation.
        if session is not None and session.skipped_files:
            raise pytest.UsageError(
                f"Mutation instrumentation skipped files: {session.skipped_files}"
            )
        if session is not None:
            if self.args.baseline_shard:
                session.enabled = False
            selected = select_shard(session.gremlins, self.args.shard)
            self.empty_shard = bool(session.gremlins) and not selected
            session.gremlins = selected

    def pytest_runtestloop(self) -> bool:
        # A real pytest subprocess validated neutral instrumentation, or
        # collection checked matching shared evidence. The outer
        # session only collects tests and dispatches mutations at session finish.
        return True


def _run_campaign(
    args: argparse.Namespace,
    report_dir: Path,
    fingerprint: str,
    receipts: list[dict[str, object]],
    plans: list[dict[str, Any]],
) -> int:
    controls = FailurePrefixControls(
        args.workers, report_dir, checked_orders=args.test_selection == "full"
    )
    cache_result = plugin._cache_gremlin_result
    dispatch = plugin._dispatch_mutation_run
    selections: dict[str, list[str]] = {}

    def record_confirmed_result(
        gremlin: Gremlin,
        selected_tests: Sequence[str],
        result: plugin.GremlinResult,
        session: plugin.GremlinSession,
    ) -> None:
        # Native collection/timeout checks and our failure-prefix checks have
        # finished before a result reaches this publication point.
        cache_result(gremlin, selected_tests, result, session)
        with (report_dir / "progress.jsonl").open("a") as progress:
            progress.write(
                json.dumps(
                    {
                        "gremlin_id": gremlin.gremlin_id,
                        "file_path": str(gremlin.file_path),
                        "line_number": gremlin.line_number,
                        "operator": gremlin.operator_name,
                        "status": result.status.value,
                        "selected_test_count": len(result.selected_tests),
                        "execution_time_ms": result.execution_time_ms,
                    }
                )
                + "\n"
            )

    def record_result(
        gremlin: Gremlin,
        selected_tests: Sequence[str],
        result: plugin.GremlinResult,
        session: plugin.GremlinSession,
    ) -> None:
        if not controls.defer(gremlin, selected_tests, result, session):
            record_confirmed_result(gremlin, selected_tests, result, session)

    def dispatch_and_confirm(
        session: pytest.Session, gremlin_session: plugin.GremlinSession
    ) -> list[plugin.GremlinResult]:
        results = dispatch(session, gremlin_session)
        controls.flush(gremlin_session, record_confirmed_result)
        return results

    def uncached_tests(gremlin: Gremlin, session: plugin.GremlinSession) -> list[str]:
        tests = list(session.test_node_ids)
        if args.test_selection == "coverage":
            selected = coverage_tests(
                plans[0]["plan"], str(gremlin.file_path), gremlin.line_number, tests
            )
            if selected == tests or args.test_order == "collection":
                return selected
            return prioritize_tests(str(gremlin.file_path), selected)
        return (
            tests
            if args.test_order == "collection"
            else prioritize_tests(str(gremlin.file_path), tests)
        )

    def select_tests(gremlin: Gremlin, session: plugin.GremlinSession) -> list[str]:
        if gremlin.gremlin_id not in selections:
            selections[gremlin.gremlin_id] = uncached_tests(gremlin, session)
        return selections[gremlin.gremlin_id].copy()

    def reuse_coverage(session: plugin.GremlinSession, rootdir: Path) -> None:
        # The writer already collected coverage while validating neutral instrumentation.
        if args.test_selection == "coverage":
            print("Reusing coverage from the validated baseline", flush=True)

    instrumentation = _RequireCompleteInstrumentation(args)
    with (
        patch.object(
            plugin,
            "_write_instrumented_sources",
            side_effect=_instrumented_writer(args, fingerprint, receipts, plans),
        ),
        patch.object(plugin, "_make_node_ids_relative", side_effect=relative_node_ids),
        patch.object(plugin, "_select_tests_for_gremlin_prioritized", side_effect=select_tests),
        patch.object(plugin, "_collect_coverage", side_effect=reuse_coverage),
        patch.object(plugin, "_cache_gremlin_result", side_effect=record_result),
        patch.object(plugin, "_dispatch_mutation_run", side_effect=dispatch_and_confirm),
    ):
        result = pytest.main(
            [
                "-q",
                *args.tests,
                "-m",
                "not mutation_infrastructure",
                "--gremlins",
                "--gremlin-executor=subprocess",
                *(["--gremlin-no-coverage-filter"] if args.test_selection == "full" else []),
                f"--gremlin-workers={args.workers}",
                f"--gremlin-targets={args.targets}",
                "--gremlin-report=json",
                f"--gremlins-html-dir={report_dir}",
            ],
            plugins=[instrumentation],
        )
    if result == 0 and instrumentation.empty_shard and not args.baseline_shard:
        (report_dir / "gremlins.json").write_text(
            json.dumps({"summary": {"total": 0}, "results": []}) + "\n"
        )
    return result


def main() -> int:
    args = _arguments()
    os.environ["TRAILMARK_MUTATION_TESTING"] = "1"
    report_dir = Path("coverage/gremlins")
    report_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "run.json",
        "gremlins.json",
        "progress.jsonl",
        "coverage-plan.json",
        "prefix-summary.json",
        "executions.jsonl",
    ):
        (report_dir / name).unlink(missing_ok=True)
    receipt_path = None
    if args.baseline_shard:
        index, count = args.baseline_shard
        receipt_path = report_dir / f"baseline-{index}-of-{count}.json"
        receipt_path.unlink(missing_ok=True)
    fingerprint = input_fingerprint(Path.cwd())
    hashes = _source_hashes(args.targets)
    receipts: list[dict[str, object]] = []
    plans: list[dict[str, Any]] = []
    result = _run_campaign(args, report_dir, fingerprint, receipts, plans)
    if result == 0 and (not receipts or input_fingerprint(Path.cwd()) != fingerprint):
        print(
            "Mutation inputs changed or instrumented baseline validation is missing",
            file=sys.stderr,
        )
        return 1
    if result == 0 and plans:
        (report_dir / "coverage-plan.json").write_text(json.dumps(plans[0]) + "\n")
    if result == 0 and receipt_path is not None:
        receipt_path.write_text(json.dumps(receipts[0], indent=2) + "\n")
    elif result == 0:
        (report_dir / "run.json").write_text(
            json.dumps(
                {
                    "pytest_gremlins": VERSION,
                    "runner": RUNNER,
                    "inputs": fingerprint,
                    "test_order": args.test_order,
                    "test_selection": args.test_selection,
                    "selection_plan": plans[0]["digest"] if plans else None,
                    "shard": args.shard,
                    "targets": args.targets,
                    "sources": hashes,
                    "tests": args.tests,
                    "baseline_validation": "shared" if args.baselines is not None else "local",
                    "kill_confirmation": (
                        "baseline-orders" if args.test_selection == "full" else "failure-prefix"
                    ),
                },
                indent=2,
            )
            + "\n"
        )
    return result


if __name__ == "__main__":
    raise SystemExit(main())
