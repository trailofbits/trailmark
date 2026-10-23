"""Identity-preserving ECMAScript expression wrappers."""

from tree_sitter import Node

_WRAPPERS = frozenset(
    {
        "parenthesized_expression",
        "as_expression",
        "satisfies_expression",
        "type_assertion",
        "non_null_expression",
    }
)


def unwrap_callable(node: Node) -> Node:
    """Remove syntax-only wrappers without looking through evaluated expressions."""
    while node.type in _WRAPPERS:
        children = node.named_children
        if not children:
            break
        node = children[-1] if node.type == "type_assertion" else children[0]
    return node


def callable_parent(node: Node) -> Node | None:
    """Find the binding container through syntax-only expression wrappers."""
    parent = node.parent
    while parent is not None and parent.type in _WRAPPERS:
        children = parent.named_children
        child = children[-1] if parent.type == "type_assertion" else children[0]
        if child != node:
            break
        node, parent = parent, parent.parent
    return parent
