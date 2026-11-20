"""Finalize Lean lookups within inspected modules and declaration order."""

from __future__ import annotations

from dataclasses import replace

from tree_sitter import Node

from trailmark.models._parsing import Lookup
from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import Attribute, CodeUnit, NodeKind
from trailmark.parsers._resolution import SymbolIndex, module_visibility

_CALLABLES = {NodeKind.FUNCTION, NodeKind.METHOD}
_RECORDS = {NodeKind.STRUCT, NodeKind.TRAIT}
_TYPES = _RECORDS | {NodeKind.ENUM}


def candidates(
    node: Node, name: str, *, namespace: str, opens: tuple[str, ...], group: str | None
) -> Lookup:
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
    return Lookup(
        tuple(map(tuple, tiers)),
        (node.start_point.row + 1, node.start_point.column),
        atomic=not rooted and "." not in name,
        group=group,
    )


class _Symbols:
    def __init__(self, graph: CodeGraph) -> None:
        self.graph = graph
        self.index = SymbolIndex(graph, "lean_qualified_name")
        self.file_modules = self.index.files
        modules = set(self.file_modules.values())
        imports: dict[str, set[str]] = {module: set() for module in modules}
        for edge in graph.edges:
            if (
                edge.kind == EdgeKind.IMPORTS
                and edge.source_id in modules
                and edge.target_id in modules
            ):
                imports[edge.source_id].add(edge.target_id)
        self.visible = module_visibility(imports)

    def resolve(self, lookup: Lookup, source: CodeUnit) -> str | None:
        _, target = self.index.resolve(
            lookup.tiers,
            lambda units: (unit for _, unit in units if self.available(unit, source, lookup)),
        )
        return target

    def available(self, unit: CodeUnit, source: CodeUnit, lookup: Lookup) -> bool:
        attributes = dict(unit.attributes)
        same_file = unit.location.file_path == source.location.file_path
        if (
            self.file_modules[unit.location.file_path]
            not in self.visible[self.file_modules[source.location.file_path]]
        ):
            return False
        if attributes.get("lean_private") and not same_file:
            return False
        if lookup.atomic and attributes.get("lean_protected") and unit.id != source.id:
            return False
        if same_file and not (
            lookup.group and lookup.group == attributes.get("_lean_mutual_group")
        ):
            position = (unit.location.start_line, unit.location.start_col or 0)
            return position <= lookup.position
        return True

    def target(self, edge: CodeEdge) -> str | None:
        pending = self.graph._parse_state.reference(edge)
        source = self.graph.nodes[edge.source_id]
        if pending is not None:
            if pending.lookup is not None:
                return self.resolve(pending.lookup, source)
            if pending.receiver is not None:
                return self.projection(pending.receiver, pending.field or "", source)
        return edge.target_id if edge.target_id in self.graph.nodes else None

    def projection(self, receiver: Lookup, name: str, source: CodeUnit) -> str | None:
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
        pending = self.graph._parse_state.reference(edge)
        if edge.kind == EdgeKind.CALLS and (pending is None or pending.lookup is None):
            return edge
        target = self.target(edge)
        clean = tuple((k, v) for k, v in edge.attributes if k != "_lookup")
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
    graph._parse_state.clear_references()
