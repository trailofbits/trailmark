"""Bundled Move grammar from MystenLabs/sui (Apache-2.0).

Source: external-crates/move/tooling/tree-sitter/grammar.js
Commit: f3fd7754ebe506e2a8b1cb4e0ddb45be3e5eb6c1
"""

from pathlib import Path

from trailmark.tree_sitter_custom._native import NativeGrammar

_GRAMMAR = NativeGrammar(
    Path(__file__).resolve().parent,
    ("binding.c", "src/parser.c", "src/tree_sitter/parser.h"),
)


def language() -> object:
    """Return the tree-sitter Language capsule, compiling changed inputs on demand."""
    return _GRAMMAR.language()
