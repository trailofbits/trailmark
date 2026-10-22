"""Infer calls through file-local constant, designated callback tables."""

from __future__ import annotations

from dataclasses import replace

from tree_sitter import Node

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind, Parameter
from trailmark.parsers._bindings import lexical_declaration
from trailmark.parsers._common import node_text
from trailmark.parsers._walk import walk_owned
from trailmark.parsers.c._calls import syntax_error_context


def resolve_constant_callbacks(root: Node, module: str, graph: CodeGraph) -> None:
    """Bind each call occurrence independently, including chained calls."""
    tables = _tables(root, module, graph)
    if not any(tables.values()):
        return
    calls = {
        (
            node.start_point.row + 1,
            node.start_point.column,
            node.end_point.row + 1,
            node.end_point.column,
        ): node
        for node in walk_owned(root)
        if node.type == "call_expression"
    }
    edges = []
    for edge in graph.edges:
        location = edge.location
        if edge.kind == EdgeKind.CALLS and location is not None:
            key = (location.start_line, location.start_col, location.end_line, location.end_col)
            occurrence = calls.get(key)
            if occurrence is not None:
                edge = _bind(edge, occurrence, tables, graph)
        edges.append(edge)
    graph.edges[:] = edges


def _tables(root: Node, module: str, graph: CodeGraph) -> dict[str, dict[str, str] | None]:
    tables: dict[str, dict[str, str] | None] = {}
    for declaration in walk_owned(
        root,
        boundaries=frozenset(
            {"function_definition", "declaration", "type_definition", "struct_specifier"}
        ),
    ):
        if declaration.type != "declaration":
            continue
        constant = _constant(declaration)
        for init in declaration.children_by_field_name("declarator"):
            name = init.child_by_field_name("declarator")
            if name is None or name.type != "identifier":
                continue
            ident = node_text(name)
            value = init.child_by_field_name("value")
            if ident in tables or not constant:
                tables[ident] = None
            elif value is not None and value.type == "initializer_list":
                tables[ident] = _fields(value, module, graph)
    return tables


def _constant(declaration: Node) -> bool:
    qualifiers = {(child.type, node_text(child)) for child in declaration.named_children}
    return (
        ("storage_class_specifier", "static") in qualifiers
        and ("type_qualifier", "const") in qualifiers
        and not _conditional(declaration)
    )


def _bind(
    edge: CodeEdge, occurrence: Node, tables: dict[str, dict[str, str] | None], graph: CodeGraph
) -> CodeEdge:
    attrs = dict(edge.attributes)
    if attrs.get("syntax_recovered") or attrs.get("macro_signature_unresolved"):
        return edge
    if syntax_error_context(occurrence):
        return edge
    receiver = _receiver(occurrence)
    if receiver is None:
        return edge
    name, field = receiver
    table = tables.get(name)
    if table is None or _shadowed(occurrence, name, graph.nodes[edge.source_id].parameters):
        return edge
    target = table.get(field)
    if target is None:
        return edge
    return replace(
        edge,
        target_id=target,
        confidence=EdgeConfidence.INFERRED,
        attributes=(*edge.attributes, ("callback_table", name), ("callback_field", field)),
    )


def _receiver(occurrence: Node) -> tuple[str, str] | None:
    function = occurrence.child_by_field_name("function")
    if function is None or function.type != "field_expression":
        return None
    receiver = function.child_by_field_name("argument")
    field = function.child_by_field_name("field")
    if receiver is None or receiver.type != "identifier" or field is None:
        return None
    if not any(child.type == "." for child in function.children):
        return None
    return node_text(receiver), node_text(field)


def _shadowed(occurrence: Node, name: str, parameters: tuple[Parameter, ...]) -> bool:
    return (
        any(param.name == name for param in parameters)
        or lexical_declaration(
            occurrence,
            name,
            scopes=frozenset({"function_definition", "compound_statement"}),
            declarations=(("declaration", "declarator"), ("init_declarator", "declarator")),
        )
        is not None
    )


def _fields(value: Node, module: str, graph: CodeGraph) -> dict[str, str]:
    fields: dict[str, str] = {}
    seen: set[str] = set()
    for pair in value.named_children:
        designators = pair.children_by_field_name("designator")
        if len(designators) != 1 or designators[0].type != "field_designator":
            continue
        name = node_text(designators[0]).removeprefix(".").strip()
        target = _target(pair.child_by_field_name("value"), module, graph)
        if name in seen:
            fields.pop(name, None)
        elif target is not None:
            fields[name] = target
        seen.add(name)
    return fields


def _target(node: Node | None, module: str, graph: CodeGraph) -> str | None:
    if node is not None and node.type == "pointer_expression":
        if not any(child.type == "&" for child in node.children):
            return None
        node = node.child_by_field_name("argument")
    if node is None or node.type != "identifier":
        return None
    node_id = f"{module}:{node_text(node)}"
    unit = graph.nodes.get(node_id)
    return node_id if unit is not None and unit.kind == NodeKind.FUNCTION else None


def _conditional(node: Node) -> bool:
    parent = node.parent
    while parent is not None:
        if parent.type.startswith("preproc_"):
            return True
        parent = parent.parent
    return False
