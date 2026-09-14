"""Tests for the vendored tree-sitter-verus grammar loader."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from trailmark.tree_sitter_custom import verus as verus_mod


def test_build_command_includes_parser_and_scanner() -> None:
    with patch.object(subprocess, "run") as mock_run:
        verus_mod._build()

    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    source_names = {Path(value).name for value in cmd if value.endswith(".c")}
    assert cmd[0] == "cc"
    assert {"binding.c", "parser.c", "scanner.c"} == source_names
    assert "-shared" in cmd
    assert "-fPIC" in cmd
    assert "-O2" in cmd
    assert "-std=c11" in cmd
    assert mock_run.call_args[1]["check"] is True


def test_build_uses_platform_specific_linker_flags() -> None:
    with (
        patch.object(subprocess, "run") as mock_run,
        patch.object(sys, "platform", "darwin"),
    ):
        verus_mod._build()
    assert "-undefined" in mock_run.call_args[0][0]

    with (
        patch.object(subprocess, "run") as mock_run,
        patch.object(sys, "platform", "linux"),
    ):
        verus_mod._build()
    assert "-undefined" not in mock_run.call_args[0][0]


def test_language_returns_capsule() -> None:
    assert verus_mod.language() is not None
