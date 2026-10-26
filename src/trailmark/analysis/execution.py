"""Bounded evidence-path queries; no authentication inference from names or trust."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from trailmark.models.execution import ExecutionFlow, ExecutionPoint
from trailmark.models.graph import CodeGraph


def authentication_paths(
    graph: CodeGraph,
    roots: tuple[ExecutionPoint, ...],
    target: ExecutionPoint,
    *,
    max_depth: int = 40,
    max_steps: int = 10_000,
) -> dict[str, Any]:
    """Classify only the requested point using explicitly supplied pre-auth roots.

    Unknown alternatives and bounds remain visible. This is an execution-fact
    query, not a classifier over the ordinary call graph or a deployment census.
    """
    if not roots:
        raise ValueError("At least one explicit pre-auth root is required")
    if max_depth < 1:
        raise ValueError("max_depth must be positive")
    if max_steps < 1:
        raise ValueError("max_steps must be positive")
    adjacency: dict[ExecutionPoint, list[ExecutionFlow]] = {}
    for flow in graph.execution_flows.values():
        adjacency.setdefault(flow.source, []).append(flow)
    paths: list[dict[str, object]] = []
    truncated = False
    steps = 0
    for root in roots:
        pending: list[tuple[ExecutionPoint, str, tuple[str, ...], frozenset[ExecutionPoint]]] = [
            (root, "pre_auth", (), frozenset())
        ]
        while pending:
            steps += 1
            if steps > max_steps:
                truncated = True
                break
            point, state, chain, seen = pending.pop()
            if point == target:
                paths.append({"classification": state, "flow_ids": list(chain)})
                continue
            if len(chain) >= max_depth:
                truncated = True
                continue
            for flow in sorted(adjacency.get(point, []), key=lambda f: (f.order or 0, f.id)):
                if flow.target in seen:
                    truncated = True
                    continue
                next_state = state
                if not flow.reviewed or flow.confidence == "uncertain":
                    next_state = "unknown"
                elif flow.kind == "gate_success" and flow.identity == "user" and state != "unknown":
                    next_state = "post_auth"
                pending.append((flow.target, next_state, (*chain, flow.id), seen | {point}))
        if steps > max_steps:
            break
    states = {p["classification"] for p in paths}
    classification = "unknown"
    if paths and not truncated and "unknown" not in states:
        classification = "mixed" if states == {"pre_auth", "post_auth"} else next(iter(states))
    return {
        "classification": classification,
        "target": asdict(target),
        "paths": paths,
        "truncated": truncated,
        "scope": "explicit_execution_facts",
        "limits": [
            "Unmodeled dispatch and absent flows are unknown; ordinary calls and trust tags "
            "are not authentication evidence."
        ],
    }
