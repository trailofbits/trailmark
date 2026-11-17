"""Finalize Lean lookups within inspected modules and declaration order."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import TypedDict

from tree_sitter import Node

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import Attribute, CodeUnit, NodeKind

_CALLABLES = {NodeKind.FUNCTION, NodeKind.METHOD}
_RECORDS = {NodeKind.STRUCT, NodeKind.TRAIT}
_TYPES = _RECORDS | {NodeKind.ENUM}
_PENDING = {"lean_candidates", "lean_receiver_candidates", "lean_field"}


class _Lookup(TypedDict):
    tiers: list[list[str]]
    atomic: bool
    position: list[int]
    group: str | None


def candidates(
    node: Node, name: str, *, namespace: str, opens: tuple[str, ...], group: str | None
) -> str:
    tiers: list[list[str]] = []
    rooted = name.startswith("_root_.")
    if rooted:
        tiers = [[name.removeprefix("_root_.")]]
    else:
        while namespace:
            tiers.append([f"{namespace}.{name}"])
            namespace = namespace.rpartition(".")[0]
        tiers.append([name])
        opened = [f"{opened}.{name}" for opened in opens]
        if "." in name:
            tiers.append(opened)
        else:
            tiers[-1].extend(opened)
    return json.dumps(
        {
            "tiers": tiers,
            "atomic": not rooted and "." not in name,
            "position": [node.start_point.row + 1, node.start_point.column],
            "group": group,
        }
    )


def _visible_modules(graph: CodeGraph) -> dict[str, set[str]]:
    modules = {n.id for n in graph.nodes.values() if n.kind == NodeKind.MODULE}
    imports: dict[str, set[str]] = {module: set() for module in modules}
    for edge in graph.edges:
        if (
            edge.kind == EdgeKind.IMPORTS
            and edge.source_id in modules
            and edge.target_id in modules
        ):
            imports[edge.source_id].add(edge.target_id)
    visible = {}
    for module in modules:
        reached, pending = {module}, [module]
        while pending:
            for target in imports[pending.pop()] - reached:
                reached.add(target)
                pending.append(target)
        visible[module] = reached
    return visible


class _Symbols:
    def __init__(self, graph: CodeGraph) -> None:
        self.graph = graph
        self.visible = _visible_modules(graph)
        self.file_modules = {
            n.location.file_path: n.id for n in graph.nodes.values() if n.kind == NodeKind.MODULE
        }
        self.symbols: dict[str, list[CodeUnit]] = {}
        for unit in graph.nodes.values():
            qualified = dict(unit.attributes).get("lean_qualified_name")
            if isinstance(qualified, str):
                self.symbols.setdefault(qualified, []).append(unit)

    def resolve(self, raw: str, source: CodeUnit) -> str | None:
        lookup: _Lookup = json.loads(raw)
        for tier in lookup["tiers"]:
            found = {
                unit.id
                for name in tier
                for unit in self.symbols.get(name, [])
                if self.available(unit, source, lookup)
            }
            if found:
                return next(iter(found)) if len(found) == 1 else None
        return None

    def available(self, unit: CodeUnit, source: CodeUnit, lookup: _Lookup) -> bool:
        attributes = dict(unit.attributes)
        same_file = unit.location.file_path == source.location.file_path
        if (
            self.file_modules[unit.location.file_path]
            not in self.visible[self.file_modules[source.location.file_path]]
        ):
            return False
        if attributes.get("lean_private") and not same_file:
            return False
        if lookup["atomic"] and attributes.get("lean_protected") and unit.id != source.id:
            return False
        if same_file and not (
            lookup["group"] and lookup["group"] == attributes.get("_lean_mutual_group")
        ):
            position = (unit.location.start_line, unit.location.start_col or 0)
            return position <= tuple(lookup["position"])
        return True

    def target(self, edge: CodeEdge) -> str | None:
        attributes = dict(edge.attributes)
        raw = attributes.get("lean_candidates")
        receiver = attributes.get("lean_receiver_candidates")
        source = self.graph.nodes[edge.source_id]
        if isinstance(raw, str):
            return self.resolve(raw, source)
        if isinstance(receiver, str):
            return self.projection(receiver, str(attributes["lean_field"]), source)
        return edge.target_id if edge.target_id in self.graph.nodes else None

    def projection(self, receiver: str, name: str, source: CodeUnit) -> str | None:
        type_id = self.resolve(receiver, source)
        if type_id is None or self.graph.nodes[type_id].kind not in _RECORDS:
            return None
        field_id = f"{type_id}.{name}"
        unit = self.graph.nodes.get(field_id)
        if unit is not None and dict(unit.attributes).get("lean_declaration") == "field":
            return field_id
        return None

    def link(self, edge: CodeEdge) -> CodeEdge | None:
        if edge.kind not in {EdgeKind.CALLS, EdgeKind.REFERENCES, EdgeKind.TYPE_USES}:
            return edge
        attributes = dict(edge.attributes)
        if edge.kind == EdgeKind.CALLS and not attributes.get("lean_candidates"):
            return edge
        target = self.target(edge)
        clean = tuple((k, v) for k, v in edge.attributes if k not in _PENDING)
        if edge.kind == EdgeKind.CALLS:
            return self.call(edge, target, clean)
        if target is None:
            return None
        if edge.kind == EdgeKind.TYPE_USES and self.graph.nodes[target].kind not in _TYPES:
            return None
        return replace(edge, target_id=target, confidence=EdgeConfidence.INFERRED, attributes=clean)

    def call(
        self, edge: CodeEdge, target: str | None, attributes: tuple[Attribute, ...]
    ) -> CodeEdge:
        if target is not None and self.graph.nodes[target].kind in _CALLABLES:
            return replace(
                edge, target_id=target, confidence=EdgeConfidence.INFERRED, attributes=attributes
            )
        return replace(
            edge,
            attributes=(
                *attributes,
                ("binding_blocked", True),
                ("binding_reason", "lean_unresolved"),
            ),
        )


def link_symbols(graph: CodeGraph) -> None:
    index = _Symbols(graph)
    edges = []
    type_uses = set()
    for edge in graph.edges:
        linked = index.link(edge)
        if linked is None:
            continue
        if linked.kind == EdgeKind.TYPE_USES:
            key = (linked.source_id, linked.target_id)
            if key in type_uses:
                continue
            type_uses.add(key)
        edges.append(linked)
    graph.edges = edges
    for ident, unit in graph.nodes.items():
        graph.nodes[ident] = replace(
            unit, attributes=tuple((k, v) for k, v in unit.attributes if k != "_lean_mutual_group")
        )
