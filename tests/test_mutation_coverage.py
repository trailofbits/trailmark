"""Exercise the coverage selector's reductions and conservative boundaries."""

from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from tests.mutation_controls import digest
from tests.mutation_coverage import (
    collect_plan,
    native_tool_fingerprints,
    select_tests,
    validate_plan,
)
from tests.mutation_gate import check
from tests.run_mutations import VERSION

pytestmark = pytest.mark.mutation_infrastructure


@pytest.mark.parametrize(
    ("source", "names", "selected"),
    [
        (
            "from target import checked\ndef test_strong():\n    assert checked() is True\n",
            ["test_strong"],
            ["test_strong"],
        ),
        (
            "import pytest\nfrom target import checked\n"
            "@pytest.fixture(scope='module')\n"
            "def flag():\n    return checked()\n"
            "def test_weak(flag):\n    assert isinstance(flag, bool)\n"
            "def test_strong(flag):\n    assert flag is True\n",
            ["test_weak", "test_strong"],
            ["test_weak", "test_strong"],
        ),
        (
            "import pytest\nfrom target import checked\n"
            "@pytest.fixture(scope='module')\n"
            "def flag():\n    return checked()\n"
            "def test_weak(flag):\n    assert isinstance(flag, bool)\n"
            "def test_strong(request):\n    assert request.getfixturevalue('flag') is True\n",
            ["test_weak", "test_strong"],
            ["test_weak", "test_strong"],
        ),
        (
            "import pytest\nfrom target import checked\n"
            "@pytest.fixture(scope='module')\n"
            "def flag():\n    yield []\n    assert checked() is True\n"
            "def test_weak(flag):\n    assert isinstance(flag, list)\n"
            "def test_strong(flag):\n    assert flag == []\n",
            ["test_weak", "test_strong"],
            ["test_weak", "test_strong"],
        ),
        (
            "import subprocess, sys\nfrom target import checked\n"
            "def test_weak():\n    assert isinstance(checked(), bool)\n"
            "def test_strong():\n"
            "    subprocess.run([sys.executable, '-c', "
            "'from target import checked; assert checked() is True'], check=True)\n",
            ["test_weak", "test_strong"],
            ["test_weak", "test_strong"],
        ),
        (
            "import sys\nfrom target import checked\n"
            "def test_weak():\n    assert isinstance(checked(), bool)\n"
            "def test_strong():\n    previous = sys.gettrace()\n"
            "    try:\n        sys.settrace(None)\n        assert checked() is True\n"
            "    finally:\n        sys.settrace(previous)\n",
            ["test_weak", "test_strong"],
            ["test_weak", "test_strong"],
        ),
        (
            "import pytest\nfrom target import checked\n"
            "@pytest.mark.parametrize('value', [0, 1], ids=['ZERO', 'one [B]'])\n"
            "def test_strong(value):\n    assert checked() is True\n",
            ["test_strong[ZERO]", "test_strong[one [B]]"],
            ["test_strong[ZERO]", "test_strong[one [B]]"],
        ),
    ],
    ids=[
        "direct",
        "shared-fixture",
        "dynamic-fixture",
        "fixture-teardown",
        "child",
        "tracer",
        "params",
    ],
)
def test_selection_preserves_consumers_and_opaque_tests_without_unrelated_tests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    names: list[str],
    selected: list[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "target.py").write_text("def checked():\n    return True\n")
    (tmp_path / "test_target.py").write_text(source + "def test_unrelated():\n    assert 1 == 1\n")
    names = [f"test_target.py::{name}" for name in names]
    tests = [*names, "test_target.py::test_unrelated"]
    envelope = collect_plan(
        tmp_path, ["-q", "-p", "no:gremlins"], tests, [str(tmp_path / "target.py")]
    )
    plan = envelope["plan"]
    assert not plan["collection_uncertain"]
    assert select_tests(plan, "target.py", 2, tests) == [
        f"test_target.py::{name}" for name in selected
    ]
    # Import-time and unobserved lines retain the complete suite.
    assert select_tests(plan, "target.py", 1, tests) == tests
    assert select_tests(plan, "target.py", 100, tests) == tests


@pytest.mark.parametrize(
    "body", ["assert False", "import os; os._exit(0)", "import sys; sys.settrace(None)"]
)
def test_failed_or_incomplete_prescan_cannot_reuse_a_stale_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "target.py").write_text("def checked():\n    return True\n")
    (tmp_path / "test_target.py").write_text(f"def test_case():\n    {body}\n")
    (tmp_path / "coverage-plan.json").write_text("{}")
    with pytest.raises(pytest.UsageError, match="Coverage baseline"):
        collect_plan(
            tmp_path,
            ["-q", "-p", "no:gremlins"],
            ["test_target.py::test_case"],
            [str(tmp_path / "target.py")],
        )
    assert not (tmp_path / "coverage-plan.json").exists()


@pytest.mark.parametrize(
    "change",
    ["context", "digest", "tests", "unknown-test", "duplicate-test", "source", "line", "import"],
)
def test_shared_plan_rejects_stale_or_invalid_evidence(change: str) -> None:
    tests = ["test_target.py::test_case"]
    context = {"inputs": "known"}
    plan = {
        "tests": tests,
        "locations": {"target.py": {"2": [0]}},
        "imported": {"target.py": [1]},
        "uncertain": [],
        "collection_uncertain": False,
        "native_tools": native_tool_fingerprints(),
    }
    envelope: dict[str, Any] = {"context": context, "plan": plan, "digest": digest(plan)}
    assert validate_plan(envelope, context, tests, {"target.py"}) == plan
    broken = deepcopy(envelope)
    if change == "context":
        broken["context"] = {"inputs": "stale"}
    elif change == "digest":
        broken["digest"] = "stale"
    elif change == "tests":
        broken["plan"]["tests"] = []
    elif change in {"unknown-test", "duplicate-test"}:
        broken["plan"]["uncertain"] = [1] if change == "unknown-test" else [0, 0]
    elif change == "source":
        broken["plan"]["locations"] = {"other.py": {"2": [0]}}
    elif change == "line":
        broken["plan"]["locations"]["target.py"]["0"] = [0]
    else:
        broken["plan"]["imported"]["target.py"] = [True]
    if change != "digest":
        broken["digest"] = digest(broken["plan"])
    with pytest.raises(ValueError):
        validate_plan(broken, context, tests, {"target.py"})


def test_python_and_script_wrappers_never_become_trusted_native_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wrapper = tmp_path / "llvm-objdump"
    wrapper.write_text("#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("TRAILMARK_LLVM_OBJDUMP", str(wrapper))
    monkeypatch.setenv("TRAILMARK_GNU_OBJDUMP", sys.executable)
    trusted = native_tool_fingerprints()
    assert str(wrapper) not in trusted
    assert str(Path(sys.executable).resolve()) not in trusted


def test_collection_tracing_gaps_require_the_full_suite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "target.py").write_text("def checked():\n    return True\n")
    (tmp_path / "test_a_collection.py").write_text(
        "import sys\nfrom target import checked\nprevious = sys.gettrace()\n"
        "try:\n    sys.settrace(None)\n    assert checked() is True\n"
        "finally:\n    sys.settrace(previous)\n"
        "def test_unrelated():\n    assert 1 == 1\n"
    )
    (tmp_path / "test_target.py").write_text(
        "from target import checked\ndef test_weak():\n    assert isinstance(checked(), bool)\n"
    )
    tests = ["test_a_collection.py::test_unrelated", "test_target.py::test_weak"]
    plan = collect_plan(
        tmp_path, ["-q", "-p", "no:gremlins"], tests, [str(tmp_path / "target.py")]
    )["plan"]
    assert plan["collection_uncertain"]
    assert select_tests(plan, "target.py", 2, tests) == tests


def test_empty_module_coverage_does_not_create_a_line_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "empty.py").touch()
    (tmp_path / "target.py").write_text("def checked():\n    return True\n")
    (tmp_path / "test_target.py").write_text(
        "import empty\nfrom target import checked\ndef test_case():\n    assert checked() is True\n"
    )
    tests = ["test_target.py::test_case"]
    envelope = collect_plan(
        tmp_path,
        ["-q", "-p", "no:gremlins"],
        tests,
        [str(tmp_path / "target.py"), str(tmp_path / "empty.py")],
    )
    envelope["context"] = {"inputs": "known"}
    plan = validate_plan(envelope, envelope["context"], tests, {"target.py", "empty.py"})
    assert all(line > 0 for lines in plan["imported"].values() for line in lines)
    assert all(int(line) > 0 for lines in plan["locations"].values() for line in lines)


def test_plan_rejects_changes_to_trusted_native_tool_contents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = tmp_path / "native-tool"
    tool.write_bytes(b"\x7fELFbefore")
    monkeypatch.setenv("TRAILMARK_LLVM_OBJDUMP", str(tool))
    tests = ["test_target.py::test_case"]
    context = {"inputs": "known"}
    plan = {
        "tests": tests,
        "locations": {},
        "imported": {},
        "uncertain": [],
        "collection_uncertain": False,
        "native_tools": native_tool_fingerprints(),
    }
    assert str(tool) in plan["native_tools"]
    envelope = {"plan": plan, "context": context, "digest": digest(plan)}
    assert validate_plan(envelope, context, tests, {"target.py"}) == plan
    tool.write_bytes(b"\x7fELFafter")
    with pytest.raises(ValueError, match="Native tools changed"):
        validate_plan(envelope, context, tests, {"target.py"})


@pytest.mark.parametrize("case", ["fixture", "child", "tracer"])
def test_coverage_and_full_campaigns_agree_on_opaque_killers(tmp_path: Path, case: str) -> None:
    (tmp_path / "target.py").write_text("def checked():\n    return True\n")
    source = {
        "fixture": (
            "import pytest\nfrom target import checked\n"
            "@pytest.fixture(scope='module')\n"
            "def flag():\n    return checked()\n"
            "def test_weak(flag):\n    assert isinstance(flag, bool)\n"
            "def test_strong(flag):\n    assert flag is True\n"
        ),
        "child": (
            "import subprocess, sys\nfrom target import checked\n"
            "def test_weak():\n    assert isinstance(checked(), bool)\n"
            "def test_strong():\n    subprocess.run([sys.executable, '-c', "
            "'from target import checked; assert checked() is True'], check=True)\n"
        ),
        "tracer": (
            "import sys\nfrom target import checked\n"
            "def test_weak():\n    assert isinstance(checked(), bool)\n"
            "def test_strong():\n    previous = sys.gettrace()\n"
            "    try:\n        sys.settrace(None)\n        assert checked() is True\n"
            "    finally:\n        sys.settrace(previous)\n"
        ),
    }[case]
    (tmp_path / "test_target.py").write_text(source + "def test_unrelated():\n    assert 1 == 1\n")
    runner = Path(__file__).with_name("run_mutations.py")
    baseline = {"pytest_gremlins": VERSION, "scope": {"target.py": "*"}, "exceptions": []}
    outcomes = []
    for selection in ("coverage", "full"):
        result = subprocess.run(  # noqa: S603
            [
                sys.executable,
                str(runner),
                "--targets=target.py",
                "--workers=2",
                f"--test-selection={selection}",
                "test_target.py",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        directory = tmp_path / "coverage/gremlins"
        report = json.loads((directory / "gremlins.json").read_text())
        run = json.loads((directory / "run.json").read_text())
        assert check(tmp_path, report, run, baseline) == []
        outcomes.append({row["gremlin_id"]: row["status"] for row in report["results"]})
        assert set(outcomes[-1].values()) == {"zapped"}
        if selection == "coverage":
            assert all(
                "test_target.py::test_unrelated" not in row["selected_tests"]
                for row in report["results"]
            )
            report["results"][0]["selected_tests"] = report["results"][0]["selected_tests"][:1]
            with pytest.raises(ValueError, match="selection differs"):
                check(tmp_path, report, run, baseline)
    assert outcomes[0] == outcomes[1]


def test_selected_subset_must_pass_before_any_mutant_runs(tmp_path: Path) -> None:
    (tmp_path / "target.py").write_text("def checked():\n    return True\n")
    (tmp_path / "state.py").write_text("ready = False\n")
    (tmp_path / "test_a_setup.py").write_text(
        "import state\ndef test_initialize():\n    state.ready = True\n"
    )
    (tmp_path / "test_target.py").write_text(
        "import state\nfrom target import checked\n"
        "def test_checked():\n    assert state.ready\n    assert checked() is True\n"
    )
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(Path(__file__).with_name("run_mutations.py")),
            "--targets=target.py",
            "--workers=2",
            ".",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "Selected instrumented baseline failed" in result.stdout + result.stderr
    assert not (tmp_path / "coverage/gremlins/run.json").exists()
    assert not (tmp_path / "coverage/gremlins/progress.jsonl").exists()
