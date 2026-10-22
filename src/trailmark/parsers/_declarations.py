"""Preserve callable declarations and bind only unambiguous local methods.

A plain name is not a declaration identity in languages with overloads. Keep
historical IDs for types and unique callables, and attach source positions to
overloads or callables sharing a type's name. This does not resolve overloads.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit, NodeKind


@dataclass
class _Declarations:
    graph: CodeGraph
    ids: dict[str, list[str]] = field(default_factory=dict)
    moved: dict[str, _MovedCallable] = field(default_factory=dict)


@dataclass(frozen=True)
class _MovedCallable:
    node_id: str
    edge_count: int


_CURRENT: ContextVar[_Declarations | None] = ContextVar("_DECLARATIONS", default=None)


@contextmanager
def callable_scope(graph: CodeGraph) -> Generator[None]:
    """Collect a file's declarations, then repair references to renamed bodies."""
    declarations = _Declarations(graph)
    token = _CURRENT.set(declarations)
    try:
        yield
        _repair_body_edges(declarations)
        owners = {
            edge.target_id: edge.source_id for edge in graph.edges if edge.kind == EdgeKind.CONTAINS
        }
        graph.edges[:] = [
            _repair_call_target(edge, declarations, owners) if edge.kind == EdgeKind.CALLS else edge
            for edge in graph.edges
        ]
    finally:
        _CURRENT.reset(token)


def _repair_body_edges(declarations: _Declarations) -> None:
    edges = []
    for index, edge in enumerate(declarations.graph.edges):
        source_id, target_id = edge.source_id, edge.target_id
        source = declarations.moved.get(source_id)
        target = declarations.moved.get(target_id)
        # A type inserted later may reuse the callable's old base ID. Only
        # edges recorded before the move belong to that callable's body.
        if source is not None and index < source.edge_count:
            source_id = source.node_id
        if edge.kind == EdgeKind.CONTAINS and target is not None and index < target.edge_count:
            target_id = target.node_id
        edges.append(replace(edge, source_id=source_id, target_id=target_id))
    declarations.graph.edges[:] = edges


def _callable_base(
    edge: CodeEdge,
    declarations: _Declarations,
    owners: dict[str, str],
) -> str | None:
    if edge.target_id in declarations.ids:
        return edge.target_id
    for base, ids in declarations.ids.items():
        if edge.target_id in ids:
            return base
    # Kotlin and Swift emit bare names. Check the caller's own declarations
    # before module functions, without inferring receiver or argument types.
    if any(separator in edge.target_id for separator in (":", ".", "->")):
        return None
    owner = owners.get(edge.source_id)
    if owner is not None:
        for separator in (".", ":"):
            base = f"{owner}{separator}{edge.target_id}"
            if base in declarations.ids:
                return base
    module = edge.source_id.split(":", 1)[0]
    base = f"{module}:{edge.target_id}"
    return base if base in declarations.ids else None


def _repair_call_target(
    edge: CodeEdge, declarations: _Declarations, owners: dict[str, str]
) -> CodeEdge:
    if dict(edge.attributes).get("binding_blocked"):
        return edge
    base = _callable_base(edge, declarations, owners)
    if base is None:
        return edge
    ids = declarations.ids[base]
    same_named_type = declarations.graph.nodes.get(base)
    constructor_ambiguity = (
        declarations.graph.language == "kotlin"
        and same_named_type is not None
        and same_named_type.kind == NodeKind.CLASS
    )
    if len(ids) == 1 and not constructor_ambiguity:
        # A same-named type owns the base ID; calls use the callable namespace.
        return replace(edge, target_id=ids[0]) if ids[0] != base else edge
    # Kotlin constructors share call syntax with factories, but the frontend
    # does not materialize their candidates. A single factory is not sufficient
    # evidence to distinguish construction from a call to that factory.
    target_id = edge.target_id
    if target_id in declarations.graph.nodes:
        # The base may now name a type. Keep unresolved overload calls from
        # becoming calls to that type merely because its ID exists.
        target_id = f"{base}@overload"
    return replace(
        edge,
        target_id=target_id,
        confidence=EdgeConfidence.UNCERTAIN,
        attributes=(*edge.attributes, ("binding_blocked", True))
        if constructor_ambiguity
        else edge.attributes,
    )


def _positioned_id(unit: CodeUnit, base: str) -> str:
    return f"{base}@{unit.location.start_line}_{unit.location.start_col or 0}"


def _current_declarations(graph: CodeGraph) -> _Declarations:
    declarations = _CURRENT.get()
    if declarations is None or declarations.graph is not graph:
        raise RuntimeError("Declaration insertion requires a callable_scope for this graph")
    return declarations


def _move_callable(declarations: _Declarations, base: str) -> None:
    graph = declarations.graph
    first = graph.nodes[base]
    first_id = _positioned_id(first, base)
    if first_id in graph.nodes:
        raise ValueError(f"Declaration ID collision: {first_id!r}")
    graph.nodes.pop(base)
    graph.nodes[first_id] = replace(first, id=first_id)
    declarations.moved[base] = _MovedCallable(first_id, len(graph.edges))
    declarations.ids[base][0] = first_id


def add_type(graph: CodeGraph, unit: CodeUnit) -> None:
    """Reserve a type's historical ID without overwriting a same-named callable."""
    declarations = _current_declarations(graph)
    if declarations.ids.get(unit.id) == [unit.id]:
        _move_callable(declarations, unit.id)
    graph.nodes[unit.id] = unit


def add_callable(graph: CodeGraph, unit: CodeUnit) -> str:
    """Insert a callable without overwriting any earlier overload's body."""
    declarations = _current_declarations(graph)
    base = unit.id
    ids = declarations.ids.setdefault(base, [])
    if not ids and base not in graph.nodes:
        graph.nodes[base] = unit
        ids.append(base)
        return base
    if ids == [base]:
        _move_callable(declarations, base)
    node_id = _positioned_id(unit, base)
    if node_id in graph.nodes:
        raise ValueError(f"Declaration ID collision: {node_id!r}")
    graph.nodes[node_id] = replace(unit, id=node_id)
    ids.append(node_id)
    return node_id


def resolve_local_method_calls(graph: CodeGraph) -> None:
    """Prefer Solidity/C++ methods in the caller's container over free functions.

    A name with multiple local declarations stays unresolved. Receiver-qualified
    calls and inherited methods require language semantics beyond this pass.
    """
    owners = {
        edge.target_id: edge.source_id for edge in graph.edges if edge.kind == EdgeKind.CONTAINS
    }
    methods: dict[tuple[str, str], list[str]] = {}
    for unit in graph.nodes.values():
        if unit.kind == NodeKind.METHOD and unit.id in owners:
            methods.setdefault((owners[unit.id], unit.name), []).append(unit.id)
    edges = []
    for edge in graph.edges:
        owner = owners.get(edge.source_id)
        module = edge.source_id.split(":", 1)[0]
        prefix = module + ":"
        if (
            edge.kind == EdgeKind.CALLS
            and owner is not None
            and edge.target_id.startswith(prefix)
            and not dict(edge.attributes).get("binding_blocked")
        ):
            name = edge.target_id[len(prefix) :]
            candidates = methods.get((owner, name), [])
            if len(candidates) == 1:
                edge = replace(edge, target_id=candidates[0])
            elif candidates:
                edge = replace(
                    edge, target_id=f"{owner}.{name}", confidence=EdgeConfidence.UNCERTAIN
                )
        edges.append(edge)
    graph.edges[:] = edges


def bind_lexical_calls(graph: CodeGraph) -> None:
    """Bind explicitly marked bare calls within their nearest extracted owner."""
    owners = {e.target_id: e.source_id for e in graph.edges if e.kind == EdgeKind.CONTAINS}
    declarations: dict[tuple[str, str], list[str]] = {}
    for unit in graph.nodes.values():
        if unit.kind in {NodeKind.FUNCTION, NodeKind.METHOD} and unit.id in owners:
            declarations.setdefault((owners[unit.id], unit.name), []).append(unit.id)
    edges = []
    for edge in graph.edges:
        attributes = dict(edge.attributes)
        name = attributes.get("bare_name")
        if (
            edge.kind == EdgeKind.CALLS
            and isinstance(name, str)
            and not attributes.get("binding_blocked")
        ):
            owner = edge.source_id
            seen = set()
            while owner and owner not in seen:
                seen.add(owner)
                candidates = declarations.get((owner, name), [])
                if candidates:
                    edge = replace(
                        edge,
                        target_id=candidates[0]
                        if len(candidates) == 1
                        else f"{owner}.{name}@overload",
                        confidence=EdgeConfidence.CERTAIN
                        if len(candidates) == 1
                        else EdgeConfidence.UNCERTAIN,
                    )
                    break
                owner = owners.get(owner, "")
        edges.append(edge)
    graph.edges[:] = edges
