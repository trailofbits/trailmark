"""Vendored tree-sitter-masm grammar with auto-compilation.

Grammar source: https://github.com/trailofbits/tree-sitter-masm (MIT)
"""

from trailmark.tree_sitter_custom._native import bundled_language


def language() -> object:
    """Return the tree-sitter Language capsule, compiling changed inputs on demand."""
    return bundled_language("masm")
