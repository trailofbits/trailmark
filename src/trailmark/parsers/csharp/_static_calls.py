"""Conservative namespace/type lookup for explicitly qualified static calls."""

from __future__ import annotations

import json
from dataclasses import replace

from tree_sitter import Node

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import Attribute, CodeUnit, NodeKind
from trailmark.parsers._bindings import namespace_receiver_matches
from trailmark.parsers._common import first_child_by_type, link_cross_file_calls, node_text

_TYPES = {NodeKind.CLASS, NodeKind.STRUCT, NodeKind.INTERFACE}


def class_attributes(node: Node) -> tuple[Attribute, ...]:
    bases = first_child_by_type(node, "base_list")
    attrs: list[Attribute] = []
    if any(c.type == "modifier" and node_text(c) == "partial" for c in node.children):
        attrs.append(("csharp_partial", True))
    if bases is not None:
        attrs.append(("csharp_bases", json.dumps([node_text(c) for c in bases.named_children])))
    body = node.child_by_field_name("body")
    for child in body.named_children if body else []:
        if child.type in {"property_declaration", "event_declaration"}:
            name = child.child_by_field_name("name")
            if name is not None:
                attrs.append(("csharp_value_member:" + node_text(name), True))
        if child.type in {"field_declaration", "event_field_declaration"}:
            declaration = first_child_by_type(child, "variable_declaration")
            for variable in declaration.named_children if declaration else []:
                name = variable.child_by_field_name("name")
                if name is not None:
                    attrs.append(("csharp_value_member:" + node_text(name), True))
    return tuple(attrs)


def call_attributes(edge: CodeEdge, call: Node, graph: CodeGraph) -> tuple[Attribute, ...]:
    raw = edge.target_id
    global_root = raw.startswith("global::")
    parts = raw.removeprefix("global::").split(".")
    if len(parts) < 2 or not all(part.isidentifier() for part in parts):
        return ()
    head = parts[0]
    if not global_root:
        if _complex_value_binding(call, head):
            return ()
        probe_target = edge.source_id.split(":", 1)[0] + ":" + head
        if not namespace_receiver_matches(edge, call, graph, probe_target):
            return ()
    imports = False
    scope = call.parent
    while scope is not None:
        for child in scope.named_children:
            if (
                child.type == "type_parameter_list"
                and not global_root
                and any(
                    node_text(name) == head
                    for p in child.named_children
                    if (name := p.child_by_field_name("name")) is not None
                )
            ):
                return ()
        if scope.type in {
            "compilation_unit",
            "namespace_declaration",
            "file_scoped_namespace_declaration",
        }:
            body = scope.child_by_field_name("body") or scope
            for child in body.named_children:
                if child.type == "using_directive":
                    name = child.child_by_field_name("name")
                    if name is None:
                        imports = True
                    elif node_text(name) == head and not global_root:
                        return ()
        scope = scope.parent
    return (
        ("csharp_static_type", ".".join(parts[:-1])),
        ("csharp_static_method", parts[-1]),
        ("csharp_static_global", global_root),
        ("csharp_static_imports", imports),
    )


def _complex_value_binding(call: Node, name: str) -> bool:
    """Keep binding forms outside the shared lexical policy conservative."""
    scope = call
    ancestor = call.parent
    while ancestor is not None:
        if ancestor.type in {
            "method_declaration",
            "constructor_declaration",
            "local_function_statement",
        }:
            scope = ancestor
        ancestor = ancestor.parent
    pending = [scope]
    while pending:
        node = pending.pop()
        if (
            node.type
            in {
                "catch_declaration",
                "for_each_statement",
                "foreach_statement",
                "foreach_variable_statement",
                "declaration_expression",
                "from_clause",
                "join_clause",
                "let_clause",
                "query_continuation",
                "parameter_list",
                "implicit_parameters",
            }
            or node.type.endswith("_pattern")
            or "designation" in node.type
        ):
            nested = list(node.named_children)
            while nested:
                child = nested.pop()
                if child.type == "identifier" and node_text(child) == name:
                    return True
                nested.extend(child.named_children)
        pending.extend(node.named_children)
    return False


def record_global_usings(root: Node, module: str, graph: CodeGraph) -> None:
    aliases, imports = [], False
    for child in root.named_children:
        if child.type != "using_directive" or not node_text(child).lstrip().startswith("global "):
            continue
        name = child.child_by_field_name("name")
        if name is None:
            imports = True
        else:
            aliases.append(node_text(name))
    if aliases or imports:
        unit = graph.nodes[module]
        graph.nodes[module] = replace(
            unit,
            attributes=(
                *unit.attributes,
                ("csharp_global_aliases", json.dumps(aliases)),
                ("csharp_global_imports", imports),
            ),
        )


def bind_static_calls(graph: CodeGraph) -> None:
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
    methods: dict[tuple[str, str], list[CodeUnit]] = {}
    global_aliases: set[str] = set()
    global_imports = False
    for unit in graph.nodes.values():
        if unit.kind in _TYPES | {NodeKind.NAMESPACE}:
            symbols.setdefault(path(unit.id), []).append(unit)
        if unit.kind == NodeKind.METHOD:
            methods.setdefault((owners.get(unit.id, ""), unit.name), []).append(unit)
        attrs = dict(unit.attributes)
        global_aliases.update(json.loads(str(attrs.get("csharp_global_aliases", "[]"))))
        global_imports |= bool(attrs.get("csharp_global_imports"))

    def lookup(name: str, scope: str, minimum: int = 0) -> list[CodeUnit]:
        parts = scope.split(".") if scope else []
        for depth in range(len(parts), minimum - 1, -1):
            prefix = ".".join(parts[:depth])
            head = ".".join(p for p in (prefix, name.split(".", 1)[0]) if p)
            if head in symbols:
                return symbols.get(".".join(p for p in (prefix, name) if p), [])
        return []

    def cluster(units: list[CodeUnit]) -> list[CodeUnit]:
        if not units or any(u.kind not in _TYPES or u.type_parameters for u in units):
            return []
        if len(units) > 1 and (
            len({u.kind for u in units}) > 1
            or not all(dict(u.attributes).get("csharp_partial") for u in units)
        ):
            return []
        return units

    edges = []
    for edge in graph.edges:
        attrs = dict(edge.attributes)
        qualifier, method = attrs.get("csharp_static_type"), attrs.get("csharp_static_method")
        if (
            edge.kind != EdgeKind.CALLS
            or not isinstance(qualifier, str)
            or not isinstance(method, str)
        ):
            edges.append(edge)
            continue
        original = attrs.setdefault("csharp_static_original", edge.target_id)
        if not isinstance(original, str):
            edges.append(edge)
            continue
        edge = replace(edge, target_id=original, attributes=tuple(attrs.items()))
        global_root = bool(attrs.get("csharp_static_global"))
        head = qualifier.split(".", 1)[0]
        owner = owners.get(edge.source_id, "")
        scopes = []
        while owner in graph.nodes:
            if graph.nodes[owner].kind in _TYPES | {NodeKind.NAMESPACE}:
                scopes.append(graph.nodes[owner])
            owner = owners.get(owner, "")
        scope = path(scopes[0].id) if scopes else ""
        namespace = next((path(u.id) for u in scopes if u.kind == NodeKind.NAMESPACE), "")
        minimum = (
            len(namespace.split("."))
            if namespace and (attrs.get("csharp_static_imports") or global_imports)
            else 0
        )
        blocked = not global_root and head in global_aliases
        if not global_root:
            for enclosing in (u for u in scopes if u.kind in _TYPES):
                family = cluster(symbols.get(path(enclosing.id), []))
                if not family or any(
                    dict(u.attributes).get("csharp_value_member:" + head) for u in family
                ):
                    blocked = True
                for unit in family:
                    for base in json.loads(str(dict(unit.attributes).get("csharp_bases", "[]"))):
                        base_units = (
                            cluster(lookup(base, path(unit.id), minimum))
                            if all(p.isidentifier() for p in base.split("."))
                            else []
                        )
                        if not base_units or any(u.kind != NodeKind.INTERFACE for u in base_units):
                            blocked = True
        targets = cluster(
            lookup(qualifier, "" if global_root else scope, 0 if global_root else minimum)
        )
        candidates = [m for unit in targets for m in methods.get((unit.id, method), [])]
        if (
            not blocked
            and len(candidates) == 1
            and dict(candidates[0].attributes).get("csharp_static")
        ):
            edge = replace(edge, target_id=candidates[0].id, confidence=EdgeConfidence.INFERRED)
        edges.append(edge)
    graph.edges[:] = edges


def link_static_calls(graph: CodeGraph) -> None:
    bind_static_calls(graph)
    link_cross_file_calls(graph)
