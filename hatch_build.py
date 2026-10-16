"""Generate the Verus C parser for checkouts; ship it in wheels and sdists."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

GENERATOR_VERSION = "0.25.3"
GENERATOR_ABI = "15"
GRAMMAR_PATH = Path("src/trailmark/tree_sitter_custom/verus")
GENERATED_FILES = (
    "src/parser.c",
    "src/tree_sitter/parser.h",
    "src/tree_sitter/alloc.h",
    "src/tree_sitter/array.h",
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inputs(grammar: Path) -> str:
    return hashlib.sha256(
        (
            GENERATOR_VERSION
            + GENERATOR_ABI
            + _digest(grammar / "grammar.js")
            + _digest(grammar / "tree-sitter.json")
        ).encode()
    ).hexdigest()


def _current(grammar: Path, inputs: str) -> bool:
    try:
        recorded = json.loads((grammar / ".generated.json").read_text())
        return recorded == {
            "inputs": inputs,
            "outputs": {name: _digest(grammar / name) for name in GENERATED_FILES},
        }
    except (OSError, ValueError):
        return False


def _generator(root: Path) -> Path:
    name = "tree-sitter.exe" if sys.platform == "win32" else "tree-sitter"
    executable = root / "node_modules/tree-sitter-cli" / name
    if not executable.is_file():
        installed = shutil.which("tree-sitter")
        if installed is None:
            raise RuntimeError("Run npm ci before building Trailmark from a Git checkout")
        executable = Path(installed)
    result = subprocess.run(  # noqa: S603
        [str(executable), "--version"], capture_output=True, text=True, check=True
    )
    if result.stdout.split()[:2] != ["tree-sitter", GENERATOR_VERSION]:
        raise RuntimeError(f"Verus generation requires tree-sitter {GENERATOR_VERSION}; run npm ci")
    return executable


def generate_verus(root: Path) -> None:
    grammar = root / GRAMMAR_PATH
    inputs = _inputs(grammar)
    if _current(grammar, inputs):
        return
    executable = _generator(root)
    with tempfile.TemporaryDirectory(prefix=".verus-", dir=grammar.parent) as directory:
        temporary = Path(directory)
        for name in ("grammar.js", "tree-sitter.json"):
            shutil.copyfile(grammar / name, temporary / name)
        subprocess.run(  # noqa: S603
            [str(executable), "generate", "--abi", GENERATOR_ABI], cwd=temporary, check=True
        )
        for name in GENERATED_FILES:
            destination = grammar / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            (temporary / name).replace(destination)
    stamp = {
        "inputs": inputs,
        "outputs": {name: _digest(grammar / name) for name in GENERATED_FILES},
    }
    (grammar / ".generated.json").write_text(json.dumps(stamp, sort_keys=True) + "\n")


def get_build_hook() -> type:
    # Hatchling is provided by the isolated PEP 517 build environment.
    from hatchling.builders.hooks.plugin.interface import (  # ty: ignore[unresolved-import]
        BuildHookInterface,
    )

    class VerusBuildHook(BuildHookInterface):
        def initialize(self, version: str, build_data: dict[str, Any]) -> None:
            generate_verus(Path(self.root))
            build_data["artifacts"].extend(
                (GRAMMAR_PATH / name).as_posix() for name in (*GENERATED_FILES, ".generated.json")
            )

    return VerusBuildHook
