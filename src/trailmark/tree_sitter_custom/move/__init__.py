"""Vendored tree-sitter-move grammar with auto-compilation.

Grammar source: https://github.com/MystenLabs/sui
Path: external-crates/move/tooling/tree-sitter
Commit: 5934ceca7dc8e16a1a0f8715632f704f7b9f18d4
License: Apache-2.0
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import sysconfig
from pathlib import Path

_DIR = Path(__file__).resolve().parent
_EXT_SUFFIX = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
_SO_PATH = _DIR / f"_binding{_EXT_SUFFIX}"


def _existing_binding_path() -> Path | None:
    """Return an already-compiled binding path, tolerating EXT_SUFFIX mismatches."""
    if _SO_PATH.exists():
        return _SO_PATH
    for candidate in sorted(_DIR.glob("_binding*.so")):
        if candidate.exists():
            return candidate
    return None


def _build() -> None:
    """Compile the tree-sitter-move C extension."""
    include_python = sysconfig.get_path("include")
    src_dir = _DIR / "src"

    if not os.access(_DIR, os.W_OK):
        msg = (
            f"Cannot build Move grammar extension in read-only directory: {_DIR}. "
            "Install a prebuilt wheel or run Trailmark from a writable environment."
        )
        raise PermissionError(msg)

    cmd = ["cc", "-shared", "-fPIC", "-O2", "-std=c11"]
    if sys.platform == "darwin":
        cmd.extend(["-undefined", "dynamic_lookup"])
    cmd.extend([f"-I{include_python}", f"-I{src_dir}"])
    cmd.extend(
        [
            str(_DIR / "binding.c"),
            str(src_dir / "parser.c"),
            "-o",
            str(_SO_PATH),
        ]
    )
    subprocess.run(cmd, check=True)  # noqa: S603 - all args are hardcoded paths


def language() -> object:
    """Return the tree-sitter Language PyCapsule for Move.

    Auto-compiles the grammar on first use if needed.
    """
    so_path = _existing_binding_path()
    if so_path is None:
        try:
            _build()
        except (PermissionError, subprocess.CalledProcessError) as err:
            msg = (
                f"Failed to build Move grammar extension at {_SO_PATH}: {err}. "
                "This can happen in read-only installs (for example, Nix store paths)."
            )
            raise RuntimeError(msg) from err
        so_path = _existing_binding_path()
        if so_path is None:
            msg = f"Build succeeded but no compiled Move grammar found under {_DIR}"
            raise RuntimeError(msg)
    spec = importlib.util.spec_from_file_location(
        "_binding",
        str(so_path),
    )
    if spec is None or spec.loader is None:
        msg = f"Failed to load compiled grammar from {so_path}"
        raise ImportError(msg)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.language()
