"""Independent specifications for graph queries over small directed multigraphs."""

from __future__ import annotations

from collections.abc import Iterable

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from trailmark.models import (
    CodeEdge,
    CodeGraph,
    CodeUnit,
    EdgeKind,
    NodeKind,
    NodeOrigin,
    SourceLocation,
)
from trailmark.storage.graph_store import GraphStore

_LOCATION = SourceLocation("graph.py", 1, 1)
_KINDS = st.one_of(st.none(), st.frozensets(st.sampled_from(list(EdgeKind)), max_size=3))


def _graph(size: int, edges: Iterable[tuple[int, int, EdgeKind]]) -> CodeGraph:
    return CodeGraph(
        nodes={
            f"n{index}": CodeUnit(
                id=f"n{index}", name=f"n{index}", kind=NodeKind.FUNCTION, location=_LOCATION
            )
            for index in range(size)
        },
        edges=[CodeEdge(f"n{start}", f"n{end}", kind) for start, end, kind in edges],
    )


@st.composite
def _small_multigraph(draw: st.DrawFn) -> CodeGraph:
    size = draw(st.integers(min_value=0, max_value=5))
    edges = draw(
        st.lists(
            st.tuples(
                st.integers(min_value=-1, max_value=size),
                st.integers(min_value=-1, max_value=size),
                st.sampled_from(list(EdgeKind)),
            ),
            max_size=18,
        )
    )
    return _graph(size, edges)


def _adjacency(
    graph: CodeGraph, kinds: frozenset[EdgeKind] | None, *, reverse: bool = False
) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {node: set() for node in graph.nodes}
    selected = frozenset({EdgeKind.CALLS}) if kinds is None else kinds
    for edge in graph.edges:
        if edge.kind in selected and edge.source_id in result and edge.target_id in result:
            start, end = edge.source_id, edge.target_id
            if reverse:
                start, end = end, start
            result[start].add(end)
    return result


def _walk(adjacency: dict[str, set[str]], start: str) -> set[str]:
    if start not in adjacency:
        return set()
    seen = {start}
    pending = [start]
    while pending:
        node = pending.pop()
        for neighbor in adjacency[node] - seen:
            seen.add(neighbor)
            pending.append(neighbor)
    return seen - {start}


def _paths(
    adjacency: dict[str, set[str]], start: str, target: str, depth: int
) -> set[tuple[str, ...]]:
    result = set()
    pending: list[tuple[str, ...]] = [(start,)] if start in adjacency and start != target else []
    while pending:
        path = pending.pop()
        if len(path) - 1 == depth:
            continue
        for neighbor in adjacency[path[-1]] - set(path):
            extended = (*path, neighbor)
            if neighbor == target:
                result.add(extended)
            else:
                pending.append(extended)
    return result


@given(graph=_small_multigraph(), kinds=_KINDS)
@example(
    graph=_graph(4, [(0, 1, EdgeKind.CALLS), (1, 2, EdgeKind.CALLS), (2, 3, EdgeKind.CALLS)]),
    kinds=None,
)
@example(
    graph=_graph(
        3,
        [
            (0, 0, EdgeKind.CALLS),
            (0, 1, EdgeKind.CALLS),
            (0, 1, EdgeKind.CALLS),
            (1, 0, EdgeKind.CALLS),
            (1, 2, EdgeKind.CONTAINS),
            (2, 3, EdgeKind.CALLS),
        ],
    ),
    kinds=None,
)
@settings(max_examples=200, deadline=None)
def test_reachability_matches_independent_walk(
    graph: CodeGraph, kinds: frozenset[EdgeKind] | None
) -> None:
    store = GraphStore(graph)
    forward, reverse = _adjacency(graph, kinds), _adjacency(graph, kinds, reverse=True)
    for node in (*graph.nodes, "missing"):
        assert store.reachable_from(node, kinds) == _walk(forward, node)
        assert store.ancestors_of(node, kinds) == _walk(reverse, node)


@given(graph=_small_multigraph())
@example(
    graph=_graph(
        2,
        [
            (0, 0, EdgeKind.CALLS),
            (0, 1, EdgeKind.CALLS),
            (0, 1, EdgeKind.CALLS),
            (0, 1, EdgeKind.CONTAINS),
            (1, 2, EdgeKind.CALLS),
            (-1, 0, EdgeKind.CALLS),
        ],
    )
)
@settings(max_examples=150, deadline=None)
def test_call_neighbors_preserve_self_calls_and_deduplicate_call_sites(graph: CodeGraph) -> None:
    store = GraphStore(graph)
    forward = _adjacency(graph, None)
    reverse = _adjacency(graph, None, reverse=True)
    for node in (*graph.nodes, "missing"):
        callees, callers = store.callees_of(node), store.callers_of(node)
        assert {unit.id for unit in callees} == forward.get(node, set())
        assert {unit.id for unit in callers} == reverse.get(node, set())
        assert len(callees) == len({unit.id for unit in callees})
        assert len(callers) == len({unit.id for unit in callers})


@given(graph=_small_multigraph(), kinds=_KINDS, depth=st.integers(min_value=1, max_value=6))
@example(
    graph=_graph(4, [(0, 1, EdgeKind.CALLS), (1, 2, EdgeKind.CALLS), (2, 3, EdgeKind.CALLS)]),
    kinds=None,
    depth=3,
)
@example(
    graph=_graph(
        3,
        [(0, 1, EdgeKind.CALLS), (0, 1, EdgeKind.CALLS), (1, 2, EdgeKind.CALLS)],
    ),
    kinds=None,
    depth=1,
)
@settings(max_examples=150, deadline=None)
def test_simple_paths_match_independent_enumeration(
    graph: CodeGraph, kinds: frozenset[EdgeKind] | None, depth: int
) -> None:
    store = GraphStore(graph)
    adjacency = _adjacency(graph, kinds)
    for start in (*graph.nodes, "missing"):
        for target in (*graph.nodes, "missing"):
            paths = store.paths_between(start, target, depth, kinds)
            actual = {tuple(path) for path in paths}
            assert actual == _paths(adjacency, start, target, depth)
            assert len(paths) == len(actual)


def test_rebuild_replaces_warmed_forward_and_reverse_relation_caches() -> None:
    graph = _graph(3, [(0, 1, EdgeKind.CALLS), (1, 2, EdgeKind.CALLS)])
    store = GraphStore(graph)
    references = frozenset({EdgeKind.REFERENCES})
    assert store.reachable_from("n0") == {"n1", "n2"}
    assert store.ancestors_of("n2") == {"n0", "n1"}
    assert store.reachable_from("n0", references) == set()
    assert store.ancestors_of("n2", references) == set()
    graph.edges = [CodeEdge("n0", "n2", EdgeKind.REFERENCES)]
    store.rebuild_index()
    assert store.reachable_from("n0") == set()
    assert store.ancestors_of("n2") == set()
    assert store.reachable_from("n0", references) == {"n2"}
    assert store.ancestors_of("n2", references) == {"n0"}


@given(graph=_small_multigraph())
@settings(max_examples=75, deadline=None)
def test_rebuild_invalidates_all_cached_edge_projections(graph: CodeGraph) -> None:
    store = GraphStore(graph)
    selections = [None, frozenset(), frozenset(EdgeKind)]
    for selection in selections:
        for node in graph.nodes:
            store.reachable_from(node, selection)
    graph.nodes["new"] = CodeUnit(id="new", name="new", kind=NodeKind.FUNCTION, location=_LOCATION)
    graph.edges = [
        CodeEdge("new", node, EdgeKind.REFERENCES) for node in graph.nodes if node != "new"
    ]
    store.rebuild_index()
    for selection in selections:
        adjacency = _adjacency(graph, selection)
        for node in graph.nodes:
            assert store.reachable_from(node, selection) == _walk(adjacency, node)


@given(graph=_small_multigraph())
@settings(max_examples=100, deadline=None)
def test_access_neighbors_match_relation_specific_endpoints(graph: CodeGraph) -> None:
    store = GraphStore(graph)
    queries = [
        (EdgeKind.READS, store.state_read_by, store.readers_of),
        (EdgeKind.WRITES, store.state_written_by, store.writers_of),
    ]
    for kind, outgoing, incoming in queries:
        forward = _adjacency(graph, frozenset({kind}))
        reverse = _adjacency(graph, frozenset({kind}), reverse=True)
        for node in (*graph.nodes, "missing"):
            found = [unit.id for unit in outgoing(node)]
            assert found == sorted(forward.get(node, set()))
            found = [unit.id for unit in incoming(node)]
            assert found == sorted(reverse.get(node, set()))


@pytest.mark.parametrize(
    "kind,origin",
    [(NodeKind.PROXY, NodeOrigin.SOURCE), (NodeKind.FUNCTION, NodeOrigin.PROXY)],
)
def test_partial_proxy_tagging_does_not_outrank_a_declaration(
    kind: NodeKind, origin: NodeOrigin
) -> None:
    proxy = CodeUnit("proxy:shared", "shared", kind, _LOCATION, origin=origin)
    real = CodeUnit("real:shared", "shared", NodeKind.FUNCTION, _LOCATION)
    store = GraphStore(CodeGraph(nodes={node.id: node for node in (proxy, real)}))
    assert store.find_node("shared") == real
    assert store.find_node(proxy.id) == proxy
    assert store.find_node_id("shared") == real.id


@given(graph=_small_multigraph(), kinds=_KINDS, data=st.data())
@settings(max_examples=150, deadline=None)
def test_induced_subgraphs_preserve_sites_and_return_detached_memberships(
    graph: CodeGraph, kinds: frozenset[EdgeKind] | None, data: st.DataObject
) -> None:
    members = data.draw(st.sets(st.sampled_from(list(graph.nodes)))) if graph.nodes else set()
    graph.subgraphs["slice"] = members
    store = GraphStore(graph)
    assert store.subgraph_edges("slice", kinds) == [
        edge
        for edge in graph.edges
        if edge.source_id in members
        and edge.target_id in members
        and (kinds is None or edge.kind in kinds)
    ]
    assert store.subgraph_edges("missing", kinds) == []
    detached = store.subgraph("slice")
    detached.clear()
    assert store.subgraph("slice") == members
    all_subgraphs = store.all_subgraphs()
    all_subgraphs["slice"].clear()
    all_subgraphs.clear()
    assert store.all_subgraphs() == {"slice": members}
