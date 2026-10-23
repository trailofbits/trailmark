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
    root_is_owner: bool = True,
) -> Iterator[Node]:
    """Visit syntax in source order without entering nested execution owners.

    A boundary is yielded so a frontend can extract its declaration separately,
    but its children are excluded. The supplied root acts as the owner by default.
    Set ``root_is_owner=False`` when an expression body can itself be a nested
    callable boundary.
    """
    stack = (
        [root]
        if include_root
        else list(reversed(root.named_children if named_only else root.children))
    )
    while stack:
        node = stack.pop()
        yield node
        if (node != root or not root_is_owner) and node.type in boundaries:
            continue
        stack.extend(reversed(node.named_children if named_only else node.children))
