"""Bind modifier applications within the declared inheritance hierarchy."""

from dataclasses import replace

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.parsers._common import link_cross_file_calls


def link_calls(graph: CodeGraph) -> None:
    resolve_modifier_calls(graph)
    link_cross_file_calls(graph)


def resolve_modifier_calls(graph: CodeGraph) -> None:
    owners = {
        edge.target_id: edge.source_id for edge in graph.edges if edge.kind == EdgeKind.CONTAINS
    }
    bases: dict[str, list[str]] = {}
    for edge in graph.edges:
        if edge.kind == EdgeKind.INHERITS:
            bases.setdefault(edge.source_id, []).append(edge.target_id)
    modifiers: dict[tuple[str, str], set[str]] = {}
    for unit in graph.nodes.values():
        if dict(unit.attributes).get("solidity_declaration_kind") == "modifier":
            modifiers.setdefault((owners.get(unit.id, ""), unit.name), set()).add(unit.id)
    edges = []
    for edge in graph.edges:
        attrs = dict(edge.attributes)
        name = attrs.get("modifier_name")
        if edge.kind != EdgeKind.CALLS or not isinstance(name, str):
            edges.append(edge)
            continue
        owner = owners.get(edge.source_id, "")
        qualifier, separator, member = name.rpartition(".")
        if not separator:
            member = name
        pending = [owner]
        ancestors = set()
        while pending:
            base = pending.pop()
            if base not in ancestors:
                ancestors.add(base)
                pending.extend(bases.get(base, ()))
        starts = [owner]
        if qualifier:
            starts = [
                base
                for base in ancestors
                if base in graph.nodes and graph.nodes[base].name == qualifier
            ]
        candidates: set[str] = set()
        unknown = False
        visited = set()
        pending = starts if len(starts) == 1 else []
        while pending:
            base = pending.pop()
            if base in visited:
                continue
            visited.add(base)
            local = modifiers.get((base, member), set())
            if local:
                candidates.update(local)
            elif base not in graph.nodes:
                unknown = True
            else:
                pending.extend(bases.get(base, ()))
        if len(candidates) == 1 and not unknown:
            target = next(iter(candidates))
            attrs.pop("binding_blocked", None)
            edge = replace(
                edge,
                target_id=target,
                confidence=EdgeConfidence.CERTAIN
                if owners.get(target) == owner
                else EdgeConfidence.INFERRED,
                attributes=tuple(attrs.items()),
            )
        else:
            attrs["binding_blocked"] = True
            edge = replace(
                edge,
                target_id=f"{edge.source_id}.$modifier.{name}",
                confidence=EdgeConfidence.UNCERTAIN,
                attributes=tuple(attrs.items()),
            )
        edges.append(edge)
    graph.edges[:] = edges
