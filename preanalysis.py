"""Pre-analysis passes that enrich code graphs before downstream skills.

Runs four passes over the graph:
1. Blast radius estimation — how many nodes are affected per function
2. Entry point enumeration — systematic entrypoint + reachability mapping
3. Privilege boundary crossing — where trust levels change across calls
4. Taint propagation — untrusted data flow through the call graph
"""

from __future__ import annotations

from typing import Any

import rustworkx as rx

from trailmark.models.annotations import (
    Annotation,
    AnnotationKind,
    TrustLevel,
)
from trailmark.models.edges import EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.storage.graph_store import GraphStore

_BLAST_RADIUS_THRESHOLD = 10
_PREANALYSIS_SOURCE = "preanalysis"
_PREANALYSIS_SUBGRAPHS = frozenset(
    {
        "high_blast_radius",
        "entrypoints",
        "entrypoint_reachable",
        "privilege_boundary",
        "tainted",
    }
)


def _clear_prior_preanalysis(store: GraphStore) -> None:
    """Remove annotations and subgraphs from a previous preanalysis run."""
    graph = store._graph  # noqa: SLF001
    for node_id in list(graph.annotations):
        graph.annotations[node_id] = [
            a for a in graph.annotations[node_id] if a.source != _PREANALYSIS_SOURCE
        ]
        if not graph.annotations[node_id]:
            del graph.annotations[node_id]
    for name in list(graph.subgraphs):
        if name in _PREANALYSIS_SUBGRAPHS or name.startswith("entrypoints:"):
            del graph.subgraphs[name]


def run_preanalysis(store: GraphStore) -> dict[str, Any]:
    """Run all pre-analysis passes and return a summary.

    Mutates the graph by adding annotations and subgraphs.
    Returns a dict summarising what each pass found.

    Safe to call multiple times: clears prior preanalysis
    annotations before recomputing.
    """
    _clear_prior_preanalysis(store)
    blast = _compute_blast_radius(store)
    entry = _enumerate_entrypoints(store)
    priv = _detect_privilege_boundaries(store)
    taint = _propagate_taint(store)
    return {
        "blast_radius": blast,
        "entrypoints": entry,
        "privilege_boundaries": priv,
        "taint_propagation": taint,
    }


def _compute_blast_radius(store: GraphStore) -> dict[str, Any]:
    """Annotate each node with its downstream blast radius.

    Blast radius = number of nodes reachable via call edges.
    Nodes above the threshold are collected in a subgraph.
    """
    graph = store._graph  # noqa: SLF001
    digraph = store._call_digraph  # noqa: SLF001
    id_to_idx = store._call_id_to_idx  # noqa: SLF001
    idx_to_id = store._call_idx_to_id  # noqa: SLF001

    high_blast: set[str] = set()
    max_radius = 0
    annotated = 0

    for node_id in graph.nodes:
        idx = id_to_idx.get(node_id)
        if idx is None:
            continue
        descendants = rx.descendants(digraph, idx)
        downstream = len(descendants)
        ancestors = rx.ancestors(digraph, idx)
        upstream = len(ancestors)

        critical = _top_critical_descendants(
            descendants,
            idx_to_id,
            graph,
        )
        desc = f"{downstream} downstream, {upstream} upstream"
        if critical:
            desc += f"; critical: {', '.join(critical)}"

        ann = Annotation(
            kind=AnnotationKind.BLAST_RADIUS,
            description=desc,
            source="preanalysis",
        )
        graph.add_annotation(node_id, ann)
        annotated += 1

        if downstream >= _BLAST_RADIUS_THRESHOLD:
            high_blast.add(node_id)
        max_radius = max(max_radius, downstream)

    store.add_subgraph("high_blast_radius", high_blast)
    return {
        "annotated_nodes": annotated,
        "high_blast_count": len(high_blast),
        "max_radius": max_radius,
        "threshold": _BLAST_RADIUS_THRESHOLD,
    }


def _top_critical_descendants(
    descendants: set[int],
    idx_to_id: dict[int, str],
    graph: CodeGraph,
    limit: int = 5,
) -> list[str]:
    """Pick the highest-complexity descendants as critical."""
    scored: list[tuple[int, str]] = []
    for d_idx in descendants:
        d_id = idx_to_id.get(d_idx)
        if d_id is None:
            continue
        node = graph.nodes.get(d_id)
        if node is None:
            continue
        cc = node.cyclomatic_complexity or 0
        if cc > 0:
            scored.append((cc, d_id))
    scored.sort(reverse=True)
    return [name for _, name in scored[:limit]]


def _enumerate_entrypoints(store: GraphStore) -> dict[str, Any]:
    """Build subgraphs for entrypoints and their reachable nodes."""
    graph = store._graph  # noqa: SLF001

    ep_ids: set[str] = set()
    reachable_all: set[str] = set()
    by_trust: dict[str, set[str]] = {}

    for ep_id, tag in graph.entrypoints.items():
        ep_ids.add(ep_id)
        trust_key = tag.trust_level.value
        by_trust.setdefault(trust_key, set()).add(ep_id)

        reachable = store.reachable_from(ep_id)
        reachable_all.update(reachable)
        reachable_all.add(ep_id)

    store.add_subgraph("entrypoints", ep_ids)
    store.add_subgraph("entrypoint_reachable", reachable_all)
    for trust_key, ids in by_trust.items():
        store.add_subgraph(f"entrypoints:{trust_key}", ids)

    return {
        "total_entrypoints": len(ep_ids),
        "reachable_nodes": len(reachable_all),
        "by_trust_level": {k: len(v) for k, v in by_trust.items()},
    }


def _detect_privilege_boundaries(
    store: GraphStore,
) -> dict[str, Any]:
    """Find nodes where trust levels change across call edges.

    A privilege boundary exists where a node reachable from an
    untrusted entrypoint calls into a node reachable only from
    trusted entrypoints (or not tagged at all).
    """
    graph = store._graph  # noqa: SLF001

    untrusted_reachable = _reachable_from_trust(
        store,
        TrustLevel.UNTRUSTED_EXTERNAL,
    )
    semi_trusted_reachable = _reachable_from_trust(
        store,
        TrustLevel.SEMI_TRUSTED_EXTERNAL,
    )
    trusted_reachable = _reachable_from_trust(
        store,
        TrustLevel.TRUSTED_INTERNAL,
    )

    boundary_nodes: set[str] = set()

    for edge in graph.edges:
        if edge.kind != EdgeKind.CALLS:
            continue
        src, tgt = edge.source_id, edge.target_id
        src_trust = _node_trust_set(
            src,
            untrusted_reachable,
            semi_trusted_reachable,
            trusted_reachable,
        )
        tgt_trust = _node_trust_set(
            tgt,
            untrusted_reachable,
            semi_trusted_reachable,
            trusted_reachable,
        )
        if src_trust != tgt_trust and src_trust and tgt_trust:
            boundary_nodes.add(src)
            boundary_nodes.add(tgt)
            for node_id in (src, tgt):
                ann = Annotation(
                    kind=AnnotationKind.PRIVILEGE_BOUNDARY,
                    description=(
                        f"trust transition across call: "
                        f"{_trust_label(src_trust)}"
                        f" -> {_trust_label(tgt_trust)}"
                    ),
                    source="preanalysis",
                )
                graph.add_annotation(node_id, ann)

    store.add_subgraph("privilege_boundary", boundary_nodes)
    return {"boundary_nodes": len(boundary_nodes)}


def _reachable_from_trust(
    store: GraphStore,
    trust: TrustLevel,
) -> set[str]:
    """Return all nodes reachable from entrypoints at a trust level."""
    graph = store._graph  # noqa: SLF001
    result: set[str] = set()
    for ep_id, tag in graph.entrypoints.items():
        if tag.trust_level == trust:
            result.add(ep_id)
            result.update(store.reachable_from(ep_id))
    return result


def _node_trust_set(
    node_id: str,
    untrusted: set[str],
    semi_trusted: set[str],
    trusted: set[str],
) -> frozenset[str]:
    """Return the set of trust levels that can reach a node."""
    levels: set[str] = set()
    if node_id in untrusted:
        levels.add("untrusted_external")
    if node_id in semi_trusted:
        levels.add("semi_trusted_external")
    if node_id in trusted:
        levels.add("trusted_internal")
    return frozenset(levels)


def _trust_label(levels: frozenset[str]) -> str:
    if not levels:
        return "none"
    return "+".join(sorted(levels))


def _propagate_taint(store: GraphStore) -> dict[str, Any]:
    """Propagate taint from untrusted entrypoints through calls.

    Marks all reachable nodes as tainted and annotates each with
    the entrypoint source that taints it.
    """
    graph = store._graph  # noqa: SLF001

    tainted_all: set[str] = set()
    taint_sources: dict[str, list[str]] = {}

    for ep_id, tag in graph.entrypoints.items():
        if tag.trust_level == TrustLevel.TRUSTED_INTERNAL:
            continue
        reachable = store.reachable_from(ep_id)
        reachable.add(ep_id)
        tainted_all.update(reachable)

        for node_id in reachable:
            taint_sources.setdefault(node_id, []).append(ep_id)

    for node_id, sources in taint_sources.items():
        unique = sorted(set(sources))
        desc = f"tainted via: {', '.join(unique)}"
        ann = Annotation(
            kind=AnnotationKind.TAINT_PROPAGATION,
            description=desc,
            source="preanalysis",
        )
        graph.add_annotation(node_id, ann)

    store.add_subgraph("tainted", tainted_all)
    return {
        "tainted_nodes": len(tainted_all),
        "taint_sources": len(
            [
                ep_id
                for ep_id, tag in graph.entrypoints.items()
                if tag.trust_level != TrustLevel.TRUSTED_INTERNAL
            ],
        ),
    }
