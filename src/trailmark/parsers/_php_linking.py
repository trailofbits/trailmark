"""Bind PHP free functions by namespace, retaining global fallback semantics."""

from dataclasses import replace

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind


def link_php_functions(graph: CodeGraph) -> None:
    owners = {e.target_id: e.source_id for e in graph.edges if e.kind == EdgeKind.CONTAINS}
    symbols: dict[str, list[str]] = {}
    for unit in graph.nodes.values():
        owner = graph.nodes.get(owners.get(unit.id, ""))
        if unit.kind == NodeKind.FUNCTION and (
            owner is None or owner.kind in {NodeKind.MODULE, NodeKind.NAMESPACE}
        ):
            symbols.setdefault(unit.id.split(":", 1)[-1], []).append(unit.id)
    graph.edges[:] = [_bind(edge, graph, symbols) for edge in graph.edges]


def _bind(edge: CodeEdge, graph: CodeGraph, symbols: dict[str, list[str]]) -> CodeEdge:
    attributes = dict(edge.attributes)
    symbol = attributes.get("php_function_target")
    if edge.kind != EdgeKind.CALLS or not isinstance(symbol, str):
        return edge
    candidates = symbols.get(symbol, [])
    fallback = symbol.rsplit(".", 1)[-1]
    if not candidates and attributes.get("php_global_fallback"):
        candidates = symbols.get(fallback, [])
        symbol = fallback
    if len(candidates) == 1:
        source = graph.nodes[edge.source_id]
        target = graph.nodes[candidates[0]]
        confidence = (
            EdgeConfidence.CERTAIN
            if source.location.file_path == target.location.file_path
            else EdgeConfidence.INFERRED
        )
        return replace(edge, target_id=target.id, confidence=confidence)
    module = edge.source_id.split(":", 1)[0]
    return replace(
        edge,
        target_id=f"{module}:{symbol}",
        confidence=EdgeConfidence.UNCERTAIN if candidates else edge.confidence,
    )
