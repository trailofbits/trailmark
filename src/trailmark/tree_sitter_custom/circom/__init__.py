"""Vendored tree-sitter-circom grammar with auto-compilation.

Grammar source: https://github.com/Decurity/tree-sitter-circom (MIT)
Commit: 02150524228b1e6afef96949f2d6b7cc0aaf999e
"""

from trailmark.tree_sitter_custom._native import bundled_language


def language() -> object:
    """Return the tree-sitter Language capsule, compiling changed inputs on demand."""
    return bundled_language("circom")
