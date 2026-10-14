"""Vendored tree-sitter-circom grammar with auto-compilation.

Grammar source: https://github.com/Decurity/tree-sitter-circom (MIT)
Commit: 02150524228b1e6afef96949f2d6b7cc0aaf999e
"""

from pathlib import Path

from trailmark.tree_sitter_custom._native import NativeGrammar

_GRAMMAR = NativeGrammar(
    Path(__file__).resolve().parent,
    (
        "binding.c",
        "src/parser.c",
        "src/tree_sitter/parser.h",
    ),
)


def language() -> object:
    """Return the tree-sitter Language capsule, compiling changed inputs on demand."""
    return _GRAMMAR.language()
