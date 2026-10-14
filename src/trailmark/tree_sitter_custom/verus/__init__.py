"""Vendored tree-sitter-verus grammar with auto-compilation.

Grammar source: https://github.com/secure-foundations/tree-sitter-verus (MIT)
Vendored commit: 0c939ef2ec0a188c3cf24518474fb8082db02ec4
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
