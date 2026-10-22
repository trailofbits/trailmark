"""Generate bundled C parsers for checkouts; ship them in wheels and sdists."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

GENERATOR_VERSION = "0.27.1"
GRAMMAR_ROOT = Path("src/trailmark/tree_sitter_custom")
GRAMMARS = {"circom": "14", "move": "14", "verus": "15"}
GENERATED_FILES = {
    name: ("src/parser.c", "src/tree_sitter/parser.h")
    + (("src/tree_sitter/alloc.h", "src/tree_sitter/array.h") if name == "verus" else ())
    for name in GRAMMARS
}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inputs(grammar: Path, abi: str) -> str:
    return hashlib.sha256(
        (
            GENERATOR_VERSION
            + abi
            + _digest(grammar / "grammar.js")
            + _digest(grammar / "tree-sitter.json")
        ).encode()
    ).hexdigest()


def _current(grammar: Path, inputs: str, outputs: tuple[str, ...]) -> bool:
    try:
        recorded = json.loads((grammar / ".generated.json").read_text())
        return recorded == {
            "inputs": inputs,
            "outputs": {name: _digest(grammar / name) for name in outputs},
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
        raise RuntimeError(
            f"Grammar generation requires tree-sitter {GENERATOR_VERSION}; run npm ci"
        )
    return executable


def generate_grammar(root: Path, name: str) -> None:
    grammar = root / GRAMMAR_ROOT / name
    abi = GRAMMARS[name]
    outputs = GENERATED_FILES[name]
    inputs = _inputs(grammar, abi)
    if _current(grammar, inputs, outputs):
        return
    executable = _generator(root)
    with tempfile.TemporaryDirectory(prefix=f".{name}-", dir=grammar.parent) as directory:
        temporary = Path(directory)
        for relative in ("grammar.js", "tree-sitter.json"):
            shutil.copyfile(grammar / relative, temporary / relative)
        subprocess.run(  # noqa: S603
            [str(executable), "generate", "--abi", abi], cwd=temporary, check=True
        )
        for relative in outputs:
            destination = grammar / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            (temporary / relative).replace(destination)
    stamp = {
        "inputs": inputs,
        "outputs": {name: _digest(grammar / name) for name in outputs},
    }
    (grammar / ".generated.json").write_text(json.dumps(stamp, sort_keys=True) + "\n")


def get_build_hook() -> type:
    # Hatchling is provided by the isolated PEP 517 build environment.
    from hatchling.builders.hooks.plugin.interface import (  # ty: ignore[unresolved-import]
        BuildHookInterface,
    )

    class GrammarBuildHook(BuildHookInterface):
        def initialize(self, version: str, build_data: dict[str, Any]) -> None:
            for name in GRAMMARS:
                generate_grammar(Path(self.root), name)
                build_data["artifacts"].extend(
                    (GRAMMAR_ROOT / name / relative).as_posix()
                    for relative in (*GENERATED_FILES[name], ".generated.json")
                )

    return GrammarBuildHook
