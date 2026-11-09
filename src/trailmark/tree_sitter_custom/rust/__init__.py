"""Vendored tree-sitter-rust grammar with auto-compilation.

Grammar source: https://github.com/tree-sitter/tree-sitter-rust (MIT)
Vendored commit: 77a3747266f4d621d0757825e6b11edcbf991ca5
"""

from pathlib import Path

from trailmark.tree_sitter_custom._native import NativeGrammar

_GRAMMAR = NativeGrammar(
    Path(__file__).resolve().parent,
    (
        "binding.c",
        "src/parser.c",
        "src/scanner.c",
        "src/tree_sitter/alloc.h",
        "src/tree_sitter/array.h",
        "src/tree_sitter/parser.h",
    ),
)


def language() -> object:
    """Return the tree-sitter Language capsule, compiling changed inputs on demand."""
    return _GRAMMAR.language()
