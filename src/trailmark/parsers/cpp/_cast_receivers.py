"""Navigate explicit reference casts to the enclosing class's declared methods."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace

from tree_sitter import Node

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import Attribute, NodeKind
from trailmark.parsers._bindings import namespace_receiver_matches
from trailmark.parsers._common import node_text


def class_attributes(node: Node) -> tuple[Attribute, ...]:
    body = node.child_by_field_name("body")
    if body is None:
        return ()
    metadata: tuple[Attribute, ...] = (
        ("cpp_cast_complete", True),
        (
            "cpp_cast_generic",
            node.parent is not None and node.parent.type == "template_declaration",
        ),
        ("cpp_cast_has_bases", any(c.type == "base_class_clause" for c in node.named_children)),
    )
    names, virtual = [], []
    pending = list(body.named_children)
    while pending:
        declaration = pending.pop()
        if declaration.type in {
            "preproc_if",
            "preproc_ifdef",
            "preproc_else",
            "preproc_elif",
            "declaration_list",
            "template_declaration",
        }:
            pending.extend(declaration.named_children)
            continue
        if declaration.type in {"using_declaration", "preproc_include", "preproc_call", "ERROR"}:
            return (*metadata, ("cpp_cast_member_imports", True))
        for outer in declaration.children_by_field_name("declarator"):
            declarator = outer
            while declarator.type in {
                "pointer_declarator",
                "reference_declarator",
                "attributed_declarator",
            }:
                nested = declarator.child_by_field_name("declarator")
                if nested is None:
                    break
                declarator = nested
            if declarator.type != "function_declarator":
                continue
            name = declarator.child_by_field_name("declarator")
            if name is None or name.type not in {"field_identifier", "identifier"}:
                continue
            names.append(node_text(name))
            if any(c.type == "virtual" for c in declaration.children) or any(
                c.type == "virtual_specifier" for c in declarator.children
            ):
                virtual.append(node_text(name))
    return (
        *metadata,
        ("cpp_cast_declared_methods", json.dumps(names)),
        ("cpp_cast_virtual_methods", json.dumps(virtual)),
    )


def call_attributes(edge: CodeEdge, call: Node, graph: CodeGraph) -> tuple[Attribute, ...]:
    field = call.child_by_field_name("function")
    if (
        field is None
        or field.type != "field_expression"
        or not any(c.type == "." for c in field.children)
    ):
        return ()
    receiver, method = field.child_by_field_name("argument"), field.child_by_field_name("field")
    if (
        receiver is None
        or receiver.type != "call_expression"
        or method is None
        or method.type != "field_identifier"
    ):
        return ()
    function = receiver.child_by_field_name("function")
    if function is None or function.type != "template_function":
        return ()
    keyword, arguments = (
        function.child_by_field_name("name"),
        function.child_by_field_name("arguments"),
    )
    if (
        keyword is None
        or node_text(keyword) != "static_cast"
        or arguments is None
        or len(arguments.named_children) != 1
    ):
        return ()
    descriptor = arguments.named_children[0]
    type_node, reference = (
        descriptor.child_by_field_name("type"),
        descriptor.child_by_field_name("declarator"),
    )
    if (
        descriptor.type != "type_descriptor"
        or type_node is None
        or type_node.type != "type_identifier"
        or reference is None
        or reference.type != "abstract_reference_declarator"
        or node_text(reference) != "&"
        or len(descriptor.named_children) != 2
        or receiver.has_error
    ):
        return ()
    name = node_text(type_node)
    target = edge.source_id.split(":", 1)[0] + ":" + name
    if not namespace_receiver_matches(edge, call, graph, target):
        return ()
    # Local type aliases/classes and template parameters can hide the injected class name.
    ancestor = call.parent
    while ancestor is not None:
        if ancestor.type == "template_parameter_list":
            return ()
        if ancestor.type == "template_declaration":
            parameters = ancestor.child_by_field_name("parameters")
            if parameters is not None and any(
                node_text(binding) == name
                for c in parameters.named_children
                if (binding := c.child_by_field_name("name")) is not None
            ):
                return ()
        ancestor = ancestor.parent
    scope = call.parent
    while scope is not None and scope.type not in {"function_definition", "lambda_expression"}:
        scope = scope.parent
    pending = [scope] if scope is not None else []
    while pending:
        node = pending.pop()
        if node.type in {
            "alias_declaration",
            "class_specifier",
            "struct_specifier",
            "enum_specifier",
            "type_parameter_declaration",
            "variadic_type_parameter_declaration",
        }:
            binding = node.child_by_field_name("name")
            if binding is not None and node_text(binding) == name:
                return ()
        if node.type == "type_definition" and any(
            node_text(c) == name for c in node.named_children
        ):
            return ()
        pending.extend(node.named_children)
    return (("cpp_cast_type", name), ("cpp_cast_method", node_text(method)))


def bind_cast_calls(graph: CodeGraph) -> None:
    owners = {e.target_id: e.source_id for e in graph.edges if e.kind == EdgeKind.CONTAINS}
    methods: dict[tuple[str, str], list[str]] = {}
    for unit in graph.nodes.values():
        if unit.kind == NodeKind.METHOD:
            methods.setdefault((owners.get(unit.id, ""), unit.name), []).append(unit.id)

    edges = []
    for edge in graph.edges:
        attrs = dict(edge.attributes)
        name, method = attrs.get("cpp_cast_type"), attrs.get("cpp_cast_method")
        if edge.kind != EdgeKind.CALLS or not isinstance(name, str) or not isinstance(method, str):
            edges.append(edge)
            continue
        attrs.pop("cpp_cast_dispatch_uncertain", None)
        original = attrs.setdefault("cpp_cast_original", edge.target_id)
        if not isinstance(original, str):
            edges.append(edge)
            continue
        edge = replace(
            edge,
            target_id=original,
            confidence=EdgeConfidence.UNCERTAIN,
            attributes=tuple(attrs.items()),
        )
        owner = graph.nodes.get(owners.get(edge.source_id, ""))
        if (
            owner is None
            or owner.kind not in {NodeKind.CLASS, NodeKind.STRUCT}
            or owner.name != name
            or dict(owner.attributes).get("cpp_cast_generic")
            or attrs.get("binding_blocked")
        ):
            edges.append(edge)
            continue
        metadata = dict(owner.attributes)
        counts = Counter(json.loads(str(metadata.get("cpp_cast_declared_methods", "[]"))))
        candidates = methods.get((owner.id, method), [])
        if (
            counts[method] == 1
            and len(candidates) == 1
            and not metadata.get("cpp_cast_member_imports")
            and not graph.nodes[candidates[0]].type_parameters
            and metadata.get("cpp_cast_complete")
        ):
            dispatch_unknown = metadata.get("cpp_cast_has_bases") or method in json.loads(
                str(metadata.get("cpp_cast_virtual_methods", "[]"))
            )
            if dispatch_unknown:
                attrs["cpp_cast_dispatch_uncertain"] = True
            edge = replace(
                edge,
                target_id=candidates[0],
                confidence=EdgeConfidence.UNCERTAIN
                if dispatch_unknown
                else EdgeConfidence.INFERRED,
                attributes=tuple(attrs.items()),
            )
        edges.append(edge)
    graph.edges[:] = edges
