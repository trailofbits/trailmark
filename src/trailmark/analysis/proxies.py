"""Normalize unresolved graph references into explicit proxy nodes."""

from __future__ import annotations

import dataclasses
import re

from trailmark.models.edges import CodeEdge, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit, NodeKind, NodeOrigin, SourceLocation, TypeRef

_PROXY_MODULE = "proxy"
_PROXY_FILE = "<proxy>"


def ensure_proxy_nodes(graph: CodeGraph) -> CodeGraph:
    """Create concrete proxy nodes for unresolved references.

    The function mutates and returns ``graph`` so callers can normalize a graph
    before building indexes while preserving existing call sites.
    """
    _materialize_unresolved_calls(graph)
    _materialize_type_use_edges(graph)
    return graph


def _materialize_unresolved_calls(graph: CodeGraph) -> None:
    """Rewrite dangling call targets to proxy nodes so traversals retain them."""
    new_edges: list[CodeEdge] = []
    for edge in graph.edges:
        if edge.kind != EdgeKind.CALLS or edge.target_id in graph.nodes:
            new_edges.append(edge)
            continue

        proxy_id = _proxy_id("unresolved", edge.target_id)
        _add_proxy_node(
            graph,
            proxy_id,
            edge.target_id,
            "unresolved",
            edge.location,
        )
        new_edges.append(
            dataclasses.replace(
                edge,
                target_id=proxy_id,
                attributes=edge.attributes + (("raw_target", edge.target_id),),
            )
        )

    graph.edges = new_edges


def _materialize_type_use_edges(graph: CodeGraph) -> None:
    """Add TYPE_USES edges for type refs that resolve to existing graph nodes."""
    existing = {
        (edge.source_id, edge.target_id, edge.kind)
        for edge in graph.edges
        if edge.kind == EdgeKind.TYPE_USES
    }
    type_index = _type_index(graph)
    new_edges: list[CodeEdge] = []
    for unit in graph.nodes.values():
        for type_ref in _iter_unit_types(unit):
            target_id = _resolve_type_ref(type_ref, type_index)
            if target_id is None:
                continue
            key = (unit.id, target_id, EdgeKind.TYPE_USES)
            if key in existing:
                continue
            existing.add(key)
            new_edges.append(
                CodeEdge(
                    source_id=unit.id,
                    target_id=target_id,
                    kind=EdgeKind.TYPE_USES,
                    attributes=(("type_name", type_ref.name),),
                )
            )
    graph.edges.extend(new_edges)


def _add_proxy_node(
    graph: CodeGraph,
    proxy_id: str,
    raw_symbol: str,
    proxy_kind: str,
    location: SourceLocation | None,
) -> None:
    """Insert a proxy node if it does not already exist."""
    if proxy_id in graph.nodes:
        return

    graph.nodes[proxy_id] = CodeUnit(
        id=proxy_id,
        name=raw_symbol,
        kind=NodeKind.PROXY,
        location=location or _proxy_location(graph),
        origin=NodeOrigin.PROXY,
        attributes=(
            ("raw_symbol", raw_symbol),
            ("proxy_kind", proxy_kind),
        ),
    )


def _proxy_location(graph: CodeGraph) -> SourceLocation:
    file_path = graph.root_path or _PROXY_FILE
    return SourceLocation(file_path=file_path, start_line=0, end_line=0)


def _proxy_id(kind: str, raw_symbol: str) -> str:
    return f"{_PROXY_MODULE}.{kind}:{_escape_proxy_symbol(raw_symbol)}"


def _escape_proxy_symbol(raw_symbol: str) -> str:
    text = raw_symbol.strip() or "unknown"
    text = re.sub(r"\s+", "_", text)
    return re.sub(r"[^0-9A-Za-z_.@$:-]+", "_", text)


def _type_index(graph: CodeGraph) -> dict[str, str | None]:
    index: dict[str, str | None] = {}
    for node_id, node in graph.nodes.items():
        for key in {node_id, node.name, node_id.rsplit(":", 1)[-1], node_id.rsplit(".", 1)[-1]}:
            existing = index.get(key)
            if existing is None and key in index:
                continue
            if key in index and existing != node_id:
                index[key] = None
            else:
                index[key] = node_id
    return index


def _resolve_type_ref(
    type_ref: TypeRef,
    type_index: dict[str, str | None],
) -> str | None:
    candidates = [type_ref.name]
    if type_ref.module:
        candidates.insert(0, f"{type_ref.module}.{type_ref.name}")
        candidates.insert(0, f"{type_ref.module}:{type_ref.name}")
    for candidate in candidates:
        target_id = type_index.get(candidate)
        if target_id is not None:
            return target_id
    return None


def _iter_unit_types(unit: CodeUnit) -> list[TypeRef]:
    refs: list[TypeRef] = []
    for param in unit.parameters:
        if param.type_ref is not None:
            refs.extend(_flatten_type_ref(param.type_ref))
    if unit.return_type is not None:
        refs.extend(_flatten_type_ref(unit.return_type))
    for exc in unit.exception_types:
        refs.extend(_flatten_type_ref(exc))
    for type_param in unit.type_parameters:
        for constraint in type_param.constraints:
            refs.extend(_flatten_type_ref(constraint))
        if type_param.default is not None:
            refs.extend(_flatten_type_ref(type_param.default))
    return refs


def _flatten_type_ref(type_ref: TypeRef) -> list[TypeRef]:
    refs = [type_ref]
    for arg in type_ref.generic_args:
        refs.extend(_flatten_type_ref(arg))
    return refs
