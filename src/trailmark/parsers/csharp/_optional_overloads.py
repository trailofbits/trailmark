"""Recognize the optional-default tie between equivalent provided signatures."""

from __future__ import annotations

import json
from dataclasses import replace

from tree_sitter import Node

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import Attribute, CodeUnit, NodeKind
from trailmark.parsers._common import node_text
from trailmark.parsers.csharp._static_calls import link_static_calls

_TYPES = {NodeKind.CLASS, NodeKind.STRUCT, NodeKind.INTERFACE}
_OBJECT_METHODS = {
    "Equals",
    "ReferenceEquals",
    "GetHashCode",
    "GetType",
    "ToString",
    "Finalize",
    "MemberwiseClone",
}


def signature_attributes(node: Node) -> tuple[Attribute, ...]:
    if (
        node.type != "method_declaration"
        or node.has_error
        or node.child_by_field_name("type_parameters")
        or any(c.type == "explicit_interface_specifier" for c in node.children)
    ):
        return ()
    if any(
        c.type == "modifier"
        and node_text(c) in {"override", "virtual", "partial", "extern", "abstract"}
        for c in node.children
    ):
        return ()
    parameters = node.child_by_field_name("parameters")
    if parameters is None or any(
        c.type not in {"(", ")", ",", "parameter", "comment"} for c in parameters.children
    ):
        return ()
    signature = []
    optional_seen = False
    for parameter in parameters.named_children:
        if parameter.type == "comment":
            continue
        name, type_node = (
            parameter.child_by_field_name("name"),
            parameter.child_by_field_name("type"),
        )
        if (
            name is None
            or type_node is None
            or any(
                c.type in {"modifier", "attribute_list", "ref", "out", "in", "params", "this"}
                for c in parameter.children
            )
        ):
            return ()
        optional = any(c.type == "=" for c in parameter.children)
        if optional_seen and not optional:
            return ()
        optional_seen |= optional
        signature.append([node_text(type_node), optional])
    context = node.parent
    while context is not None and context.type not in {"class_declaration", "struct_declaration"}:
        context = context.parent
    if context is None:
        return ()
    return (
        (
            "csharp_optional_signature",
            json.dumps({"parameters": signature, "context": context.start_byte}),
        ),
    )


def base_alias_attributes(node: Node) -> tuple[Attribute, ...]:
    """A using alias can redirect an apparently inspected interface base."""
    scope = node.parent
    while scope is not None:
        if scope.type in {
            "compilation_unit",
            "namespace_declaration",
            "file_scoped_namespace_declaration",
        }:
            body = scope.child_by_field_name("body") or scope
            if any(
                c.type == "using_directive" and c.child_by_field_name("name") is not None
                for c in body.named_children
            ):
                return (("csharp_optional_base_alias", True),)
        scope = scope.parent
    return ()


def argument_attributes(call: Node) -> tuple[Attribute, ...]:
    function, arguments = (
        call.child_by_field_name("function"),
        call.child_by_field_name("arguments"),
    )
    if call.has_error or function is None or function.type != "identifier" or arguments is None:
        return ()
    values = [c for c in arguments.named_children if c.type != "comment"]
    if any(
        value.type != "argument"
        or any(c.type in {":", "ref", "out", "in", "name_colon"} for c in value.children)
        for value in values
    ):
        return ()
    return (("csharp_positional_count", len(values)),)


def resolve_optional_overloads(graph: CodeGraph) -> None:
    owners = {e.target_id: e.source_id for e in graph.edges if e.kind == EdgeKind.CONTAINS}
    paths: dict[str, str] = {}

    def path(key: str) -> str:
        if key not in paths:
            unit = graph.nodes[key]
            parent = owners.get(key, "")
            prefix = (
                path(parent)
                if parent in graph.nodes and graph.nodes[parent].kind != NodeKind.MODULE
                else ""
            )
            paths[key] = ".".join(p for p in (prefix, unit.name) if p)
        return paths[key]

    symbols: dict[str, list[CodeUnit]] = {}
    declarations: dict[tuple[str, str], list[CodeUnit]] = {}
    global_alias = False
    for unit in graph.nodes.values():
        if unit.kind in _TYPES | {NodeKind.NAMESPACE}:
            symbols.setdefault(path(unit.id), []).append(unit)
        if unit.kind in {NodeKind.METHOD, NodeKind.FUNCTION}:
            declarations.setdefault((owners.get(unit.id, ""), unit.name), []).append(unit)
        global_alias |= bool(
            json.loads(str(dict(unit.attributes).get("csharp_global_aliases", "[]")))
        )

    def complete_class(owner: str) -> list[CodeUnit]:
        family = symbols.get(path(owner), [])
        if not family or any(
            u.kind not in {NodeKind.CLASS, NodeKind.STRUCT} or u.type_parameters for u in family
        ):
            return []
        if len(family) > 1 and (
            len({u.kind for u in family}) != 1
            or not all(dict(u.attributes).get("csharp_partial") for u in family)
        ):
            return []
        for unit in family:
            namespace_owner = owners.get(unit.id, "")
            while (
                namespace_owner in graph.nodes
                and graph.nodes[namespace_owner].kind != NodeKind.NAMESPACE
            ):
                namespace_owner = owners.get(namespace_owner, "")
            namespace = path(namespace_owner) if namespace_owner in graph.nodes else ""
            bases = json.loads(str(dict(unit.attributes).get("csharp_bases", "[]")))
            if bases and (global_alias or dict(unit.attributes).get("csharp_optional_base_alias")):
                return []
            for base in bases:
                if not all(p.isidentifier() for p in base.split(".")):
                    return []
                # Only directly identifiable interfaces exclude unseen base-class overloads.
                candidates = symbols.get(".".join(p for p in (namespace, base) if p), [])
                if not candidates or any(c.kind != NodeKind.INTERFACE for c in candidates):
                    return []
        return family

    edges = []
    for edge in graph.edges:
        attrs = dict(edge.attributes)
        original = attrs.get("csharp_optional_original")
        if isinstance(original, str):
            # Directory merging can reveal an incomplete family after a per-file selection.
            edge = replace(edge, target_id=original, confidence=EdgeConfidence.UNCERTAIN)
        name, count = attrs.get("bare_name"), attrs.get("csharp_positional_count")
        if (
            edge.kind != EdgeKind.CALLS
            or not isinstance(name, str)
            or not isinstance(count, int)
            or name in _OBJECT_METHODS
            or attrs.get("binding_blocked")
        ):
            edges.append(edge)
            continue
        owner = edge.source_id
        while owner in graph.nodes:
            if declarations.get((owner, name)) or graph.nodes[owner].kind in _TYPES:
                break
            owner = owners.get(owner, "")
        if owner not in graph.nodes or graph.nodes[owner].kind not in {
            NodeKind.CLASS,
            NodeKind.STRUCT,
        }:
            edges.append(edge)
            continue
        family = complete_class(owner)
        candidates = [m for unit in family for m in declarations.get((unit.id, name), [])]
        # Identical type spellings need the same declaration/import context.
        if len(candidates) < 2 or len({m.location.file_path for m in candidates}) != 1:
            edges.append(edge)
            continue
        original = attrs.setdefault("csharp_optional_original", edge.target_id)
        if not isinstance(original, str):
            edges.append(edge)
            continue
        edge = replace(
            edge,
            target_id=original,
            confidence=EdgeConfidence.UNCERTAIN,
            attributes=tuple(attrs.items()),
        )
        raw = [dict(m.attributes).get("csharp_optional_signature") for m in candidates]
        if any(not isinstance(s, str) for s in raw) or any(m.type_parameters for m in candidates):
            edges.append(edge)
            continue
        parsed = [json.loads(str(s)) for s in raw]
        if len({s["context"] for s in parsed}) != 1:
            edges.append(edge)
            continue
        signatures = [s["parameters"] for s in parsed]
        applicable = [
            (m, s)
            for m, s in zip(candidates, signatures, strict=True)
            if count <= len(s) and all(p[1] for p in s[count:])
        ]
        exact = [(m, s) for m, s in applicable if len(s) == count]
        if (
            len(exact) == 1
            and len(applicable) == len(candidates)
            and all([p[0] for p in s[:count]] == [p[0] for p in exact[0][1]] for _, s in applicable)
            and len({bool(dict(m.attributes).get("csharp_static")) for m in candidates}) == 1
        ):
            edge = replace(edge, target_id=exact[0][0].id, confidence=EdgeConfidence.INFERRED)
        edges.append(edge)
    graph.edges[:] = edges


def link_calls(graph: CodeGraph) -> None:
    resolve_optional_overloads(graph)
    link_static_calls(graph)
