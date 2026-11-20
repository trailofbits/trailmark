"""Vendored tree-sitter-rust grammar with auto-compilation.

Grammar source: https://github.com/tree-sitter/tree-sitter-rust (MIT)
Vendored commit: 77a3747266f4d621d0757825e6b11edcbf991ca5
"""

from trailmark.tree_sitter_custom._native import bundled_language


def language() -> object:
    """Return the tree-sitter Language capsule, compiling changed inputs on demand."""
    return bundled_language("rust")
