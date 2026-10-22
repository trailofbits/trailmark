"""Conservative method binding through resolved Solidity inheritance edges."""

from __future__ import annotations

from dataclasses import replace

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit, NodeKind, TypeRef

type _Signature = tuple[TypeRef | None, ...]


def _ancestors(owner: str, bases: dict[str, list[str]]) -> set[str]:
    """Walk each base once, terminating even on malformed inheritance cycles."""
    seen = {owner}
    stack = list(bases.get(owner, ()))
    while stack:
        base = stack.pop()
        if base in seen:
            continue
        seen.add(base)
        stack.extend(bases.get(base, ()))
    return seen - {owner}


def _inherited_candidates(
    owner: str,
    name: str,
    ancestors: set[str],
    bases: dict[str, list[str]],
    inherited_methods: dict[tuple[str, str], list[CodeUnit]],
    local: list[CodeUnit],
) -> set[str]:
    """Keep nearest declarations per signature and branch, including local ones.

    Different signatures remain candidates: we do not infer argument types.
    Multiple base branches remain ambiguous without compiler linearization.
    """
    local_signatures = {
        tuple(parameter.type_ref for parameter in unit.parameters) for unit in local
    }
    signatures: set[_Signature] = {
        tuple(parameter.type_ref for parameter in method.parameters)
        for base in ancestors
        for method in inherited_methods.get((base, name), ())
    }
    candidates: set[str] = {unit.id for unit in local}
    for signature in signatures - local_signatures:
        seen = {owner}
        stack = list(bases.get(owner, ()))
        while stack:
            base = stack.pop()
            if base in seen:
                continue
            seen.add(base)
            declarations = {
                method.id
                for method in inherited_methods.get((base, name), ())
                if tuple(parameter.type_ref for parameter in method.parameters) == signature
            }
            if declarations:
                candidates.update(declarations)
            else:
                stack.extend(bases.get(base, ()))
    return candidates


def resolve_inherited_method_calls(graph: CodeGraph) -> None:
    """Bind bare Solidity calls to a unique internally callable method.

    Run after local method and type relationship binding, before free-function
    fallback. Revisit earlier bindings as directory parsing discovers more bases.
    This resolves call reachability, not storage effects or argument types.
    """
    if graph.language != "solidity":
        return
    owners: dict[str, str] = {}
    bases: dict[str, list[str]] = {}
    for edge in graph.edges:
        if edge.kind == EdgeKind.CONTAINS:
            owners[edge.target_id] = edge.source_id
        elif edge.kind == EdgeKind.INHERITS:
            bases.setdefault(edge.source_id, []).append(edge.target_id)
    methods: dict[tuple[str, str], list[CodeUnit]] = {}
    for unit in graph.nodes.values():
        owner = owners.get(unit.id)
        if unit.kind == NodeKind.METHOD and owner is not None:
            methods.setdefault((owner, unit.name), []).append(unit)
    states = {
        (owners.get(unit.id), unit.name)
        for unit in graph.nodes.values()
        if unit.kind == NodeKind.STATE_VARIABLE
    }
    inherited_methods = {
        key: [
            unit
            for unit in units
            if dict(unit.attributes).get("solidity_visibility") not in {"private", "external"}
        ]
        for key, units in methods.items()
    }
    ancestors: dict[str, set[str]] = {}
    unknown_bases: dict[str, bool] = {}
    cache: dict[tuple[str, str], set[str]] = {}
    edges = []
    for edge in graph.edges:
        owner = owners.get(edge.source_id)
        if (
            edge.kind != EdgeKind.CALLS
            or owner not in bases
            or dict(edge.attributes).get("binding_blocked")
        ):
            edges.append(edge)
            continue
        if owner not in ancestors:
            ancestors[owner] = _ancestors(owner, bases)
            unknown_bases[owner] = any(base not in graph.nodes for base in ancestors[owner])
        prefix = edge.source_id.split(":", 1)[0] + ":"
        scoped_prefix = owner + "."
        target = graph.nodes.get(edge.target_id)
        name = ""
        # Local/prior inheritance binding emits module-qualified method IDs.
        # Raw receiver expressions (this.f, super.f) have no module colon.
        if (
            target is not None
            and target.kind == NodeKind.METHOD
            and owners.get(target.id) in ancestors[owner] | {owner}
        ):
            name = target.name
        elif edge.target_id.startswith(scoped_prefix):
            name = edge.target_id[len(scoped_prefix) :].removesuffix("@overload")
        elif edge.target_id.startswith(prefix):
            name = edge.target_id[len(prefix) :]
        if name and not any(separator in name for separator in (":", ".", "->", "@")):
            if any((base, name) in states for base in ancestors[owner] | {owner}):
                edges.append(
                    replace(
                        edge,
                        target_id=f"{edge.source_id}.$binding.{name}",
                        confidence=EdgeConfidence.UNCERTAIN,
                        attributes=(
                            *edge.attributes,
                            ("binding_blocked", True),
                            ("binding_reason", "inherited_state_value"),
                        ),
                    )
                )
                continue
            key = (owner, name)
            if key not in cache:
                cache[key] = _inherited_candidates(
                    owner, name, ancestors[owner], bases, inherited_methods, methods.get(key, [])
                )
            candidates = cache[key]
            unknown = unknown_bases[owner]
            if len(candidates) == 1:
                candidate = next(iter(candidates))
                edge = replace(
                    edge,
                    target_id=candidate,
                    confidence=(
                        EdgeConfidence.CERTAIN
                        if owners.get(candidate) == owner and not unknown
                        else EdgeConfidence.INFERRED
                    ),
                )
            elif candidates:
                edge = replace(
                    edge,
                    target_id=f"{owner}.{name}@overload",
                    confidence=EdgeConfidence.UNCERTAIN,
                )
            elif edge.target_id == f"{scoped_prefix}{name}@overload":
                # A standalone file could not inspect a remote base. Once that
                # base is known to have no eligible method, restore free-call
                # fallback instead of retaining its provisional ambiguity.
                edge = replace(edge, target_id=prefix + name, confidence=EdgeConfidence.CERTAIN)
            if unknown:
                resolved = graph.nodes.get(edge.target_id)
                if resolved is not None and resolved.kind in {NodeKind.FUNCTION, NodeKind.METHOD}:
                    edge = replace(edge, confidence=EdgeConfidence.INFERRED)
            elif (
                target is not None
                and target.kind == NodeKind.FUNCTION
                and edge.target_id == prefix + name
            ):
                edge = replace(edge, confidence=EdgeConfidence.CERTAIN)
        edges.append(edge)
    graph.edges[:] = edges
