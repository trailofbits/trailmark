"""Distinguish PHP callable creation from invocation without evaluating code."""

from __future__ import annotations

from tree_sitter import Node

_CALLS = frozenset(
    {
        "function_call_expression",
        "member_call_expression",
        "nullsafe_member_call_expression",
        "scoped_call_expression",
    }
)


def unwrap_callable(node: Node) -> Node:
    """Remove expression parentheses while retaining the callable's source span."""
    while node.type == "parenthesized_expression":
        children = [child for child in node.named_children if child.type != "comment"]
        if len(children) != 1:
            break
        node = children[0]
    return node


def acquisition_form(node: Node) -> str | None:
    """Name first-class or partial callable creation, not nested argument expressions."""
    if node.type not in _CALLS:
        return None
    arguments = node.child_by_field_name("arguments")
    if arguments is None:
        return None
    parts = [child for child in arguments.named_children if child.type != "comment"]
    variadic = any(child.type == "variadic_placeholder" for child in parts)
    placeholder = any(
        child.type == "argument"
        and any(value.type == "argument_placeholder" for value in child.named_children)
        for child in parts
    )
    if placeholder or variadic and len(parts) > 1:
        return "partial_application"
    return "first_class_callable" if variadic else None


def pipe_target(node: Node) -> Node | None:
    """Return the expression whose resulting callable a pipe executes."""
    operator = node.child_by_field_name("operator")
    if node.type != "binary_expression" or operator is None or operator.type != "|>":
        return None
    right = node.child_by_field_name("right")
    return unwrap_callable(right) if right is not None else None


def inline_callable(node: Node) -> bool:
    """Whether the expression directly describes a callable's known implementation."""
    return acquisition_form(node) is not None or node.type in {
        "anonymous_function",
        "arrow_function",
    }
