"""Vendored tree-sitter-verus grammar with auto-compilation.

Grammar source: https://github.com/secure-foundations/tree-sitter-verus (MIT)
Vendored commit: 0c939ef2ec0a188c3cf24518474fb8082db02ec4
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

_DIR = Path(__file__).resolve().parent
_EXT_SUFFIX = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
_SOURCES = (
    "binding.c",
    "src/parser.c",
    "src/scanner.c",
    "src/tree_sitter/alloc.h",
    "src/tree_sitter/array.h",
    "src/tree_sitter/parser.h",
)


def _compiler_flags() -> list[str]:
    flags = ["cc", "-shared", "-fPIC", "-O2", "-std=c11"]
    if sys.platform == "darwin":
        flags.extend(["-undefined", "dynamic_lookup"])
    flags.extend([f"-I{sysconfig.get_path('include')}", f"-I{_DIR / 'src'}"])
    return flags


def _binding_path() -> Path:
    """Identify the compiled inputs and ABI, independently of file timestamps."""
    digest = hashlib.sha256()
    configuration = (_compiler_flags(), sysconfig.get_config_var("SOABI"), sysconfig.get_platform())
    digest.update(repr(configuration).encode())
    for relative in _SOURCES:
        digest.update(relative.encode())
        digest.update(hashlib.sha256((_DIR / relative).read_bytes()).digest())
    return _DIR / f"_binding_{digest.hexdigest()}{_EXT_SUFFIX}"


def _build(output_path: Path) -> None:
    """Publish only a completely compiled binding; leave no failed build behind."""
    with tempfile.NamedTemporaryFile(
        dir=output_path.parent, prefix=".verus-", suffix=_EXT_SUFFIX, delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        cmd = _compiler_flags()
        cmd.extend(str(_DIR / relative) for relative in _SOURCES if relative.endswith(".c"))
        cmd.extend(["-o", str(temporary_path)])
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)  # noqa: S603
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or "no compiler diagnostic").strip()
            msg = f"Verus grammar compilation failed (exit {exc.returncode}): {detail}"
            raise ImportError(msg) from exc
        os.replace(temporary_path, output_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def language() -> object:
    """Return the tree-sitter Language PyCapsule for Verus.

    Compiles when the sources, build options, or Python ABI change.
    """
    binding_path = _binding_path()
    if not binding_path.exists():
        _build(binding_path)
    spec = importlib.util.spec_from_file_location(
        "_binding",
        str(binding_path),
    )
    if spec is None or spec.loader is None:
        msg = f"Failed to load compiled grammar from {binding_path}"
        raise ImportError(msg)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    entrypoint = getattr(mod, "language", None)
    if not callable(entrypoint):
        msg = f"Verus grammar binding has no callable language entrypoint: {binding_path}"
        raise ImportError(msg)
    return entrypoint()
