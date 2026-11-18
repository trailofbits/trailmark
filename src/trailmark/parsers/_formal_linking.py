"""Link only unique, visible source declarations; retain source evidence roles."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit, NodeKind, NodeOrigin


class _Symbols:
    def __init__(self, graph: CodeGraph) -> None:
        self.graph = graph
        self.files = {
            n.location.file_path: n.id
            for n in graph.nodes.values()
            if n.kind == NodeKind.MODULE and n.origin == NodeOrigin.SOURCE
        }
        self.modules: dict[str, set[str]] = {}
        self.names: dict[str, list[CodeUnit]] = {}
        self.exports: dict[str, set[str]] = {}
        self.ordered = {
            unit.location.file_path
            for unit in graph.nodes.values()
            if dict(unit.attributes).get("ordered_declarations")
        }
        for unit in graph.nodes.values():
            attrs = dict(unit.attributes)
            for name in json.loads(str(attrs.get("formal_modules", "[]"))):
                self.modules.setdefault(name, set()).add(unit.id)
            for name, imports in json.loads(str(attrs.get("formal_exports", "{}"))).items():
                self.exports.setdefault(name, set()).update(imports)
            qualified = attrs.get("qualified_name")
            if isinstance(qualified, str):
                self.names.setdefault(qualified, []).append(unit)
            for alias in json.loads(str(attrs.get("qualified_aliases", "[]"))):
                self.names.setdefault(alias, []).append(unit)
        self.imports = self.link_imports()
        self.visible = {module: self.reachable(module) for module in self.files.values()}

    def link_imports(self) -> dict[str, set[str]]:
        imports: dict[str, set[str]] = {module: set() for module in self.files.values()}
        edges = []
        for edge in self.graph.edges:
            name = dict(edge.attributes).get("import_name")
            targets = self.modules.get(str(name), set()) if edge.kind == EdgeKind.IMPORTS else set()
            if targets:
                edges.extend(replace(edge, target_id=target) for target in sorted(targets))
                imports[edge.source_id].update(targets)
            else:
                edges.append(edge)
        self.graph.edges = edges
        return imports

    def reachable(self, module: str) -> set[str]:
        reached, pending = {module}, [module]
        while pending:
            for target in self.imports.get(pending.pop(), set()) - reached:
                reached.add(target)
                pending.append(target)
        return reached

    def resolve(self, edge: CodeEdge, tiers: list[list[str]]) -> str | None:
        source = self.graph.nodes[edge.source_id]
        source_file = source.location.file_path
        visible = self.visible[self.files[source_file]]
        for tier in tiers:
            found = [
                unit
                for name in self.expanded(tier)
                for unit in self.names.get(name, [])
                if self.files[unit.location.file_path] in visible
                and self.available(unit, edge, source_file)
                and (
                    unit.location.file_path == source_file
                    or not dict(unit.attributes).get("private")
                )
            ]
            local = [unit for unit in found if unit.location.file_path == source_file]
            if local:
                found = local
                definitions = [n for n in found if not dict(n.attributes).get("signature")]
                found = definitions or found
            else:
                signatures = [n for n in found if dict(n.attributes).get("signature")]
                found = signatures or found
            targets = {unit.id for unit in found}
            if targets:
                return next(iter(targets)) if len(targets) == 1 else None
        return None

    def expanded(self, names: list[str]) -> set[str]:
        result: set[str] = set()
        pending = list(names)
        while pending:
            name = pending.pop()
            if name in result:
                continue
            result.add(name)
            namespace, _, member = name.rpartition(".")
            pending.extend(f"{parent}.{member}" for parent in self.exports.get(namespace, ()))
        return result

    def available(self, unit: CodeUnit, edge: CodeEdge, source_file: str) -> bool:
        if unit.location.file_path != source_file or source_file not in self.ordered:
            return True
        if edge.location is None:
            return False
        return (unit.location.start_line, unit.location.start_col or 0) <= (
            edge.location.start_line,
            edge.location.start_col or 0,
        )

    def correspondence(self) -> list[CodeEdge]:
        result = []
        for units in self.names.values():
            signatures = [u for u in units if dict(u.attributes).get("signature")]
            definitions = [u for u in units if not dict(u.attributes).get("signature")]
            if len(definitions) == 1:
                result.extend(
                    CodeEdge(
                        signature.id,
                        definitions[0].id,
                        EdgeKind.CORRESPONDS_TO,
                        EdgeConfidence.INFERRED,
                        signature.location,
                    )
                    for signature in signatures
                )
        return result


@dataclass(frozen=True)
class _Linked:
    edge: CodeEdge
    application: bool = False
    position: int | None = None
    enclosing: tuple[int | None, ...] = ()


def _link(edge: CodeEdge, symbols: _Symbols) -> _Linked | None:
    attrs = dict(edge.attributes)
    raw = attrs.pop("_formal_candidates", None)
    application = attrs.pop("_formal_application", False)
    position = attrs.pop("_formal_position", None)
    enclosing = json.loads(str(attrs.pop("_formal_enclosing", "[]")))
    if not isinstance(raw, str):
        return _Linked(edge)
    target = symbols.resolve(edge, json.loads(raw))
    if target is None:
        return None
    linked = replace(
        edge,
        target_id=target,
        attributes=tuple(attrs.items()),
        confidence=EdgeConfidence.INFERRED,
    )
    return _Linked(
        linked,
        application is True,
        position if isinstance(position, int) else None,
        tuple(enclosing),
    )


def _computational(graph: CodeGraph, ident: str) -> bool:
    return dict(graph.nodes[ident].attributes).get("executable") is True


def _runtime(reference: _Linked, callees: dict[tuple[str, int | None], bool]) -> bool:
    context = dict(reference.edge.attributes).get("reference_context")
    if context == "body":
        return True
    return (
        context == "argument"
        and bool(reference.enclosing)
        and all(
            callees.get((reference.edge.source_id, position), False)
            for position in reference.enclosing
        )
    )


def link_symbols(graph: CodeGraph) -> None:
    symbols = _Symbols(graph)
    references = [linked for edge in graph.edges if (linked := _link(edge, symbols)) is not None]
    callees = {
        (reference.edge.source_id, reference.position): _computational(
            graph, reference.edge.target_id
        )
        for reference in references
        if reference.application
    }
    result = []
    for reference in references:
        edge = reference.edge
        result.append(edge)
        if (
            reference.application
            and _runtime(reference, callees)
            and _computational(graph, edge.source_id)
            and _computational(graph, edge.target_id)
        ):
            result.append(replace(edge, kind=EdgeKind.CALLS))
    graph.edges = list(dict.fromkeys([*result, *symbols.correspondence()]))
    targets = {e.target_id for e in graph.edges}
    for ident in list(graph.nodes):
        if ident.startswith("@import:") and ident not in targets:
            del graph.nodes[ident]
