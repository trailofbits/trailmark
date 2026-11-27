"""Validate, retain, and conditionally summarize claims without inferring source semantics."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import asdict, replace
from enum import Enum
from typing import TYPE_CHECKING, Any, NamedTuple, cast

from trailmark.models.constraints import (
    ArgumentRef,
    CallSite,
    ConstraintBoolean,
    ConstraintClaim,
    ConstraintExpr,
    ConstraintPredicate,
    ConstraintSummary,
    ParameterBinding,
    ParameterRef,
)
from trailmark.models.edges import CodeEdge, EdgeKind
from trailmark.models.nodes import NodeKind

if TYPE_CHECKING:
    from trailmark.models.graph import CodeGraph


def graph_context(graph: CodeGraph) -> dict[str, object]:
    """Capture mutable containers while sharing their immutable model values."""
    return {
        "root": graph.root_path,
        "language": graph.language,
        "sources": graph.source_digests.copy(),
        "source_file_counts": graph.source_file_counts.copy(),
        "policy": deepcopy(graph.source_policy),
        "dependencies": graph.dependencies.copy(),
        "entrypoints": graph.entrypoints.copy(),
        "entrypoint_evidence": graph.entrypoint_evidence.copy(),
        "binary_artifacts": graph.binary_artifacts.copy(),
        "binary_ranges": graph.binary_ranges.copy(),
        "binary_source_mappings": graph.binary_source_mappings.copy(),
        "diagnostics": graph.parse_diagnostics.copy(),
        "nodes": graph.nodes.copy(),
        "edges": graph.edges.copy(),
        "sites": graph.call_sites.copy(),
        "parameters": graph.parameter_bindings.copy(),
    }


def _context_payload(context: dict[str, object]) -> dict[str, object]:
    """Serialize known model fields without widening the source-policy format."""
    payload = context.copy()
    for name in (
        "entrypoints",
        "entrypoint_evidence",
        "binary_artifacts",
        "binary_ranges",
        "binary_source_mappings",
        "nodes",
        "sites",
    ):
        records = cast(dict[str, Any], context[name])
        payload[name] = {key: asdict(record) for key, record in records.items()}
    for name in ("edges", "diagnostics"):
        payload[name] = [asdict(item) for item in cast(list[Any], context[name])]
    payload["parameters"] = {
        key: [asdict(slot) for slot in profile]
        for key, profile in cast(dict[str, Any], context["parameters"]).items()
    }
    return payload


def _encode_context(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Unsupported constraint context: {type(value).__name__}")


def graph_fingerprint(graph: CodeGraph, *, context: dict[str, object] | None = None) -> str:
    """Bind claims to sources, selection, bindings, signatures, and operand evidence."""
    payload = graph_context(graph) if context is None else context
    return hashlib.sha256(
        json.dumps(_context_payload(payload), sort_keys=True, default=_encode_context).encode()
    ).hexdigest()


def site_edges(graph: CodeGraph, site: CallSite) -> list[CodeEdge]:
    return [
        edge
        for edge in graph.edges
        if edge.kind == EdgeKind.CALLS
        and edge.source_id == site.caller_id
        and edge.location == site.location
    ]


def validate_claim(
    graph: CodeGraph,
    caller_id: str,
    claim: ConstraintClaim,
    *,
    require_current: bool,
    fingerprint: str | None = None,
) -> None:
    site = graph.call_sites.get(claim.call_site_id)
    if site is None or site.caller_id != caller_id or caller_id not in graph.nodes:
        raise ValueError("Constraint call site does not belong to its annotation owner")
    if site.execution != "call":
        raise ValueError("Deferred or unknown execution cannot carry an invocation constraint")
    _validate_operands(site, claim.predicate)
    if require_current:
        _validate_current_claim(graph, claim, fingerprint)


def _validate_subject(subject: ArgumentRef | ParameterRef, site: CallSite) -> None:
    if not isinstance(subject, ArgumentRef):
        raise ValueError("Constraint subject is not an observed operand at this call site")
    if subject.receiver:
        if site.receiver is None:
            raise ValueError("Constraint subject is not an observed operand at this call site")
        return
    if subject.index is None or subject.index >= len(site.arguments):
        raise ValueError("Constraint subject is not an observed operand at this call site")


def _validate_operands(site: CallSite, predicate: ConstraintExpr) -> None:
    pending = [(predicate, 0)]
    while pending:
        expression, depth = pending.pop()
        if depth > 128:
            raise ValueError("Constraint expression nesting exceeds 128")
        if isinstance(expression, ConstraintBoolean):
            pending.extend((term, depth + 1) for term in expression.terms)
            continue
        _validate_subject(expression.subject, site)


def _validate_current_claim(
    graph: CodeGraph, claim: ConstraintClaim, fingerprint: str | None
) -> None:
    for evidence in claim.evidence:
        if graph.source_digests.get(evidence.location.file_path) != evidence.source_sha256:
            raise ValueError("Constraint evidence has an unknown source digest")
    current = graph_fingerprint(graph) if fingerprint is None else fingerprint
    if claim.graph_fingerprint != current:
        raise ValueError("Constraint claim refers to a different graph context")


def current_claim(graph: CodeGraph, claim: ConstraintClaim, fingerprint: str) -> bool:
    return claim.graph_fingerprint == fingerprint and all(
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
        return (_negate(terms[0][0]), True) if precise else (ConstraintBoolean("true"), False)
    return _combine(expression.operation, [term for term, _ in terms]), precise


def _negate(expression: ConstraintExpr) -> ConstraintExpr:
    if expression == ConstraintBoolean("true"):
        return ConstraintBoolean("false")
    if expression == ConstraintBoolean("false"):
        return ConstraintBoolean("true")
    return ConstraintBoolean("not", (expression,))


def _explicit_mapping(site: CallSite, mappings: list[str]) -> int | None:
    if len(mappings) != 1:
        return None
    try:
        index = int(mappings[0])
    except ValueError:
        return None
    valid = (
        str(index) == mappings[0]
        and 0 <= index < len(site.arguments)
        and site.arguments[index].kind not in {"unpacked", "implicit"}
    )
    return index if valid else None


def _inferred_argument(
    profile: tuple[ParameterBinding, ...], site: CallSite, parameter: int
) -> int | None:
    slot = profile[parameter]
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


def _unsupported_binding(graph: CodeGraph, node_id: str, site: CallSite) -> bool:
    return graph.nodes[node_id].kind == NodeKind.METHOD or any(
        argument.kind in {"unpacked", "implicit"} for argument in site.arguments
    )


def _bound_argument(
    graph: CodeGraph, node_id: str, site: CallSite, parameter: int, assumptions: tuple[str, ...]
) -> int | None:
    profile = graph.parameter_bindings.get(node_id)
    if profile is not None and (
        tuple(slot.name for slot in profile)
        != tuple(slot.name for slot in graph.nodes[node_id].parameters)
        or profile[parameter].kind in {"var_positional", "var_keyword"}
    ):
        return None
    prefix = f"argument_mapping:{site.id}:{parameter}="
    mappings = [value[len(prefix) :] for value in assumptions if value.startswith(prefix)]
    if mappings:
        return _explicit_mapping(site, mappings)
    if profile is None or _unsupported_binding(graph, node_id, site):
        return None
    slot = profile[parameter]
    if (
        not slot.value_preserving
        and f"identity_parameter_value:{site.id}:{parameter}" not in assumptions
    ):
        return None
    if slot.kind in {"unknown", "var_positional", "var_keyword"}:
        return None
    return _inferred_argument(profile, site, parameter)


class _SiteSummary(NamedTuple):
    predicate: ConstraintExpr
    gaps: tuple[str, ...] = ()
    profiles: tuple[str, ...] = ()
    site_id: str | None = None


def _accepted_claims(
    graph: CodeGraph, site: CallSite, assumptions: tuple[str, ...], fingerprint: str
) -> tuple[list[ConstraintClaim], set[str]]:
    claims = []
    gaps = set()
    for annotation in graph.annotations.get(site.caller_id, []):
        claim = annotation.constraint
        if claim is None or claim.call_site_id != site.id:
            continue
        if not current_claim(graph, claim, fingerprint):
            gaps.add("stale_constraint")
        elif not set(claim.assumptions) <= set(assumptions):
            gaps.add("constraint_assumptions_unsatisfied")
        elif claim.justification == "asserted" and "accept_asserted_constraints" not in assumptions:
            gaps.add("asserted_constraint_not_accepted")
        else:
            claims.append(claim)
    return claims, gaps


def _binding_gaps(graph: CodeGraph, node_id: str, parameter: int) -> tuple[str, ...]:
    profile = graph.parameter_bindings.get(node_id, ())
    if parameter < len(profile) and not profile[parameter].value_preserving:
        return "parameter_binding_unknown", "parameter_value_conversion_unknown"
    return ("parameter_binding_unknown",)


def _site_summary(
    graph: CodeGraph,
    node_id: str,
    site: CallSite | None,
    parameter: int,
    assumptions: tuple[str, ...],
    *,
    fingerprint: str,
) -> _SiteSummary:
    if (
        site is None
        or site.execution != "call"
        or set(site.gaps) - {"parameter_mapping_unverified", "unpacked_argument_mapping_unknown"}
    ):
        return _SiteSummary(ConstraintBoolean("true"), ("incoming_call_evidence_incomplete",))
    argument = _bound_argument(graph, node_id, site, parameter, assumptions)
    if argument is None:
        return _SiteSummary(
            ConstraintBoolean("true"), _binding_gaps(graph, node_id, parameter), site_id=site.id
        )
    claims, gaps = _accepted_claims(graph, site, assumptions, fingerprint)
    predicate = _combine(
        "and", [_project(claim.predicate, argument, parameter)[0] for claim in claims]
    )
    if predicate == ConstraintBoolean("false"):
        gaps.add("unsatisfiable_incoming_constraint")
        predicate = ConstraintBoolean("true")
    elif predicate == ConstraintBoolean("true"):
        gaps.add("unconstrained_incoming_argument")
    return _SiteSummary(
        predicate,
        tuple(sorted(gaps)),
        tuple(sorted({claim.semantics for claim in claims})),
        site.id,
    )


def _summary_gaps(
    graph: CodeGraph,
    node_id: str,
    has_incoming: bool,
    profiles: set[str],
    assumptions: tuple[str, ...],
) -> set[str]:
    gaps = set()
    if len(profiles) > 1:
        gaps.add("conflicting_semantics_profiles")
    if "complete_callers" not in assumptions:
        gaps.add("caller_completeness_unverified")
    if node_id in graph.entrypoints and f"exclude_external_entrypoint:{node_id}" not in assumptions:
        gaps.add("known_external_entrypoint")
    if not has_incoming:
        gaps.add("no_observed_incoming_calls")
    return gaps


def _validate_summary_inputs(
    graph: CodeGraph, node_id: str, parameter: int, assumptions: tuple[str, ...]
) -> None:
    if type(parameter) is not int or not 0 <= parameter < len(graph.nodes[node_id].parameters):
        raise ValueError("Unknown callee parameter index")
    if (
        not isinstance(assumptions, tuple)
        or any(not isinstance(value, str) or not value.strip() for value in assumptions)
        or len(set(assumptions)) != len(assumptions)
    ):
        raise ValueError("Summary assumptions must be distinct nonempty names")


def summarize(
    graph: CodeGraph,
    node_id: str,
    parameter: int,
    assumptions: tuple[str, ...],
    *,
    fingerprint: str | None = None,
) -> ConstraintSummary:
    _validate_summary_inputs(graph, node_id, parameter, assumptions)
    fingerprint = graph_fingerprint(graph) if fingerprint is None else fingerprint
    occurrences = {
        (edge.source_id, edge.location)
        for edge in graph.edges
        if edge.kind == EdgeKind.CALLS and edge.target_id == node_id
    }
    indexed = {(site.caller_id, site.location): site for site in graph.call_sites.values()}
    contributions = [
        _site_summary(
            graph, node_id, indexed.get(occurrence), parameter, assumptions, fingerprint=fingerprint
        )
        for occurrence in sorted(occurrences, key=repr)
    ]
    profiles = {profile for result in contributions for profile in result.profiles}
    global_gaps = _summary_gaps(graph, node_id, bool(occurrences), profiles, assumptions)
    gaps = global_gaps | {gap for result in contributions for gap in result.gaps}
    alternatives = [result.predicate for result in contributions]
    if global_gaps:
        alternatives.append(ConstraintBoolean("true"))
    predicate = _combine("or", alternatives)
    return ConstraintSummary(
        node_id,
        parameter,
        predicate,
        assumptions,
        tuple(sorted(gaps)),
        tuple(result.site_id for result in contributions if result.site_id is not None),
        tuple(sorted(profiles)),
        "unknown" if predicate == ConstraintBoolean("true") else "conditional",
        fingerprint,
    )
