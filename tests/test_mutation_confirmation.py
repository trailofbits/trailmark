"""A mutation kill must survive a matching neutral failure-prefix check."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.mutation_bootstrap import record_test_execution

pytestmark = pytest.mark.mutation_infrastructure


def _campaign(
    project: Path,
    files: dict[str, str],
    source: str = "def checked():\n    return True\n",
    *,
    selection: str = "coverage",
) -> subprocess.CompletedProcess[str]:
    (project / "target.py").write_text(source)
    for name, content in files.items():
        (project / name).write_text(content)
    return subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(Path(__file__).with_name("run_mutations.py")),
            "--targets=target.py",
            "--workers=2",
            f"--test-selection={selection}",
            ".",
        ],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )


def test_longest_failure_prefix_covers_shorter_failures(tmp_path: Path) -> None:
    result = _campaign(
        tmp_path,
        {
            "test_target.py": (
                "from target import checked\n"
                "def test_negative():\n    assert checked(-1) is False\n"
                "def test_zero():\n    assert checked(0) is True\n"
                "def test_positive():\n    assert checked(1) is True\n"
                "def test_unrelated():\n    assert True\n"
            )
        },
        "def checked(value):\n    return value >= 0\n",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    reports = tmp_path / "coverage/gremlins"
    report = json.loads((reports / "gremlins.json").read_text())
    assert len(report["results"]) == 5
    assert {row["status"] for row in report["results"]} == {"zapped"}
    controls = json.loads((reports / "prefix-summary.json").read_text())
    assert controls["recorded_mutants"] == 5
    assert controls["actual_mutant_test_executions"] == 7
    assert controls["prefixes_before_reuse"] == 2
    assert controls["distinct_prefix_controls"] == 1
    assert controls["executed_neutral_tests"] == 2
    assert controls["full_subset_neutral_tests"] == 3


def test_failure_prefix_keeps_the_entire_selected_collection(tmp_path: Path) -> None:
    result = _campaign(
        tmp_path,
        {
            "state.py": "ready = False\n",
            "test_a_target.py": (
                "import state\nfrom target import checked\n"
                "def test_first():\n    assert not state.ready\n    assert checked() is True\n"
            ),
            "test_b_target.py": (
                "import state\nfrom target import checked\nstate.ready = True\n"
                "def test_weak():\n    checked()\n"
            ),
            "test_c_reset.py": (
                "import state\nstate.ready = False\ndef test_unrelated():\n    assert True\n"
            ),
        },
    )
    # The full baseline passes, but the selected collection changes state. A
    # control that imported only the failing test would falsely credit the kill.
    assert result.returncode != 0
    assert "Unmutated failure prefix failed" in result.stdout + result.stderr
    assert not (tmp_path / "coverage/gremlins/run.json").exists()


def test_failure_prefix_executes_fixture_teardown(tmp_path: Path) -> None:
    result = _campaign(
        tmp_path,
        {
            "test_target.py": (
                "from pathlib import Path\nimport pytest\nfrom target import checked\n"
                "@pytest.fixture(autouse=True)\ndef check_after():\n    yield\n"
                "    with Path('coverage/events').open('a') as output:\n"
                "        output.write('teardown\\n')\n"
                "    assert checked() is True\n"
                "def test_first():\n    assert True\n"
                "def test_second():\n    assert True\n"
            ),
            "test_z_unrelated.py": "def test_unrelated():\n    assert True\n",
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    reports = tmp_path / "coverage/gremlins"
    report = json.loads((reports / "gremlins.json").read_text())
    assert len(report["results"]) == 2
    assert {row["status"] for row in report["results"]} == {"zapped"}
    controls = json.loads((reports / "prefix-summary.json").read_text())
    assert controls["distinct_prefix_controls"] == controls["executed_neutral_tests"] == 1
    assert (tmp_path / "coverage/events").read_text().splitlines() == ["teardown"] * 5


@pytest.mark.parametrize("selection", ["coverage", "full"])
def test_harness_exit_without_a_failed_report_cannot_count_as_a_kill(
    tmp_path: Path, selection: str
) -> None:
    result = _campaign(
        tmp_path,
        {
            "test_target.py": (
                "from target import checked\ndef test_first():\n    assert checked() is True\n"
            ),
            "conftest.py": (
                "import os\ndef pytest_sessionfinish():\n"
                "    if os.environ.get('ACTIVE_GREMLIN'):\n        raise SystemExit(1)\n"
            ),
        },
        selection=selection,
    )
    assert result.returncode != 0
    assert "Missing actual failing-test execution evidence" in result.stdout + result.stderr
    assert not (tmp_path / "coverage/gremlins/run.json").exists()


def test_execution_recorder_rejects_unknown_bootstrap_layout(tmp_path: Path) -> None:
    path = tmp_path / "gremlin_bootstrap.py"
    path.write_text("print('unrecognized bootstrap')\n")
    with pytest.raises(pytest.UsageError, match="Revalidate"):
        record_test_execution(tmp_path)
    assert path.read_text() == "print('unrecognized bootstrap')\n"
