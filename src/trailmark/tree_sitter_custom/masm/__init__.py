"""Vendored tree-sitter-masm grammar with auto-compilation.

Grammar source: https://github.com/trailofbits/tree-sitter-masm (MIT)
"""

from pathlib import Path

from trailmark.tree_sitter_custom._native import NativeGrammar

_GRAMMAR = NativeGrammar(
    Path(__file__).resolve().parent,
    (
        "binding.c",
        "src/parser.c",
        "src/tree_sitter/alloc.h",
        "src/tree_sitter/array.h",
        "src/tree_sitter/parser.h",
    ),
)


def language() -> object:
    """Return the tree-sitter Language capsule, compiling changed inputs on demand."""
    return _GRAMMAR.language()
