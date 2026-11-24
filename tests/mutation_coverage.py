"""Collect conservative per-test coverage for mutation test selection."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from collections.abc import Generator, Sequence
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from _pytest.fixtures import FixtureDef

import coverage

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.mutation_controls import digest


def _fixture_key(fixture: Any) -> str:
    return json.dumps([fixture.baseid, fixture.argname, fixture.scope])


def native_tool_fingerprints() -> dict[str, str]:
    """Known native tools cannot execute the Python mutation in their own process."""
    candidates = {
        value
        for key, value in os.environ.items()
        if key.startswith(("TRAILMARK_LLVM_", "TRAILMARK_GNU_"))
    }
    candidates.update(
        path
        for command in ("cc", "clang", "clang++", "gcc", "g++")
        if (path := shutil.which(command))
    )
    for candidate in tuple(candidates):
        if Path(candidate).name == "llvm-objdump":
            candidates.add(str(Path(candidate).with_name("llvm-strip")))
    native = {}
    for candidate in candidates:
        path = Path(candidate).resolve()
        if (
            path.name.lower().startswith(("python", "pypy"))
            or path == Path(sys.executable).resolve()
        ):
            continue
        if str(path) in native:
            continue
        try:
            with path.open("rb") as executable:
                magic = executable.read(4)
                # Script wrappers remain opaque even under a configured tool name.
                if magic in {
                    b"\x7fELF",
                    b"\xcf\xfa\xed\xfe",
                    b"\xfe\xed\xfa\xcf",
                    b"\xca\xfe\xba\xbe",
                }:
                    executable.seek(0)
                    native[str(path)] = hashlib.file_digest(executable, "sha256").hexdigest()
        except OSError:
            continue
    return native


class SelectionCoverage:
    """Attribute fixture setup to every consumer and preserve opaque executions."""

    def __init__(self, measured: coverage.Coverage, tests: list[str]) -> None:
        self.measured = measured
        self.tests = tests
        self.context: tuple[str, str] | None = None
        self.consumers: dict[str, set[str]] = defaultdict(set)
        self.uncertain: set[tuple[str, str]] = set()
        self.collection_uncertain = False
        self.completed: set[str] = set()
        self._finish_fixture = FixtureDef.finish
        self.native_tools = native_tool_fingerprints()
        self.tracer: object = None
        self.recording = True
        self.switching_context = False

    def audit(self, event: str, arguments: tuple[object, ...]) -> None:
        if not self.recording:
            return
        if event in {"sys.settrace", "sys._settraceallthreads"} and self.switching_context:
            return
        if event in {"subprocess.Popen", "os.posix_spawn", "os.exec"} and arguments:
            executable = arguments[0]
            if isinstance(executable, (str, bytes)):
                name = os.fsdecode(executable)
                resolved = shutil.which(name) if not Path(name).is_absolute() else name
                if resolved is not None and str(Path(resolved).resolve()) in self.native_tools:
                    return
        if event in {
            "subprocess.Popen",
            "os.fork",
            "os.forkpty",
            "os.posix_spawn",
            "os.exec",
            "sys.settrace",
            "sys._settraceallthreads",
        }:
            if self.context is not None:
                self.uncertain.add(self.context)
            else:
                self.collection_uncertain = True

    def _switch(self, context: tuple[str, str] | None) -> None:
        self.context = context
        self.switching_context = True
        try:
            self.measured.switch_context(json.dumps(context) if context is not None else "")
        finally:
            self.switching_context = False

    def _phase(self, item: pytest.Item) -> Generator[None, None, None]:
        if sys.gettrace() is not self.tracer:
            raise pytest.UsageError("Coverage tracer was replaced between tests")
        previous = self.context
        self._switch(("test", item.nodeid))
        try:
            yield
        finally:
            self._switch(previous)
            if sys.gettrace() is not self.tracer:
                raise pytest.UsageError("Test did not restore the coverage tracer")

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_runtest_setup(self, item: pytest.Item) -> Generator[None, None, None]:
        yield from self._phase(item)

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_runtest_call(self, item: pytest.Item) -> Generator[None, None, None]:
        yield from self._phase(item)

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_runtest_teardown(self, item: pytest.Item) -> Generator[None, None, None]:
        # This includes cached fixtures and fixtures obtained with getfixturevalue.
        request = getattr(item, "_request", None)
        if request is not None:
            for fixture in request._fixture_defs.values():
                if hasattr(fixture, "baseid"):
                    self.consumers[_fixture_key(fixture)].add(item.nodeid)
        yield from self._phase(item)

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_fixture_setup(self, fixturedef: Any) -> Generator[None, None, None]:
        previous = self.context
        self._switch(("fixture", _fixture_key(fixturedef)))
        try:
            yield
        finally:
            self._switch(previous)

    def finish_fixture(self, fixturedef: Any, request: Any) -> None:
        previous = self.context
        self._switch(("fixture", _fixture_key(fixturedef)))
        try:
            self._finish_fixture(fixturedef, request)
        finally:
            self._switch(previous)

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        if [item.nodeid for item in session.items] != self.tests:
            raise pytest.UsageError("Coverage collection changed the requested test selection")

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.when == "call" or (report.when == "setup" and report.skipped):
            self.completed.add(report.nodeid)

    def _tests_for(self, context: tuple[str, str]) -> set[str]:
        kind, name = context
        if kind == "test":
            return {name}
        if kind == "fixture":
            # A fixture skipped during setup may never enter request._fixture_defs.
            return self.consumers.get(name, set(self.tests))
        raise ValueError(f"Unresolved coverage context: {context}")

    def selection(self, root: Path) -> dict[str, Any]:
        if self.completed != set(self.tests):
            raise ValueError("Coverage run did not finish every selected test")
        opaque = set()
        indices = {test: index for index, test in enumerate(self.tests)}
        for context in self.uncertain:
            opaque.update(self._tests_for(context))
        locations: dict[str, dict[str, list[int]]] = {}
        imported: dict[str, list[int]] = {}
        data = self.measured.get_data()
        for filename in sorted(data.measured_files()):
            path = Path(filename).resolve().relative_to(root).as_posix()
            lines = {}
            imports = []
            for line, contexts in data.contexts_by_lineno(filename).items():
                # Python 3.15 can report a synthetic line zero for an empty module.
                if line < 1:
                    continue
                tests = set()
                for raw in contexts:
                    if raw:
                        tests.update(self._tests_for(tuple(json.loads(raw))))
                    else:
                        imports.append(line)
                if tests:
                    lines[str(line)] = sorted(indices[test] for test in tests)
            locations[path] = lines
            imported[path] = sorted(imports)
        return {
            "tests": self.tests,
            "locations": locations,
            "imported": imported,
            "uncertain": sorted(indices[test] for test in opaque),
            "collection_uncertain": self.collection_uncertain,
            "native_tools": self.native_tools,
        }


def select_tests(
    plan: dict[str, Any], source: str, line: int, tests: list[str], *, root: Path | None = None
) -> list[str]:
    """Use observed coverage, retaining opaque executions and unknown locations."""
    root = (root or Path.cwd()).resolve()
    path = Path(source)
    path = (path if path.is_absolute() else root / path).resolve().relative_to(root).as_posix()
    covered = plan["locations"].get(path, {}).get(str(line))
    if not covered or plan["collection_uncertain"] or line in plan["imported"].get(path, []):
        return tests
    selected = set(covered) | set(plan["uncertain"])
    return [test for index, test in enumerate(tests) if index in selected]


def collect_plan(
    directory: Path, command: Sequence[str], tests: list[str], sources: list[str]
) -> dict[str, Any]:
    """Require a successful complete pre-scan; never reuse a partial coverage map."""
    specification = directory / "coverage-input.json"
    destination = directory / "coverage-plan.json"
    destination.unlink(missing_ok=True)
    specification.write_text(json.dumps({"tests": tests, "sources": sources}))
    env = os.environ.copy()
    env.pop("ACTIVE_GREMLIN", None)
    env.pop("PYTEST_GREMLINS_SOURCES_FILE", None)
    env.pop("COVERAGE_PROCESS_CONFIG", None)
    env.pop("COVERAGE_PROCESS_START", None)
    env["COVERAGE_CORE"] = "ctrace"
    env["TRAILMARK_MUTATION_COVERAGE"] = "1"
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(Path(__file__).resolve()),
            str(specification),
            str(destination),
            *command,
        ],
        env=env,
        check=False,
    )
    if result.returncode:
        raise pytest.UsageError("Coverage baseline failed; refusing a partial selection map")
    try:
        return json.loads(destination.read_text())
    except (OSError, ValueError) as exc:
        raise pytest.UsageError(
            "Coverage baseline did not produce complete selection evidence"
        ) from exc


def validate_plan(
    envelope: dict[str, Any],
    context: dict[str, Any],
    tests: list[str],
    sources: set[str],
) -> dict[str, Any]:
    """Reject stale, incomplete, or unresolvable shared selection evidence."""
    if not isinstance(envelope, dict) or not {"plan", "context", "digest"} <= envelope.keys():
        raise ValueError("Incomplete coverage selection evidence")
    plan = envelope["plan"]
    if (
        not isinstance(plan, dict)
        or not {
            "tests",
            "locations",
            "imported",
            "uncertain",
            "collection_uncertain",
            "native_tools",
        }
        <= plan.keys()
    ):
        raise ValueError("Incomplete coverage selection plan")
    if envelope["context"] != context or envelope["digest"] != digest(plan):
        raise ValueError("Stale or corrupt coverage selection plan")
    if plan["native_tools"] != native_tool_fingerprints():
        raise ValueError("Native tools changed after coverage selection")
    if plan["tests"] != tests or type(plan["collection_uncertain"]) is not bool:
        raise ValueError("Coverage plan does not match the selected tests")

    def test_list(value: Any) -> None:
        if (
            not isinstance(value, list)
            or not all(type(item) is int and 0 <= item < len(tests) for item in value)
            or len(value) != len(set(value))
        ):
            raise ValueError("Coverage plan contains unknown or duplicate test IDs")

    test_list(plan["uncertain"])
    if not isinstance(plan["locations"], dict) or not isinstance(plan["imported"], dict):
        raise ValueError("Invalid coverage location inventory")
    for source, lines in plan["locations"].items():
        if source not in sources or not isinstance(lines, dict):
            raise ValueError("Coverage plan contains an unknown source")
        for line, selected in lines.items():
            if not isinstance(line, str) or not line.isdecimal() or int(line) < 1:
                raise ValueError("Coverage plan contains an invalid line number")
            test_list(selected)
    for source, lines in plan["imported"].items():
        if source not in sources or not isinstance(lines, list):
            raise ValueError("Coverage plan contains an unknown imported source")
        if any(type(line) is not int or line < 1 for line in lines):
            raise ValueError("Coverage plan contains an invalid import line")
    return plan


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("specification", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("pytest_arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    specification = json.loads(args.specification.read_text())
    measured = coverage.Coverage(
        config_file=False,
        include=specification["sources"],
        data_file=str(args.specification.with_suffix(".coverage")),
    )
    collector = SelectionCoverage(measured, specification["tests"])
    measured.start()
    collector.tracer = sys.gettrace()
    sys.addaudithook(collector.audit)

    def finish_fixture(fixturedef: Any, request: Any) -> None:
        collector.finish_fixture(fixturedef, request)

    try:
        with patch.object(FixtureDef, "finish", finish_fixture):
            result = pytest.main(
                [*args.pytest_arguments, *specification["tests"]], plugins=[collector]
            )
    finally:
        collector.recording = False
        measured.stop()
    if result == 0:
        plan = collector.selection(Path.cwd())
        args.destination.write_text(json.dumps({"plan": plan, "digest": digest(plan)}) + "\n")
    return int(result)


if __name__ == "__main__":
    raise SystemExit(main())
