"""Property-based tests for Trailmark core invariants.

Uses Hypothesis to verify structural properties that example-based
tests cannot cover exhaustively: index bijections, merge monotonicity,
annotation round-trips, graph traversal duality, and diagram safety.
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import replace

from hypothesis import example, given, settings
from hypothesis import strategies as st

from trailmark.diagram import sanitize_id
from trailmark.models.annotations import Annotation, AnnotationKind
from trailmark.models.edges import CodeEdge, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit, NodeKind, SourceLocation
from trailmark.storage.graph_store import GraphStore

# ── Shared strategies ────────────────────────────────────────────

_MERMAID_ID_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

_LOC = SourceLocation("test.py", 1, 10)

_node_id_st = st.text(
    st.characters(whitelist_categories=("L", "N"), whitelist_characters="_:"),
    min_size=1,
    max_size=20,
)


def _make_unit(nid: str) -> CodeUnit:
    return CodeUnit(id=nid, name=nid, kind=NodeKind.FUNCTION, location=_LOC)


@st.composite
def _graph_st(
    draw: st.DrawFn,
    max_nodes: int = 15,
    max_edges: int = 25,
) -> CodeGraph:
    """Generate a random CodeGraph with unique node IDs."""
    ids = draw(
        st.lists(_node_id_st, min_size=0, max_size=max_nodes, unique=True),
    )
    nodes = {nid: _make_unit(nid) for nid in ids}
    edges: list[CodeEdge] = []
    if len(ids) >= 2:
        raw = draw(
            st.lists(
                st.tuples(
                    st.sampled_from(ids),
                    st.sampled_from(ids),
                    st.sampled_from(list(EdgeKind)),
                ),
                max_size=max_edges,
            ),
        )
        edges = [CodeEdge(source_id=s, target_id=t, kind=k) for s, t, k in raw if s != t]
    deps = draw(
        st.lists(st.text(min_size=1, max_size=10), max_size=5, unique=True),
    )
    return CodeGraph(nodes=nodes, edges=edges, dependencies=deps)


_annotation_kind_st = st.sampled_from(list(AnnotationKind))

_annotation_st = st.builds(
    Annotation,
    kind=_annotation_kind_st,
    description=st.text(min_size=1, max_size=30),
    source=st.text(min_size=1, max_size=10),
)


# ── 1. GraphStore index bijection ────────────────────────────────


@given(graph=_graph_st())  # type: ignore[misc]  # st.composite ParamSpec
@settings(max_examples=200)
def test_index_bijection(graph: CodeGraph) -> None:
    """_id_to_idx and _idx_to_id form a perfect bijection over nodes."""
    store = GraphStore(graph)
    assert set(store._id_to_idx.keys()) == set(graph.nodes.keys())
    assert len(store._id_to_idx) == len(store._idx_to_id)
    for nid, idx in store._id_to_idx.items():
        assert store._idx_to_id[idx] == nid
    for idx, nid in store._idx_to_id.items():
        assert store._id_to_idx[nid] == idx


# ── 2. CodeGraph.merge monotonicity ─────────────────────────────


@given(g1=_graph_st(), g2=_graph_st())  # type: ignore[misc]  # st.composite ParamSpec
@example(
    g1=CodeGraph(nodes={"shared": _make_unit("shared")}, dependencies=["old", "common"]),
    g2=CodeGraph(
        nodes={"shared": replace(_make_unit("shared"), name="replacement")},
        edges=[CodeEdge("shared", "shared", EdgeKind.CALLS)] * 2,
        dependencies=["common", "new"],
    ),
)
@settings(max_examples=200, deadline=None)
def test_merge_preserves_exact_nodes_edges_and_dependency_order(
    g1: CodeGraph, g2: CodeGraph
) -> None:
    """Merge replaces nodes, retains repeated edges, and stably deduplicates dependencies."""
    before, incoming = deepcopy(g1), deepcopy(g2)
    g1.merge(g2)
    assert g1.nodes == before.nodes | incoming.nodes
    assert g1.edges == before.edges + incoming.edges
    assert g1.dependencies == list(dict.fromkeys([*before.dependencies, *incoming.dependencies]))
    assert g2 == incoming


# ── 3. Annotation add/clear round-trip ──────────────────────────


@given(
    annotations=st.lists(_annotation_st, min_size=1, max_size=15),
    clear_kind=st.one_of(st.none(), _annotation_kind_st),
)
@settings(max_examples=200)
def test_annotation_add_clear_roundtrip(
    annotations: list[Annotation],
    clear_kind: AnnotationKind | None,
) -> None:
    """Adding then clearing annotations preserves correct state."""
    graph = CodeGraph(nodes={"n": _make_unit("n")})
    store = GraphStore(graph)

    for ann in annotations:
        assert store.add_annotation("n", ann) is True

    assert store.annotations_for("n") == annotations
    detached = store.annotations_for("n")
    detached.clear()
    assert store.annotations_for("n") == annotations

    assert store.clear_annotations("n", clear_kind) is True
    expected = [] if clear_kind is None else [ann for ann in annotations if ann.kind != clear_kind]
    assert store.annotations_for("n") == expected


# ── 4. reachable_from / ancestors_of duality ─────────────────────


@given(graph=_graph_st(max_nodes=10, max_edges=15))  # type: ignore[misc]  # st.composite ParamSpec
@settings(max_examples=100)
def test_reachable_ancestors_duality(graph: CodeGraph) -> None:
    """If B is reachable from A, then A is an ancestor of B."""
    store = GraphStore(graph)
    for node_id in graph.nodes:
        reachable = store.reachable_from(node_id)
        for r_id in reachable:
            ancestors = store.ancestors_of(r_id)
            assert node_id in ancestors, f"{node_id} reaches {r_id} but not in ancestors_of({r_id})"


@given(graph=_graph_st(max_nodes=10, max_edges=15))  # type: ignore[misc]  # st.composite ParamSpec
@settings(max_examples=100)
def test_reachable_transitive_closure(graph: CodeGraph) -> None:
    """reachable_from is transitively closed (modulo start node).

    rx.descendants excludes the starting node, so in a cycle A→B→A,
    reachable_from(A) = {B} and reachable_from(B) = {A}. The property
    holds when we account for the excluded start node.
    """
    store = GraphStore(graph)
    for node_id in graph.nodes:
        reachable = store.reachable_from(node_id)
        for r_id in reachable:
            sub_reachable = store.reachable_from(r_id)
            # Everything reachable from r_id (except node_id itself,
            # which is excluded by rx.descendants) must be in reachable
            assert sub_reachable - {node_id} <= reachable, (
                f"reachable_from({node_id}) is not transitively closed: "
                f"{r_id} reaches {sub_reachable - reachable - {node_id}}"
            )


# ── 5. sanitize_id idempotency and output validity ──────────────


@given(node_id=st.text(min_size=1, max_size=80))
@settings(max_examples=500)
def test_sanitize_id_idempotent(node_id: str) -> None:
    """Applying sanitize_id twice gives the same result as once."""
    once = sanitize_id(node_id)
    twice = sanitize_id(once)
    assert once == twice


@given(node_id=st.text(min_size=1, max_size=80))
@settings(max_examples=500)
def test_sanitize_id_produces_valid_mermaid_id(node_id: str) -> None:
    """sanitize_id always produces a valid Mermaid identifier."""
    result = sanitize_id(node_id)
    assert len(result) > 0
    assert _MERMAID_ID_RE.match(result), f"Invalid Mermaid ID: {result!r}"


def test_sanitize_id_empty_input() -> None:
    """Empty input produces a non-empty fallback identifier."""
    assert sanitize_id("") == "n_empty"
    assert _MERMAID_ID_RE.match(sanitize_id(""))
