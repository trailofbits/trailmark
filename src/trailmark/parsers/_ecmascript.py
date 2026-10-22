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
    extract: Callable[[Node, str, str, str | None], str | None],
) -> None:
    """Preserve nested and anonymous bodies without implying their execution."""
    by_location = _callable_locations(graph, file_path)
    stack: list[tuple[Node, str, str | None]] = [(root, module_id, None)]
    calls: dict[tuple[int, int], Node] = {}
    while stack:
        node, owner, class_owner = stack.pop()
        if node.type == "call_expression":
            calls[(node.start_point.row + 1, node.start_point.column)] = node
        if node.type in CALLABLES:
            class_owner = _callable_class_owner(node, class_owner)
            node_id = _discover_callable(node, owner, class_owner, by_location, extract)
            if node_id is None:
                continue
            owner = node_id
        elif node.type in {"class_declaration", "class"}:
            position = (node.start_point.row + 1, node.start_point.column)
            owner = by_location.get(position, owner)
            class_owner = owner
        stack.extend((child, owner, class_owner) for child in reversed(node.named_children))
    # Forward nested declarations are available only after discovery completes.
    _guard_discovered_calls(graph, calls)


def _callable_class_owner(node: Node, owner: str | None) -> str | None:
    if node.type == "arrow_function" or (
        node.type == "method_definition"
        and node.parent is not None
        and node.parent.type == "class_body"
    ):
        return owner
    return None


def _discover_callable(
    node: Node,
    owner: str,
    class_owner: str | None,
    by_location: dict[tuple[int, int | None], str],
    extract: Callable[[Node, str, str, str | None], str | None],
) -> str | None:
    position = (node.start_point.row + 1, node.start_point.column)
    known = by_location.get(position)
    return known if known is not None else extract(node, _discovered_name(node), owner, class_owner)


def _callable_locations(graph: CodeGraph, path: str) -> dict[tuple[int, int | None], str]:
    return {
        (unit.location.start_line, unit.location.start_col): unit.id
        for unit in graph.nodes.values()
        if unit.location.file_path == path and unit.kind != NodeKind.MODULE
    }


def declared_name(node: Node, override: str | None) -> str | None:
    """Read a declaration name while retaining an explicit anonymous identity."""
    if override is not None:
        return override
    name = node.child_by_field_name("name")
    return node_text(name) if name is not None else None


def _guard_discovered_calls(graph: CodeGraph, calls: dict[tuple[int, int], Node]) -> None:
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


def _discovered_name(node: Node) -> str:
    name = _callable_name(node)
    if (
        node.type == "method_definition"
        and node.parent is not None
        and node.parent.type == "object"
    ):
        name = f"{_object_binding(node.parent)}.{name}"
    return name


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
