"""Link literal self receivers within an inspected Cairo impl family."""

import json
from dataclasses import replace

from tree_sitter import Node

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.parsers._common import node_text


def _canonical(node: Node) -> str:
    parts = []
    pending = [node]
    while pending:
        child = pending.pop()
        if child.type == "comment":
            continue
        if child.children:
            pending.extend(reversed(child.children))
        else:
            parts.append(node_text(child))
    return "".join(parts)


def impl_parameters(node: Node) -> str | None:
    body = next((c for c in node.named_children if c.type == "block"), None)
    if body is not None and any(
        c.type in {"type_item", "use_declaration"} for c in body.named_children
    ):
        return None
    parameters = next((c for c in node.named_children if c.type == "type_parameters"), None)
    if parameters is None:
        return "[]"
    if parameters.has_error:
        return None
    return json.dumps([_canonical(c) for c in parameters.named_children if c.type != "comment"])


def self_parameter(node: Node) -> tuple[tuple[str, str | bool], ...]:
    """Keep only concrete, nongeneric method signatures with a literal self."""
    if node.type != "function_definition":
        return ()
    if any(c.has_error for c in node.named_children if c.type != "block"):
        return (("cairo_self_unknown", True),)
    for parameter in node.named_children:
        if parameter.type != "parameter" or not any(c.type == "self" for c in parameter.children):
            continue
        types = [c for c in parameter.named_children if c.type not in {"self", "comment"}]
        if len(types) != 1 or parameter.has_error:
            return (("cairo_self_unknown", True),)
        type_node = types[0]
        mode = "ref" if any(c.type == "ref" for c in parameter.children) else "value"
        if type_node.type == "at_type":
            nested = [c for c in type_node.named_children if c.type != "comment"]
            if len(nested) != 1:
                return (("cairo_self_unknown", True),)
            type_node, mode = nested[0], "snapshot"
        if type_node.type not in {"type_identifier", "scoped_type_identifier", "generic_type"}:
            return (("cairo_self_unknown", True),)
        return (
            ("cairo_self_type", _canonical(type_node)),
            ("cairo_self_mode", mode),
            ("cairo_self_generic", any(c.type == "type_parameters" for c in node.named_children)),
        )
    return ()


def self_call(edge: CodeEdge, node: Node, graph: CodeGraph) -> CodeEdge:
    function = node.child_by_field_name("function")
    if function is None or function.type != "field_expression":
        return edge
    receiver = function.child_by_field_name("value")
    selector = function.child_by_field_name("field")
    if receiver is None or receiver.type != "self" or selector is None:
        return edge
    ancestor = node.parent
    while ancestor is not None and ancestor.type != "function_definition":
        ancestor = ancestor.parent
    location = graph.nodes[edge.source_id].location
    if (
        ancestor is None
        or location is None
        or (
            ancestor.start_point.row + 1,
            ancestor.start_point.column,
        )
        != (location.start_line, location.start_col)
    ):
        return edge
    return replace(
        edge,
        confidence=EdgeConfidence.UNCERTAIN,
        attributes=(
            ("cairo_self_selector", node_text(selector)),
            (
                "cairo_self_arity",
                None
                if node.has_error or any(c.type == ":" for c in node.children)
                else len([c for c in node.named_children if c.type != "comment"]) - 1,
            ),
            ("binding_blocked", True),
        ),
    )


def bind_self_calls(graph: CodeGraph) -> None:
    owners = {e.target_id: e.source_id for e in graph.edges if e.kind == EdgeKind.CONTAINS}
    families: dict[tuple[str, str], list[str]] = {}
    for unit in graph.nodes.values():
        owner = owners.get(unit.id, "")
        impl = graph.nodes.get(owner)
        if impl is not None and dict(impl.attributes).get("cairo_impl"):
            families.setdefault((owners.get(owner, ""), unit.name), []).append(unit.id)
    edges = []
    for edge in graph.edges:
        edge_attrs = dict(edge.attributes)
        selector, arity = edge_attrs.get("cairo_self_selector"), edge_attrs.get("cairo_self_arity")
        source = graph.nodes.get(edge.source_id)
        impl = graph.nodes.get(owners.get(edge.source_id, ""))
        if (
            edge.kind != EdgeKind.CALLS
            or not isinstance(selector, str)
            or source is None
            or impl is None
        ):
            edges.append(edge)
            continue
        source_attrs, impl_attrs = dict(source.attributes), dict(impl.attributes)
        signature = impl_attrs.get("cairo_impl_parameters")
        self_type = source_attrs.get("cairo_self_type")
        candidates = []
        blocked = False
        if (
            isinstance(signature, str)
            and isinstance(self_type, str)
            and isinstance(arity, int)
            and impl_attrs.get("cairo_impl_count") == 1
            and source_attrs.get("cairo_definition_count") == 1
            and not source_attrs.get("cairo_self_generic")
        ):
            for ident in families.get((owners.get(impl.id, ""), selector), []):
                target = graph.nodes[ident]
                target_attrs = dict(target.attributes)
                target_impl = dict(graph.nodes[owners[ident]].attributes)
                if target_attrs.get("cairo_self_unknown"):
                    blocked = True
                    continue
                if len(target.parameters) != arity:
                    continue
                if target_attrs.get("cairo_self_type") != self_type:
                    # Distinct spellings can denote the same aliased type.
                    if "cairo_self_type" in target_attrs:
                        blocked = True
                    continue
                if (
                    target_impl.get("cairo_impl_parameters") != signature
                    or target_impl.get("cairo_impl_count") != 1
                    or target_attrs.get("cairo_definition_count") != 1
                    or target_attrs.get("cairo_self_generic")
                ):
                    blocked = True
                    continue
                if target_attrs.get("cairo_self_mode") == "snapshot" or target_attrs.get(
                    "cairo_self_mode"
                ) == source_attrs.get("cairo_self_mode"):
                    candidates.append(ident)
        if len(candidates) == 1 and not blocked:
            attrs = dict(edge.attributes)
            attrs["binding_blocked"] = False
            edge = replace(
                edge,
                target_id=candidates[0],
                confidence=EdgeConfidence.INFERRED,
                attributes=tuple(attrs.items()),
            )
        edges.append(edge)
    graph.edges[:] = edges
