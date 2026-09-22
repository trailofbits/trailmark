"""Ensure no snapshot harness can silently bless mutations in automation."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.mutation_infrastructure


@pytest.mark.parametrize(
    ("ci", "mutant", "update", "fails"),
    [
        ("true", None, "1", True),
        (None, "", "1", True),  # An empty activation variable must not enable snapshot writes.
        (None, "g001", "1", True),
        ("true", None, "0", False),
        ("false", None, "1", False),
    ],
)
def test_snapshot_update_guard(
    tmp_path: Path, ci: str | None, mutant: str | None, update: str, fails: bool
) -> None:
    (tmp_path / "conftest.py").write_text(Path(__file__).with_name("conftest.py").read_text())
    (tmp_path / "test_pass.py").write_text("def test_pass():\n    assert True\n")
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"CI", "ACTIVE_GREMLIN", "PYTEST_GREMLINS_SOURCES_FILE"}
    }
    env["TRAILMARK_UPDATE_SNAPSHOTS"] = update
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    if ci is not None:
        env["CI"] = ci
    if mutant is not None:
        env["ACTIVE_GREMLIN"] = mutant
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", "-q", str(tmp_path)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == (4 if fails else 0), result.stdout + result.stderr
    if fails:
        assert "Snapshot updates are disabled" in result.stderr


def test_gremlins_baseline_rejects_snapshot_updates(tmp_path: Path) -> None:
    (tmp_path / "conftest.py").write_text(Path(__file__).with_name("conftest.py").read_text())
    (tmp_path / "test_pass.py").write_text("def test_pass():\n    assert True\n")
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"CI", "ACTIVE_GREMLIN", "PYTEST_GREMLINS_SOURCES_FILE"}
    }
    env["TRAILMARK_UPDATE_SNAPSHOTS"] = "1"
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "pytest_gremlins.plugin",
            "--gremlins",
            "--gremlin-targets=.",
            "-q",
            str(tmp_path),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 4, result.stdout + result.stderr
    assert "Snapshot updates are disabled" in result.stderr
