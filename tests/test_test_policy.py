"""Keep CI correctness checks independent of machine speed."""

import ast
from pathlib import Path

import pytest
from hypothesis import HealthCheck, settings


def _timing_controls(source: str) -> list[str]:
    controls = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Import)
            and any(alias.name in {"time", "timeit"} for alias in node.names)
            or isinstance(node, ast.ImportFrom)
            and node.module in {"time", "timeit"}
        ):
            controls.append("clock import")
        elif (
            isinstance(node, ast.Attribute)
            and node.attr == "timeout"
            and (
                isinstance(node.value, ast.Attribute)
                and node.value.attr == "mark"
                or isinstance(node.value, ast.Name)
                and node.value.id == "mark"
            )
        ):
            controls.append("pytest timeout")
        elif isinstance(node, ast.Call):
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            for keyword in node.keywords:
                if keyword.arg == "timeout" and name in {
                    "run",
                    "check_call",
                    "check_output",
                    "communicate",
                    "wait",
                    "join",
                    "get",
                }:
                    controls.append("test timeout")
                elif (
                    keyword.arg == "deadline"
                    and name in {"settings", "register_profile"}
                    and not (
                        isinstance(keyword.value, ast.Constant) and keyword.value.value is None
                    )
                ):
                    controls.append("test deadline")
    return controls


@pytest.mark.parametrize(
    "source",
    [
        "import time as clock",
        "from time import perf_counter as now",
        "@pytest.mark.timeout(5)\ndef test_case(): pass",
        "from pytest import mark\n@mark.timeout(5)\ndef test_case(): pass",
        "subprocess.run(['tool'], timeout=10)",
        "@settings(deadline=200)\ndef test_case(): pass",
    ],
)
def test_policy_rejects_clock_based_checks(source: str) -> None:
    assert _timing_controls(source)


@pytest.mark.parametrize(
    "source",
    [
        "BinaryLimits(tool_timeout=0.05)",
        "TimeoutExpired('tool', timeout=0.05)",
        "run_tool(['tool'], limits, deadline=5.0)",
        "@settings(deadline=None)\ndef test_case(): pass",
    ],
)
def test_policy_allows_product_limits_and_disabled_deadlines(source: str) -> None:
    assert _timing_controls(source) == []


def test_suite_has_no_test_timeouts_or_wall_clock_assertions() -> None:
    # These are opt-in measurement tools or the mutation executor, not tests.
    measurement_tools = {
        "benchmark_analysis.py",
        "benchmark_mutations.py",
        "smoke_binary_projects.py",
        "run_mutations.py",
        "mutation_confirmation.py",
    }
    violations = {
        path.name: controls
        for path in Path(__file__).parent.glob("*.py")
        if path.name not in measurement_tools and (controls := _timing_controls(path.read_text()))
    }
    assert violations == {}


def test_hypothesis_has_no_deadline_or_generation_speed_limit() -> None:
    defaults = settings.default
    assert defaults is not None
    assert defaults.deadline is None
    assert HealthCheck.too_slow in defaults.suppress_health_check
    mutation = settings.get_profile("trailmark-mutation")
    assert mutation.derandomize is True
    assert mutation.database is None
