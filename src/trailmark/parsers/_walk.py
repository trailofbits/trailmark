"""Iterative, owner-bounded syntax traversal shared by frontends."""

from __future__ import annotations

from collections.abc import Iterator

from tree_sitter import Node


def walk_owned(
    root: Node,
    *,
    include_root: bool = True,
    boundaries: frozenset[str] = frozenset(),
    named_only: bool = True,
) -> Iterator[Node]:
    """Visit syntax in source order without entering nested execution owners.

    A boundary is yielded so a frontend can extract its declaration separately,
    but its children are excluded. The supplied root is always an owner, even
    when its type is a boundary (for example, an expression-bodied lambda).
    """
    stack = (
        [root]
        if include_root
        else list(reversed(root.named_children if named_only else root.children))
    )
    while stack:
        node = stack.pop()
        yield node
        if node != root and node.type in boundaries:
            continue
        stack.extend(reversed(node.named_children if named_only else node.children))
