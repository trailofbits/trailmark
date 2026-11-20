"""Vendored tree-sitter-verus grammar with auto-compilation.

Grammar source: https://github.com/secure-foundations/tree-sitter-verus (MIT)
Vendored commit: 0c939ef2ec0a188c3cf24518474fb8082db02ec4
"""

from trailmark.tree_sitter_custom._native import bundled_language


def language() -> object:
    """Return the tree-sitter Language capsule, compiling changed inputs on demand."""
    return bundled_language("verus")
