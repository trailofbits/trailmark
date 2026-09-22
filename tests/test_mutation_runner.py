"""Controls against false kills and false survivors in the mutation harness."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.mutation_infrastructure


def test_runner_executes_fixtures_parameters_and_package_mutations(tmp_path: Path) -> None:
    package = tmp_path / "toy"
    package.mkdir()
    (package / "value.txt").write_text("known resource")
    (package / "helper.py").write_text("VALUE = 7\n")
    (package / "__init__.py").write_text(
        "from pathlib import Path\nfrom .helper import VALUE\n"
        'RESOURCE = Path(__file__).with_name("value.txt").read_text()\n'
        "def checked(value):\n    return value > 0\n"
        "def ignored(value):\n    return value > 0\n"
        "def delegated(value):\n    return value > 0\n"
    )
    (tmp_path / "test_toy.py").write_text("""\
import pytest
from toy import checked, ignored, RESOURCE, VALUE

@pytest.mark.parametrize(("value", "answer"), [(0, False), (1, True)], ids=["ZERO", "ONE"])
def test_strong(tmp_path, value, answer):
    assert tmp_path.is_dir()
    assert RESOURCE == "known resource" and VALUE == 7
    assert checked(value) is answer

@pytest.mark.parametrize("value", [0, 1])
def test_weak(tmp_path, value):
    assert tmp_path.is_dir()
    ignored(value)
""")
    (tmp_path / "test_integration.py").write_text(
        "from toy import delegated\n"
        "def test_integration(tmp_path):\n"
        "    assert tmp_path.is_dir()\n"
        "    assert delegated(0) is False\n"
        "    assert delegated(1) is True\n"
    )
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"ACTIVE_GREMLIN", "PYTEST_GREMLINS_SOURCES_FILE", "PYTEST_ADDOPTS"}
    }
    runner = Path(__file__).with_name("run_mutations.py")
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(runner),
            "--targets=toy/__init__.py",
            "--workers=2",
            "test_toy.py",
            "test_integration.py",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / "coverage/gremlins/gremlins.json").read_text())
    results = report["results"]
    strong = [r for r in results if r["line_number"] == 5]
    weak = [r for r in results if r["line_number"] == 7]
    delegated = [r for r in results if r["line_number"] == 9]
    assert len(strong) >= 4 and len(weak) == len(strong)
    assert len(delegated) == len(strong)
    assert {r["status"] for r in strong} == {"zapped"}
    assert {r["status"] for r in delegated} == {"zapped"}
    assert {r["status"] for r in weak} == {"survived"}
    assert all(len(r["selected_tests"]) == 5 for r in results)
    assert all(
        "test_toy.py::test_strong[ZERO]" in r["selected_tests"]
        and "test_toy.py::test_strong[ONE]" in r["selected_tests"]
        and "test_integration.py::test_integration" in r["selected_tests"]
        for r in results
    )


def test_runner_rejects_order_dependent_baseline(tmp_path: Path) -> None:
    (tmp_path / "target.py").write_text("def checked(value):\n    return value > 0\n")
    (tmp_path / "state.py").write_text("ready = False\n")
    (tmp_path / "test_a_setup.py").write_text(
        "import state\ndef test_initialize():\n    state.ready = True\n"
    )
    (tmp_path / "test_target.py").write_text(
        "import state\nfrom target import checked\n"
        "def test_checked():\n    assert state.ready\n    assert checked(1)\n"
    )
    runner = Path(__file__).with_name("run_mutations.py")
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(runner), "--targets=target.py", "."],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "Instrumented baseline failed" in result.stdout + result.stderr
    assert not (tmp_path / "coverage/gremlins/run.json").exists()
