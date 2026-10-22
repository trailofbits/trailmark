"""Shared JavaScript/TypeScript callable discovery and lexical ownership."""

from __future__ import annotations

from collections.abc import Callable

from tree_sitter import Node

from trailmark.models.edges import EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind
from trailmark.parsers._bindings import guard_call
from trailmark.parsers._common import node_text

CALLABLES = frozenset(
    {
        "function_declaration",
        "generator_function_declaration",
        "arrow_function",
        "function_expression",
        "generator_function",
        "method_definition",
        "function",
    }
)


def discover_callables(
    root: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
    extract: Callable[[Node, str, str, bool], str | None],
) -> None:
    """Preserve nested and anonymous bodies without implying their execution."""
    by_location = {
        (unit.location.start_line, unit.location.start_col): unit.id
        for unit in graph.nodes.values()
        if unit.location.file_path == file_path and unit.kind != NodeKind.MODULE
    }
    stack = [(root, module_id, False)]
    calls: dict[tuple[int, int], Node] = {}
    while stack:
        node, owner, class_owner = stack.pop()
        if node.type == "call_expression":
            calls[(node.start_point.row + 1, node.start_point.column)] = node
        if node.type in CALLABLES:
            position = (node.start_point.row + 1, node.start_point.column)
            node_id = by_location.get(position)
            if node_id is None:
                name = _callable_name(node)
                if (
                    node.type == "method_definition"
                    and node.parent is not None
                    and node.parent.type == "object"
                ):
                    name = f"{_object_binding(node.parent)}.{name}"
                node_id = extract(node, name, owner, class_owner)
                if node_id is None:
                    continue
            owner, class_owner = node_id, False
        elif node.type in {"class_declaration", "class"}:
            position = (node.start_point.row + 1, node.start_point.column)
            owner = by_location.get(position, owner)
            class_owner = True
        stack.extend((child, owner, class_owner) for child in reversed(node.named_children))
    # Forward nested declarations are available only after discovery completes.
    graph.edges[:] = [
        guard_call(
            edge, calls[(edge.location.start_line, edge.location.start_col or 0)], graph=graph
        )
        if edge.kind == EdgeKind.CALLS
        and edge.location is not None
        and (edge.location.start_line, edge.location.start_col or 0) in calls
        else edge
        for edge in graph.edges
    ]


def _callable_name(node: Node) -> str:
    name = node.child_by_field_name("name")
    if name is not None:
        return node_text(name)
    parent = node.parent
    if parent is not None:
        field = {
            "variable_declarator": "name",
            "assignment_expression": "left",
            "field_definition": "property",
            "public_field_definition": "name",
            "pair": "key",
        }.get(parent.type)
        if field is not None:
            binding = parent.child_by_field_name(field)
            if binding is not None:
                return node_text(binding).removeprefix("module.exports.").removeprefix("exports.")
        if parent.type == "export_statement":
            return "default"
    return f"$lambda@{node.start_point.row + 1}_{node.start_point.column}"


def _object_binding(node: Node) -> str:
    parts = []
    current: Node | None = node
    while current is not None:
        parent = current.parent
        if parent is None:
            break
        field = {"variable_declarator": "name", "assignment_expression": "left", "pair": "key"}.get(
            parent.type
        )
        binding = parent.child_by_field_name(field) if field else None
        if binding is not None:
            parts.append(node_text(binding))
        if parent.type != "pair":
            break
        current = parent.parent
    return (
        ".".join(reversed(parts)) or f"$object@{node.start_point.row + 1}_{node.start_point.column}"
    )
