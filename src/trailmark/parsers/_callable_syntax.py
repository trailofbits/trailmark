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


def _wrapped_expression(node: Node) -> Node | None:
    children = node.named_children
    if node.type == "type_assertion":
        children.reverse()
    return next((child for child in children if not child.is_extra), None)


def unwrap_callable(node: Node) -> Node:
    """Remove syntax-only wrappers without looking through evaluated expressions."""
    while node.type in _WRAPPERS:
        child = _wrapped_expression(node)
        if child is None:
            break
        node = child
    return node


def callable_parent(node: Node) -> Node | None:
    """Find the binding container through syntax-only expression wrappers."""
    parent = node.parent
    while parent is not None and parent.type in _WRAPPERS:
        child = _wrapped_expression(parent)
        if child != node:
            break
        node, parent = parent, parent.parent
    return parent
