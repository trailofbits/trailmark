"""Validate, retain, and conditionally summarize claims without inferring source semantics."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from enum import Enum
from typing import TYPE_CHECKING

from trailmark.models.constraints import (
    ArgumentRef,
    CallSite,
    ConstraintBoolean,
    ConstraintClaim,
    ConstraintExpr,
    ConstraintPredicate,
    ConstraintSummary,
    ParameterRef,
)
from trailmark.models.edges import CodeEdge, EdgeKind
from trailmark.models.nodes import NodeKind

if TYPE_CHECKING:
    from trailmark.models.graph import CodeGraph


def graph_fingerprint(graph: CodeGraph) -> str:
    """Bind claims to sources, selection, bindings, signatures, and operand evidence."""
    payload = {
        "sources": graph.source_digests,
        "policy": graph.source_policy,
        "entrypoints": {key: asdict(tag) for key, tag in graph.entrypoints.items()},
        "diagnostics": [asdict(item) for item in graph.parse_diagnostics],
        "nodes": {key: asdict(unit) for key, unit in graph.nodes.items()},
        "edges": [asdict(edge) for edge in graph.edges],
        "sites": {key: asdict(site) for key, site in graph.call_sites.items()},
        "parameters": {
            key: [asdict(slot) for slot in profile]
            for key, profile in graph.parameter_bindings.items()
        },
    }

    def encode(value: object) -> object:
        if isinstance(value, Enum):
            return value.value
        raise TypeError(f"Unsupported constraint context: {type(value).__name__}")

    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=encode).encode()).hexdigest()


def site_edges(graph: CodeGraph, site: CallSite) -> list[CodeEdge]:
    return [
        edge
        for edge in graph.edges
        if edge.kind == EdgeKind.CALLS
        and edge.source_id == site.caller_id
        and edge.location == site.location
    ]


def validate_claim(
    graph: CodeGraph, caller_id: str, claim: ConstraintClaim, *, require_current: bool
) -> None:
    site = graph.call_sites.get(claim.call_site_id)
    if site is None or site.caller_id != caller_id or caller_id not in graph.nodes:
        raise ValueError("Constraint call site does not belong to its annotation owner")
    if site.execution != "call":
        raise ValueError("Deferred or unknown execution cannot carry an invocation constraint")
    pending = [(claim.predicate, 0)]
    while pending:
        expression, depth = pending.pop()
        if depth > 128:
            raise ValueError("Constraint expression nesting exceeds 128")
        if isinstance(expression, ConstraintBoolean):
            pending.extend((term, depth + 1) for term in expression.terms)
            continue
        subject = expression.subject
        if not isinstance(subject, ArgumentRef) or (
            subject.receiver
            and site.receiver is None
            or not subject.receiver
            and (subject.index is None or subject.index >= len(site.arguments))
        ):
            raise ValueError("Constraint subject is not an observed operand at this call site")
    for evidence in claim.evidence:
        span = evidence.location
        if (
            not span.file_path
            or span.start_line < 1
            or span.end_line < span.start_line
            or graph.source_digests.get(span.file_path) != evidence.source_sha256
        ):
            raise ValueError("Constraint evidence has an invalid span or unknown source digest")
    if require_current and claim.graph_fingerprint != graph_fingerprint(graph):
        raise ValueError("Constraint claim refers to a different graph context")


def current_claim(graph: CodeGraph, claim: ConstraintClaim, fingerprint: str | None = None) -> bool:
    return claim.graph_fingerprint == (fingerprint or graph_fingerprint(graph)) and all(
        graph.source_digests.get(item.location.file_path) == item.source_sha256
        for item in claim.evidence
    )


def _combine(operation: str, terms: list[ConstraintExpr]) -> ConstraintExpr:
    if operation == "and":
        if any(term == ConstraintBoolean("false") for term in terms):
            return ConstraintBoolean("false")
        terms = [term for term in terms if term != ConstraintBoolean("true")]
    else:
        if any(term == ConstraintBoolean("true") for term in terms):
            return ConstraintBoolean("true")
        terms = [term for term in terms if term != ConstraintBoolean("false")]
    if not terms:
        return ConstraintBoolean("true" if operation == "and" else "false")
    if len(terms) == 1:
        return terms[0]
    return ConstraintBoolean("and" if operation == "and" else "or", tuple(terms))


def _project(
    expression: ConstraintExpr, argument: int, parameter: int
) -> tuple[ConstraintExpr, bool]:
    if isinstance(expression, ConstraintPredicate):
        subject = expression.subject
        if isinstance(subject, ArgumentRef) and not subject.receiver and subject.index == argument:
            return replace(expression, subject=ParameterRef(parameter, subject.slices)), True
        return ConstraintBoolean("true"), False
    if expression.operation in {"true", "false"}:
        return expression, True
    terms = [_project(term, argument, parameter) for term in expression.terms]
    precise = all(known for _, known in terms)
    if expression.operation == "not":
        return (
            (ConstraintBoolean("not", (terms[0][0],)), True)
            if precise
            else (ConstraintBoolean("true"), False)
        )
    return _combine(expression.operation, [term for term, _ in terms]), precise


def _bound_argument(
    graph: CodeGraph, node_id: str, site: CallSite, parameter: int, assumptions: tuple[str, ...]
) -> int | None:
    profile = graph.parameter_bindings.get(node_id)
    if (
        profile is not None
        and parameter < len(profile)
        and profile[parameter].kind in {"var_positional", "var_keyword"}
    ):
        return None
    prefix = f"argument_mapping:{site.id}:{parameter}="
    mappings = [value[len(prefix) :] for value in assumptions if value.startswith(prefix)]
    if mappings:
        if len(mappings) != 1:
            return None
        try:
            index = int(mappings[0])
        except ValueError:
            return None
        return (
            index
            if str(index) == mappings[0]
            and 0 <= index < len(site.arguments)
            and (site.arguments[index].kind not in {"unpacked", "implicit"})
            else None
        )
    profile = graph.parameter_bindings.get(node_id)
    if (
        profile is None
        or parameter >= len(profile)
        or graph.nodes[node_id].kind == NodeKind.METHOD
        or any(argument.kind in {"unpacked", "implicit"} for argument in site.arguments)
    ):
        return None
    slot = profile[parameter]
    if (
        not slot.value_preserving
        and f"identity_parameter_value:{site.id}:{parameter}" not in assumptions
    ):
        return None
    if slot.kind in {"unknown", "var_positional", "var_keyword"}:
        return None
    named = [argument.index for argument in site.arguments if argument.name == slot.name]
    positional_slots = [
        index
        for index, binding in enumerate(profile)
        if binding.kind in {"positional_only", "positional_or_keyword"}
    ]
    positional_arguments = [
        argument.index for argument in site.arguments if argument.kind == "positional"
    ]
    ordinal = positional_slots.index(parameter) if parameter in positional_slots else None
    positional = (
        positional_arguments[ordinal]
        if ordinal is not None and ordinal < len(positional_arguments)
        else None
    )
    if len(named) > 1 or (named and (positional is not None or slot.kind == "positional_only")):
        return None
    return named[0] if named else positional


def summarize(
    graph: CodeGraph, node_id: str, parameter: int, assumptions: tuple[str, ...]
) -> ConstraintSummary:
    unit = graph.nodes[node_id]
    if type(parameter) is not int or not 0 <= parameter < len(unit.parameters):
        raise ValueError("Unknown callee parameter index")
    if (
        not isinstance(assumptions, tuple)
        or any(not isinstance(value, str) or not value.strip() for value in assumptions)
        or len(set(assumptions)) != len(assumptions)
    ):
        raise ValueError("Summary assumptions must be distinct nonempty names")
    fingerprint = graph_fingerprint(graph)
    gaps = set()
    profiles = set()
    alternatives: list[ConstraintExpr] = []
    sites = []
    incoming = [
        edge for edge in graph.edges if edge.kind == EdgeKind.CALLS and edge.target_id == node_id
    ]
    by_occurrence = {(edge.source_id, edge.location) for edge in incoming}
    indexed = {(site.caller_id, site.location): site for site in graph.call_sites.values()}
    for occurrence in sorted(by_occurrence, key=repr):
        site = indexed.get(occurrence)
        if (
            site is None
            or site.execution != "call"
            or set(site.gaps)
            - {"parameter_mapping_unverified", "unpacked_argument_mapping_unknown"}
        ):
            alternatives.append(ConstraintBoolean("true"))
            gaps.add("incoming_call_evidence_incomplete")
            continue
        sites.append(site.id)
        argument = _bound_argument(graph, node_id, site, parameter, assumptions)
        if argument is None:
            alternatives.append(ConstraintBoolean("true"))
            gaps.add("parameter_binding_unknown")
            profile = graph.parameter_bindings.get(node_id)
            if profile is not None and not profile[parameter].value_preserving:
                gaps.add("parameter_value_conversion_unknown")
            continue
        facts: list[ConstraintExpr] = []
        local_profiles = set()
        for annotation in graph.annotations.get(site.caller_id, []):
            claim = annotation.constraint
            if claim is None or claim.call_site_id != site.id:
                continue
            if not current_claim(graph, claim, fingerprint):
                gaps.add("stale_constraint")
                continue
            if not set(claim.assumptions) <= set(assumptions):
                gaps.add("constraint_assumptions_unsatisfied")
                continue
            if (
                claim.justification == "asserted"
                and "accept_asserted_constraints" not in assumptions
            ):
                gaps.add("asserted_constraint_not_accepted")
                continue
            predicate, _ = _project(claim.predicate, argument, parameter)
            facts.append(predicate)
            local_profiles.add(claim.semantics)
        profiles.update(local_profiles)
        path_predicate = _combine("and", facts)
        if path_predicate == ConstraintBoolean("true"):
            gaps.add("unconstrained_incoming_argument")
        alternatives.append(path_predicate)
    if len(profiles) > 1:
        gaps.add("conflicting_semantics_profiles")
        alternatives.append(ConstraintBoolean("true"))
    if "complete_callers" not in assumptions:
        gaps.add("caller_completeness_unverified")
        alternatives.append(ConstraintBoolean("true"))
    if node_id in graph.entrypoints and f"exclude_external_entrypoint:{node_id}" not in assumptions:
        gaps.add("known_external_entrypoint")
        alternatives.append(ConstraintBoolean("true"))
    if not incoming:
        gaps.add("no_observed_incoming_calls")
        alternatives.append(ConstraintBoolean("true"))
    predicate = _combine("or", alternatives)
    return ConstraintSummary(
        node_id,
        parameter,
        predicate,
        assumptions,
        tuple(sorted(gaps)),
        tuple(sites),
        tuple(sorted(profiles)),
        "unknown" if predicate == ConstraintBoolean("true") else "conditional",
        fingerprint,
    )
