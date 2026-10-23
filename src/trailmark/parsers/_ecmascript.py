"""Shared JavaScript/TypeScript callable discovery and lexical ownership."""

from __future__ import annotations

from collections.abc import Callable, Iterator

from tree_sitter import Node

from trailmark.models.edges import EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import BranchInfo, CodeUnit, NodeKind, SourceLocation, TypeRef
from trailmark.parsers._binding_index import syntax_nodes
from trailmark.parsers._bindings import guard_call
from trailmark.parsers._common import (
    _collect_throw_type,
    _extract_condition_text,
    add_contains_edge,
    extract_call_name,
    make_location,
    node_text,
)
from trailmark.parsers._declarations import prepare_call_bindings
from trailmark.parsers._nesting import nesting_diagnostic
from trailmark.parsers._walk import owned_syntax

CLASSES = frozenset({"class_declaration", "abstract_class_declaration", "class"})
NAMESPACES = frozenset({"internal_module", "module"})

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


def body_info(
    body: Node | None,
    path: str,
    *,
    branch_types: frozenset[str],
    throw_types: frozenset[str],
    boundaries: frozenset[str],
) -> tuple[list[BranchInfo], list[TypeRef], list[tuple[str, Node]]]:
    """Collect ECMAScript body sites with the selected JS, TS, or TSX grammar."""
    branches: list[BranchInfo] = []
    exceptions: list[TypeRef] = []
    calls: list[tuple[str, Node]] = []
    if body is not None:
        types = branch_types | throw_types | {"call_expression"}
        for node in _execution_sites(body, types, boundaries):
            if node.type in branch_types:
                branches.append(
                    BranchInfo(make_location(node, path), _extract_condition_text(node))
                )
            if node.type in throw_types:
                _collect_throw_type(node, exceptions)
            if node.type == "call_expression" and (name := extract_call_name(node)):
                calls.append((name, node))
    return branches, exceptions, calls


def _execution_sites(
    body: Node, types: frozenset[str], boundaries: frozenset[str]
) -> Iterator[Node]:
    """Include class evaluation sites while keeping method bodies with their owners."""
    boundaries = boundaries - CLASSES
    for node in owned_syntax(
        body, types | {"method_definition"}, boundaries=boundaries, tokens=types
    ):
        if node.type == "method_definition":
            name = node.child_by_field_name("name")
            if name is not None and name.type == "computed_property_name":
                yield from owned_syntax(name, types, boundaries=boundaries, tokens=types)
        else:
            yield node


def discover_callables(
    root: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
    extract: Callable[[Node, str, str, str | None], str | None],
) -> None:
    """Preserve nested and anonymous bodies without implying their execution."""
    by_location = _callable_locations(graph, file_path)
    stack: list[tuple[int, str, str | None]] = [(root.end_byte + 1, module_id, None)]
    calls: dict[SourceLocation, Node] = {}
    for node in _bounded_discovery(root, file_path, graph):
        if node.type == "call_expression":
            calls[make_location(node, file_path)] = node
            continue
        while node.start_byte >= stack[-1][0]:
            stack.pop()
        _, owner, class_owner = stack[-1]
        if node.type in CALLABLES:
            class_owner = _callable_class_owner(node, class_owner)
            node_id = _discover_callable(node, owner, class_owner, by_location, extract)
            if node_id is None:
                continue
            owner = node_id
        else:
            position = (node.start_point.row + 1, node.start_point.column)
            owner = by_location.get(position) or _discover_container(
                node, owner, graph, file_path=file_path
            )
            class_owner = owner if node.type in CLASSES else None
        stack.append((node.end_byte, owner, class_owner))
    # Forward nested declarations are available only after discovery completes.
    _guard_discovered_calls(graph, calls)


def _bounded_discovery(root: Node, file_path: str, graph: CodeGraph) -> list[Node]:
    containers = CALLABLES | CLASSES | NAMESPACES
    nodes = []
    enclosing: list[int] = []
    skipped_until = -1
    for node in syntax_nodes(root, containers | {"call_expression"}):
        if node.start_byte < skipped_until:
            continue
        while enclosing and node.start_byte >= enclosing[-1]:
            enclosing.pop()
        if node.type in containers:
            if len(enclosing) >= 48:
                graph.parse_diagnostics.append(nesting_diagnostic(node, file_path, graph.language))
                skipped_until = node.end_byte
                continue
            enclosing.append(node.end_byte)
        nodes.append(node)
    return nodes


def _discover_container(node: Node, owner: str, graph: CodeGraph, *, file_path: str) -> str:
    namespace = node.type in NAMESPACES
    name_node = node.child_by_field_name("name")
    name = (
        node_text(name_node)
        if name_node is not None
        else f"$class@{node.start_point.row + 1}_{node.start_point.column}"
    )
    separator = "." if ":" in owner else ":"
    node_id = owner + separator + name
    if namespace and node_id in graph.nodes:
        return node_id
    if node_id in graph.nodes:
        node_id += f"@{node.start_point.row + 1}_{node.start_point.column}"
    graph.nodes[node_id] = CodeUnit(
        id=node_id,
        name=name,
        kind=NodeKind.NAMESPACE if namespace else NodeKind.CLASS,
        location=make_location(node, file_path),
    )
    add_contains_edge(graph, owner, node_id)
    return node_id


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


def _guard_discovered_calls(graph: CodeGraph, calls: dict[SourceLocation, Node]) -> None:
    prepare_call_bindings(graph)
    graph.edges[:] = [
        guard_call(edge, calls[edge.location], graph=graph)
        if edge.kind == EdgeKind.CALLS
        and not dict(edge.attributes).get("import_binding")
        and edge.location is not None
        and edge.location in calls
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


def class_member_callable(node: Node, class_owner: str | None) -> bool:
    """Class methods and arrow-valued fields retain method metadata."""
    if class_owner is None:
        return False
    if node.type == "method_definition":
        return True
    parent = node.parent
    return (
        node.type == "arrow_function"
        and parent is not None
        and parent.type in {"field_definition", "public_field_definition"}
    )
