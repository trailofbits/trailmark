"""Confirm actual failure prefixes without replaying every selected test subset."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pytest
from pytest_gremlins import plugin
from pytest_gremlins.instrumentation.gremlin import Gremlin
from pytest_gremlins.node_id_file import command_with_node_ids_file
from pytest_gremlins.parallel.exit_codes import COLLECTION_KILLING_TEST

from tests.mutation_bootstrap import baseline_environment, test_command

type _PendingKill = tuple[Gremlin, list[str], plugin.GremlinResult, plugin.GremlinSession]
type _ControlKey = tuple[tuple[str, ...], tuple[str, ...]]
type _Recorder = Callable[
    [Gremlin, Sequence[str], plugin.GremlinResult, plugin.GremlinSession], None
]


def _execution_path(directory: Path, gremlin_id: str) -> Path:
    identifier = hashlib.sha256(gremlin_id.encode()).hexdigest()
    return directory / f"execution-{identifier}.json"


def _read_execution(path: Path) -> dict[str, Any]:
    evidence = json.loads(path.read_text())
    if (
        not isinstance(evidence, dict)
        or not isinstance(evidence.get("tests"), list)
        or not all(isinstance(test, str) for test in evidence["tests"])
        or type(evidence.get("failed")) is not bool
        or type(evidence.get("stopped")) is not bool
        or type(evidence.get("exit_code")) is not int
    ):
        raise ValueError("Invalid test execution evidence")
    return evidence


class FailurePrefixControls:
    def __init__(self, workers: int, output: Path, *, checked_orders: bool = False) -> None:
        self.workers = workers
        self.output = output
        self.checked_orders = checked_orders
        self.pending: list[_PendingKill] = []
        self.witnesses: dict[str, dict[str, str]] = {}

    def defer(
        self,
        gremlin: Gremlin,
        selected: Sequence[str],
        result: plugin.GremlinResult,
        session: plugin.GremlinSession,
    ) -> bool:
        if result.status.value != "zapped" or result.killing_test == COLLECTION_KILLING_TEST:
            return False
        self.pending.append((gremlin, list(selected), result, session))
        return True

    def flush(self, session: plugin.GremlinSession, record: _Recorder) -> None:
        """Validate deferred kills after the mutant pool closes, within its worker budget."""
        started_at = time.perf_counter()
        directory = session.instrumented_dir
        if directory is None:
            raise pytest.UsageError("Missing instrumented sources for failure confirmation")
        groups: dict[_ControlKey, list[_PendingKill]] = defaultdict(list)
        baseline_reused = 0
        executions: dict[str, dict[str, Any]] = {}
        with (self.output / "executions.jsonl").open("w") as output:
            for gremlin in session.gremlins:
                try:
                    evidence = _read_execution(_execution_path(directory, gremlin.gremlin_id))
                    executions[gremlin.gremlin_id] = evidence
                    failures = evidence.get("failures", [])
                    if failures:
                        self.witnesses[gremlin.gremlin_id] = failures[0]
                except (OSError, ValueError):
                    evidence = None
                output.write(
                    json.dumps(
                        {
                            "gremlin_id": gremlin.gremlin_id,
                            "executed_tests": len(evidence["tests"]) if evidence else None,
                            "last_test": (
                                evidence["tests"][-1] if evidence and evidence["tests"] else None
                            ),
                            "failed": evidence["failed"] if evidence else None,
                            "exit_code": evidence["exit_code"] if evidence else None,
                            "failure": self.witnesses.get(gremlin.gremlin_id),
                        }
                    )
                    + "\n"
                )
        for row in self.pending:
            gremlin, selected, _, _ = row
            if gremlin.gremlin_id not in executions:
                raise pytest.UsageError("Missing actual failing-test execution evidence")
            evidence = executions[gremlin.gremlin_id]
            prefix = evidence["tests"]
            if (
                not prefix
                or evidence["exit_code"] != 1
                or not evidence["failed"]
                or prefix != selected[: len(prefix)]
            ):
                raise pytest.UsageError("Invalid actual failing-test execution evidence")
            if self.checked_orders or selected == list(session.test_node_ids):
                record(*row)
                baseline_reused += 1
            else:
                groups[(tuple(selected), tuple(prefix))].append(row)

        # Each prefix is an initial segment of the same collected selection. A
        # successful longer prefix also proves its shorter failure prefixes.
        original_prefixes = len(groups)
        longest: dict[tuple[str, ...], tuple[_ControlKey, list[_PendingKill]]] = {}
        for key, rows in groups.items():
            selected, prefix = key
            if selected not in longest:
                longest[selected] = (key, list(rows))
            else:
                previous, previous_rows = longest[selected]
                longest[selected] = (
                    key if len(prefix) > len(previous[1]) else previous,
                    previous_rows + rows,
                )
        groups = dict(longest.values())
        command = test_command(directory, session)

        def confirm(key: _ControlKey) -> _ControlKey:
            selected, prefix = key
            identifier = hashlib.sha256(json.dumps(key).encode()).hexdigest()
            report = directory / f"prefix-{identifier}.json"
            env = baseline_environment(directory)
            env["TRAILMARK_PREFIX_LIMIT"] = str(len(prefix))
            env["TRAILMARK_PREFIX_REPORT"] = str(report)
            control = subprocess.run(  # noqa: S603
                command_with_node_ids_file(command, selected, directory),
                env=env,
                capture_output=True,
                text=True,
                timeout=240,
                check=False,
            )
            try:
                evidence = _read_execution(report)
            except (OSError, ValueError) as exc:
                raise pytest.UsageError("Missing neutral failure-prefix evidence") from exc
            if (
                control.returncode
                or evidence["failed"]
                or evidence["tests"] != list(prefix)
                or not evidence["stopped"]
                or evidence["exit_code"] != 0
            ):
                raise pytest.UsageError(
                    "Unmutated failure prefix failed: "
                    + control.stdout[-2000:]
                    + control.stderr[-2000:]
                )
            return key

        print(
            f"Confirming {len(groups)} actual failure prefixes for {len(self.pending)} kills",
            flush=True,
        )
        pool = ThreadPoolExecutor(max_workers=self.workers)
        try:
            futures = {pool.submit(confirm, key): key for key in groups}
            for completed, future in enumerate(as_completed(futures), 1):
                key = future.result()
                for row in groups[key]:
                    record(*row)
                if completed % 25 == 0 or completed == len(futures):
                    print(f"Confirmed failure prefixes: {completed}/{len(futures)}", flush=True)
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        summary = {
            "recorded_mutants": len(executions),
            "actual_mutant_test_executions": sum(len(e["tests"]) for e in executions.values()),
            "kills": len(self.pending),
            "baseline_reused_kills": baseline_reused,
            "distinct_prefix_controls": len(groups),
            "prefixes_before_reuse": original_prefixes,
            "executed_neutral_tests": sum(len(key[1]) for key in groups),
            "full_subset_neutral_tests": sum(len(key[0]) for key in groups),
            "wall_seconds": time.perf_counter() - started_at,
        }
        (self.output / "prefix-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        self.pending.clear()
