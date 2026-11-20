"""Bundled Move grammar from MystenLabs/sui (Apache-2.0).

Source: external-crates/move/tooling/tree-sitter/grammar.js
Commit: f3fd7754ebe506e2a8b1cb4e0ddb45be3e5eb6c1
"""

from trailmark.tree_sitter_custom._native import bundled_language


def language() -> object:
    """Return the tree-sitter Language capsule, compiling changed inputs on demand."""
    return bundled_language("move")
