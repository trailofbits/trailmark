"""KAT snapshot tests for the trailmark CLI.

Invokes each subcommand against a known fixture directory and asserts the
exact stdout. Snapshots can be regenerated with TRAILMARK_UPDATE_SNAPSHOTS=1.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

from trailmark.cli import main

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "kat" / "python"
FIXTURE_FILE = FIXTURE_DIR / "taxonomy.py"
SNAPSHOT_DIR = Path(__file__).parent / "fixtures" / "kat" / "cli"
UPDATE_ENV = "TRAILMARK_UPDATE_SNAPSHOTS"

_VERSION_PATTERN = re.compile(r"\b\d+\.\d+\.\d+(?:[.\-+][\w.\-+]+)?\b")


def _normalize(text: str) -> str:
    """Replace absolute paths and the trailmark version with stable tokens.

    Snapshots must remain stable across machines, pytest invocations, and
    point releases — the test would otherwise need to be regenerated for
    every version bump.
    """
    here = str(FIXTURE_DIR.resolve())
    text = text.replace(here, "<FIXTURE_DIR>")
    text = text.replace(str(FIXTURE_FILE.resolve()), "<FIXTURE_DIR>/taxonomy.py")
    text = _VERSION_PATTERN.sub("<VERSION>", text)
    return text


def _run_cli(argv: list[str]) -> str:
    """Invoke trailmark with ``argv``, capture stdout, return normalized text."""
    buf = io.StringIO()
    with (
        redirect_stdout(buf),
        patch.object(sys, "argv", ["trailmark", *argv]),
        contextlib.suppress(SystemExit),
    ):
        main()
    return _normalize(buf.getvalue())


def _assert_or_update(name: str, actual: str) -> None:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = SNAPSHOT_DIR / f"{name}.expected.txt"

    if os.environ.get(UPDATE_ENV) == "1":
        snapshot.write_text(actual)
        return

    if not snapshot.exists():
        msg = f"Missing snapshot: {snapshot}. Set {UPDATE_ENV}=1 to create."
        raise AssertionError(msg)

    expected = snapshot.read_text()
    if actual != expected:
        msg = (
            f"CLI snapshot mismatch for {name}.\n"
            f"Run with {UPDATE_ENV}=1 to regenerate {snapshot.name}."
        )
        raise AssertionError(msg)


def test_version_subcommand_snapshot() -> None:
    out = _run_cli(["version"])
    _assert_or_update("version", out)


def test_analyze_summary_snapshot() -> None:
    out = _run_cli(["analyze", str(FIXTURE_DIR), "--language", "python", "--summary"])
    _assert_or_update("analyze_summary", out)


def test_analyze_complexity_snapshot() -> None:
    out = _run_cli(
        ["analyze", str(FIXTURE_DIR), "--language", "python", "--complexity", "2"],
    )
    _assert_or_update("analyze_complexity", out)


def test_analyze_complexity_none_snapshot() -> None:
    out = _run_cli(
        ["analyze", str(FIXTURE_DIR), "--language", "python", "--complexity", "9999"],
    )
    _assert_or_update("analyze_complexity_none", out)


def test_entrypoints_text_snapshot() -> None:
    out = _run_cli(["entrypoints", str(FIXTURE_DIR), "--language", "python"])
    _assert_or_update("entrypoints_text", out)


def test_entrypoints_json_snapshot() -> None:
    out = _run_cli(["entrypoints", str(FIXTURE_DIR), "--language", "python", "--json"])
    _assert_or_update("entrypoints_json", out)


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_top_level_version_snapshot(flag: str) -> None:
    out = _run_cli([flag])
    _assert_or_update("top_level_version", out)


def test_no_command_prints_help_snapshot() -> None:
    out = _run_cli([])
    _assert_or_update("no_command_help", out)
