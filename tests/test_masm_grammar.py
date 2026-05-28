"""Tests for the vendored tree-sitter-masm grammar loader."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from trailmark.tree_sitter_custom import masm as masm_mod


class TestBuild:
    """Verify _build() constructs the correct compiler command."""

    def test_build_command_structure(self) -> None:
        """_build should invoke cc with correct flags and paths."""
        with patch.object(subprocess, "run") as mock_run:
            masm_mod._build()
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "cc"
        assert "-shared" in cmd
        assert "-fPIC" in cmd
        assert "-O2" in cmd
        assert "-std=c11" in cmd
        assert mock_run.call_args[1]["check"] is True

    def test_build_includes_python_headers(self) -> None:
        with patch.object(subprocess, "run") as mock_run:
            masm_mod._build()
        cmd = mock_run.call_args[0][0]
        include_flags = [a for a in cmd if a.startswith("-I")]
        assert len(include_flags) >= 2

    def test_build_output_flag(self) -> None:
        with patch.object(subprocess, "run") as mock_run:
            masm_mod._build()
        cmd = mock_run.call_args[0][0]
        assert "-o" in cmd
        o_idx = cmd.index("-o")
        output_path = cmd[o_idx + 1]
        assert "_binding" in output_path

    def test_build_source_files(self) -> None:
        with patch.object(subprocess, "run") as mock_run:
            masm_mod._build()
        cmd = mock_run.call_args[0][0]
        source_files = [a for a in cmd if a.endswith(".c")]
        assert len(source_files) == 2
        names = [Path(f).name for f in source_files]
        assert "binding.c" in names
        assert "parser.c" in names

    def test_build_darwin_flags(self) -> None:
        with (
            patch.object(subprocess, "run") as mock_run,
            patch.object(sys, "platform", "darwin"),
        ):
            masm_mod._build()
        cmd = mock_run.call_args[0][0]
        assert "-undefined" in cmd
        assert "dynamic_lookup" in cmd

    def test_build_linux_no_darwin_flags(self) -> None:
        with (
            patch.object(subprocess, "run") as mock_run,
            patch.object(sys, "platform", "linux"),
        ):
            masm_mod._build()
        cmd = mock_run.call_args[0][0]
        assert "-undefined" not in cmd
        assert "dynamic_lookup" not in cmd


def _mock_so_exists(exists: bool) -> Any:
    """Patch _SO_PATH.exists by replacing the module-level Path object."""
    real_str = str(masm_mod._SO_PATH)
    fake_path = MagicMock(spec=Path)
    fake_path.exists.return_value = exists
    fake_path.__str__ = MagicMock(return_value=real_str)
    fake_path.__fspath__ = MagicMock(return_value=real_str)
    return patch.object(masm_mod, "_SO_PATH", fake_path)


class TestLanguage:
    """Verify language() loads the grammar correctly."""

    def test_language_returns_capsule(self) -> None:
        result = masm_mod.language()
        assert result is not None

    def test_language_calls_build_if_missing(self) -> None:
        """If the .so doesn't exist, _build() should be called."""
        with (
            _mock_so_exists(False),
            patch.object(masm_mod, "_build") as mock_build,
            patch.object(importlib.util, "spec_from_file_location") as mock_spec,
        ):
            mock_loader = MagicMock()
            mock_module = MagicMock()
            mock_module.language.return_value = "capsule"
            mock_spec_obj = MagicMock()
            mock_spec_obj.loader = mock_loader
            mock_spec.return_value = mock_spec_obj

            with patch.object(
                importlib.util,
                "module_from_spec",
                return_value=mock_module,
            ):
                masm_mod.language()
        mock_build.assert_called_once()

    def test_language_skips_build_if_exists(self) -> None:
        """If the .so exists, _build() should not be called."""
        with patch.object(masm_mod, "_build") as mock_build:
            masm_mod.language()
        mock_build.assert_not_called()

    def test_language_raises_on_none_spec(self) -> None:
        with (
            _mock_so_exists(True),
            patch.object(
                importlib.util,
                "spec_from_file_location",
                return_value=None,
            ),
            pytest.raises(ImportError, match="Failed to load"),
        ):
            masm_mod.language()

    def test_language_raises_on_none_loader(self) -> None:
        mock_spec = MagicMock()
        mock_spec.loader = None
        with (
            _mock_so_exists(True),
            patch.object(
                importlib.util,
                "spec_from_file_location",
                return_value=mock_spec,
            ),
            pytest.raises(ImportError, match="Failed to load"),
        ):
            masm_mod.language()

    def test_language_loads_with_correct_name(self) -> None:
        with (
            _mock_so_exists(True),
            patch.object(importlib.util, "spec_from_file_location") as mock_spec_fn,
        ):
            mock_loader = MagicMock()
            mock_spec_obj = MagicMock()
            mock_spec_obj.loader = mock_loader
            mock_spec_fn.return_value = mock_spec_obj
            mock_mod = MagicMock()
            mock_mod.language.return_value = "capsule"
            with patch.object(
                importlib.util,
                "module_from_spec",
                return_value=mock_mod,
            ):
                masm_mod.language()
        mock_spec_fn.assert_called_once_with("_binding", str(masm_mod._SO_PATH))
