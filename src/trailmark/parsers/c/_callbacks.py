"""Infer calls through file-local constant, designated callback tables."""

from __future__ import annotations

from dataclasses import replace

from tree_sitter import Node

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind
from trailmark.parsers._bindings import lexical_declaration
from trailmark.parsers._common import node_text
from trailmark.parsers._walk import walk_owned


def resolve_constant_callbacks(root: Node, module: str, graph: CodeGraph) -> None:
    """Use object and field identity; never match unrelated same-name fields."""
    tables: dict[str, dict[str, str] | None] = {}
    for declaration in walk_owned(root, boundaries=frozenset({"function_definition"})):
        if declaration.type != "declaration":
            continue
        static = any(
            child.type == "storage_class_specifier" and node_text(child) == "static"
            for child in declaration.named_children
        )
        constant = any(
            child.type == "type_qualifier" and node_text(child) == "const"
            for child in declaration.named_children
        )
        for init in declaration.children_by_field_name("declarator"):
            name = init.child_by_field_name("declarator")
            if name is None or name.type != "identifier":
                continue
            ident = node_text(name)
            value = init.child_by_field_name("value")
            if ident in tables or not static or not constant or _conditional(declaration):
                tables[ident] = None
            elif value is not None and value.type == "initializer_list":
                tables[ident] = _fields(value, module, graph)
    calls = {
        (node.start_point.row + 1, node.start_point.column): node
        for node in walk_owned(root)
        if node.type == "call_expression"
    }
    edges = []
    for edge in graph.edges:
        if edge.kind != EdgeKind.CALLS or edge.location is None:
            edges.append(edge)
            continue
        occurrence = calls.get((edge.location.start_line, edge.location.start_col or 0))
        function = occurrence.child_by_field_name("function") if occurrence is not None else None
        if occurrence is None or function is None or function.type != "field_expression":
            edges.append(edge)
            continue
        receiver = function.child_by_field_name("argument")
        field = function.child_by_field_name("field")
        if (
            receiver is None
            or receiver.type != "identifier"
            or field is None
            or not any(child.type == "." for child in function.children)
        ):
            edges.append(edge)
            continue
        name = node_text(receiver)
        source = graph.nodes[edge.source_id]
        shadowed = (
            dict(source.attributes).get("parse_incomplete")
            or any(param.name == name for param in source.parameters)
            or lexical_declaration(
                occurrence,
                name,
                scopes=frozenset({"function_definition", "compound_statement"}),
                declarations=(("declaration", "declarator"), ("init_declarator", "declarator")),
            )
        )
        table = tables.get(name)
        target = table.get(node_text(field)) if table is not None and not shadowed else None
        if target is not None:
            edge = replace(
                edge,
                target_id=target,
                confidence=EdgeConfidence.INFERRED,
                attributes=(
                    *edge.attributes,
                    ("callback_table", name),
                    ("callback_field", node_text(field)),
                ),
            )
        edges.append(edge)
    graph.edges[:] = edges


def _fields(value: Node, module: str, graph: CodeGraph) -> dict[str, str]:
    fields: dict[str, str] = {}
    seen: set[str] = set()
    for pair in value.named_children:
        designators = pair.children_by_field_name("designator")
        if len(designators) != 1 or designators[0].type != "field_designator":
            continue
        name = node_text(designators[0]).removeprefix(".").strip()
        if name in seen:
            fields.pop(name, None)
            continue
        seen.add(name)
        target = pair.child_by_field_name("value")
        if target is not None and target.type == "pointer_expression":
            if not any(child.type == "&" for child in target.children):
                continue
            target = target.child_by_field_name("argument")
        if target is None or target.type != "identifier":
            continue
        node_id = f"{module}:{node_text(target)}"
        unit = graph.nodes.get(node_id)
        if unit is not None and unit.kind == NodeKind.FUNCTION:
            fields[name] = node_id
    return fields


def _conditional(node: Node) -> bool:
    parent = node.parent
    while parent is not None:
        if parent.type.startswith("preproc_"):
            return True
        parent = parent.parent
    return False
