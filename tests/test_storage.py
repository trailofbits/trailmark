"""Tests for the rustworkx-backed graph store."""

from __future__ import annotations

from dataclasses import replace
from itertools import pairwise

import pytest

from trailmark.models import (
    Annotation,
    AnnotationKind,
    CodeEdge,
    CodeGraph,
    CodeUnit,
    EdgeKind,
    EntrypointKind,
    EntrypointTag,
    NodeKind,
    NodeOrigin,
    SourceLocation,
    TrustLevel,
)
from trailmark.storage.graph_store import GraphStore

_LOC = SourceLocation(file_path="test.py", start_line=1, end_line=10)


def test_source_file_counts_are_copied_without_topology_rebuild() -> None:
    graph = CodeGraph(source_file_counts={"python": 1})
    store = GraphStore(graph)
    store.source_file_counts()["python"] = 99
    assert store.source_file_counts() == {"python": 1}
    graph.source_file_counts["rust"] = 2
    assert store.source_file_counts() == {"python": 1, "rust": 2}


def _make_node(
    node_id: str,
    name: str,
    complexity: int | None = None,
) -> CodeUnit:
    return CodeUnit(
        id=node_id,
        name=name,
        kind=NodeKind.FUNCTION,
        location=_LOC,
        cyclomatic_complexity=complexity,
    )


def _build_graph() -> tuple[CodeGraph, GraphStore]:
    """Build a test graph: A -> B -> C, A -> D."""
    nodes = {
        "a": _make_node("a", "a"),
        "b": _make_node("b", "b"),
        "c": _make_node("c", "c"),
        "d": _make_node("d", "d"),
    }
    edges = [
        CodeEdge(source_id="a", target_id="b", kind=EdgeKind.CALLS),
        CodeEdge(source_id="b", target_id="c", kind=EdgeKind.CALLS),
        CodeEdge(source_id="a", target_id="d", kind=EdgeKind.CALLS),
    ]
    graph = CodeGraph(nodes=nodes, edges=edges)
    return graph, GraphStore(graph)


def _build_contains_bridge_graph() -> tuple[CodeGraph, GraphStore]:
    """Build a graph where CONTAINS would be a false call bridge."""
    nodes = {
        "mod": _make_node("mod", "mod"),
        "a": _make_node("a", "a"),
        "b": _make_node("b", "b"),
    }
    edges = [
        CodeEdge(source_id="mod", target_id="a", kind=EdgeKind.CONTAINS),
        CodeEdge(source_id="a", target_id="b", kind=EdgeKind.CALLS),
    ]
    graph = CodeGraph(nodes=nodes, edges=edges)
    return graph, GraphStore(graph)


class TestGraphStoreStateAccess:
    def test_access_neighbors_validate_endpoints_and_keep_calls_separate(self) -> None:
        nodes = {name: _make_node(name, name) for name in ("f", "g", "x", "y")}
        graph = CodeGraph(
            nodes=nodes,
            edges=[
                CodeEdge("f", "x", EdgeKind.READS),
                CodeEdge("f", "x", EdgeKind.READS),
                CodeEdge("f", "x", EdgeKind.WRITES),
                CodeEdge("g", "x", EdgeKind.WRITES),
                CodeEdge("f", "y", EdgeKind.READS),
                CodeEdge("f", "g", EdgeKind.CALLS),
                CodeEdge("f", "missing", EdgeKind.WRITES),
                CodeEdge("missing", "x", EdgeKind.READS),
            ],
        )
        store = GraphStore(graph)
        assert [n.id for n in store.state_read_by("f")] == ["x", "y"]
        assert [n.id for n in store.state_written_by("f")] == ["x"]
        assert [n.id for n in store.readers_of("x")] == ["f"]
        assert [n.id for n in store.writers_of("x")] == ["f", "g"]
        assert store.readers_of("y") == [nodes["f"]]
        assert store.writers_of("y") == []
        assert store.state_read_by("x") == []
        assert store.state_written_by("x") == []
        for query in (
            store.state_read_by,
            store.state_written_by,
            store.readers_of,
            store.writers_of,
        ):
            assert query("missing") == []
        assert store.callees_of("f") == [nodes["g"]]
        assert store.reachable_from("f") == {"g"}


class TestGraphStoreCallers:
    def test_callers_of_b(self) -> None:
        _, store = _build_graph()
        callers = store.callers_of("b")
        assert len(callers) == 1
        assert callers[0].id == "a"
        assert callers[0].name == "a"

    def test_callers_of_c(self) -> None:
        _, store = _build_graph()
        callers = store.callers_of("c")
        assert len(callers) == 1
        assert callers[0].id == "b"

    def test_callers_of_root(self) -> None:
        _, store = _build_graph()
        callers = store.callers_of("a")
        assert len(callers) == 0

    def test_callers_of_nonexistent(self) -> None:
        _, store = _build_graph()
        assert store.callers_of("zzz") == []


class TestGraphStoreCallees:
    def test_callees_of_a(self) -> None:
        _, store = _build_graph()
        callees = store.callees_of("a")
        ids = {c.id for c in callees}
        assert ids == {"b", "d"}
        assert len(callees) == 2

    def test_callees_of_b(self) -> None:
        _, store = _build_graph()
        callees = store.callees_of("b")
        assert len(callees) == 1
        assert callees[0].id == "c"

    def test_callees_of_leaf(self) -> None:
        _, store = _build_graph()
        assert store.callees_of("c") == []

    def test_callees_of_nonexistent(self) -> None:
        _, store = _build_graph()
        assert store.callees_of("zzz") == []

    def test_callees_differ_from_callers(self) -> None:
        """callees_of and callers_of must return different results for middle node."""
        _, store = _build_graph()
        callees = store.callees_of("b")
        callers = store.callers_of("b")
        callee_ids = {c.id for c in callees}
        caller_ids = {c.id for c in callers}
        assert callee_ids == {"c"}
        assert caller_ids == {"a"}
        assert callee_ids != caller_ids

    def test_callees_only_calls_edges(self) -> None:
        """Only CALLS edges should be returned, not CONTAINS."""
        nodes = {
            "mod": _make_node("mod", "mod"),
            "f": _make_node("f", "f"),
        }
        edges = [
            CodeEdge(
                source_id="mod",
                target_id="f",
                kind=EdgeKind.CONTAINS,
            ),
        ]
        graph = CodeGraph(nodes=nodes, edges=edges)
        store = GraphStore(graph)
        assert store.callees_of("mod") == []

    def test_callers_only_calls_edges(self) -> None:
        """Only CALLS edges should count for callers."""
        nodes = {
            "mod": _make_node("mod", "mod"),
            "f": _make_node("f", "f"),
        }
        edges = [
            CodeEdge(
                source_id="mod",
                target_id="f",
                kind=EdgeKind.CONTAINS,
            ),
        ]
        graph = CodeGraph(nodes=nodes, edges=edges)
        store = GraphStore(graph)
        assert store.callers_of("f") == []


class TestGraphStorePaths:
    def test_self_call_site_does_not_create_a_nontrivial_simple_self_path(self) -> None:
        graph = CodeGraph(
            nodes={"a": _make_node("a", "a")},
            edges=[CodeEdge("a", "a", EdgeKind.CALLS)],
            entrypoints={"a": EntrypointTag(kind=EntrypointKind.API)},
            subgraphs={"shared": {"a"}},
        )
        store = GraphStore(graph)

        assert store.paths_between("a", "a", max_depth=1) == []
        assert store.entrypoint_paths_to("a", max_depth=1) == []
        assert store.connect_subgraphs("shared", "shared", max_depth=1) == [["a"]]
        assert store.call_edges() == graph.edges

    @pytest.mark.parametrize("edge_count", [1, 2, 3, 20, 21])
    def test_depth_counts_edges_at_exact_chain_boundary(self, edge_count: int) -> None:
        ids = [f"f{i}" for i in range(edge_count + 1)]
        graph = CodeGraph(
            nodes={node_id: _make_node(node_id, node_id) for node_id in ids},
            edges=[CodeEdge(src, dst, EdgeKind.CALLS) for src, dst in pairwise(ids)],
            entrypoints={ids[0]: EntrypointTag(kind=EntrypointKind.API)},
            subgraphs={"source": {ids[0]}, "sink": {ids[-1]}},
        )
        store = GraphStore(graph)

        assert store.paths_between(ids[0], ids[-1], max_depth=edge_count) == [ids]
        assert store.entrypoint_paths_to(ids[-1], max_depth=edge_count) == [ids]
        assert store.connect_subgraphs("source", "sink", max_depth=edge_count) == [ids]
        assert store.paths_between(ids[0], ids[-1]) == ([ids] if edge_count <= 20 else [])
        if edge_count > 1:
            assert store.paths_between(ids[0], ids[-1], max_depth=edge_count - 1) == []
            assert store.entrypoint_paths_to(ids[-1], max_depth=edge_count - 1) == []
            assert store.connect_subgraphs("source", "sink", max_depth=edge_count - 1) == []

    @pytest.mark.parametrize("max_depth", [0, -1])
    def test_nonpositive_depth_rejected_even_without_nodes(self, max_depth: int) -> None:
        store = GraphStore(CodeGraph())
        with pytest.raises(ValueError, match="max_depth.*at least 1"):
            store.paths_between("missing", "missing", max_depth=max_depth)
        with pytest.raises(ValueError, match="max_depth.*at least 1"):
            store.entrypoint_paths_to("missing", max_depth=max_depth)
        with pytest.raises(ValueError, match="max_depth.*at least 1"):
            store.connect_subgraphs("missing", "missing", max_depth=max_depth)

    @pytest.mark.parametrize(
        "edge_kinds",
        [None, frozenset({EdgeKind.CALLS, EdgeKind.TYPE_USES})],
    )
    def test_repeated_call_sites_do_not_duplicate_node_paths(
        self, edge_kinds: frozenset[EdgeKind] | None
    ) -> None:
        nodes = {name: _make_node(name, name) for name in ("a", "b", "c")}
        calls = [
            CodeEdge("a", "b", EdgeKind.CALLS),
            CodeEdge("a", "b", EdgeKind.CALLS),
            CodeEdge("b", "c", EdgeKind.CALLS),
            CodeEdge("b", "c", EdgeKind.CALLS),
            CodeEdge("a", "c", EdgeKind.CALLS),
        ]
        graph = CodeGraph(
            nodes=nodes,
            edges=[*calls, CodeEdge("a", "b", EdgeKind.TYPE_USES)],
        )
        store = GraphStore(graph)

        paths = store.paths_between("a", "c", edge_kinds=edge_kinds)

        assert sorted(paths) == [["a", "b", "c"], ["a", "c"]]
        assert store.call_edges() == calls
        assert graph.edges == [*calls, CodeEdge("a", "b", EdgeKind.TYPE_USES)]
        assert {node.id for node in store.callees_of("a")} == {"b", "c"}

    def test_paths_a_to_c(self) -> None:
        _, store = _build_graph()
        paths = store.paths_between("a", "c")
        assert len(paths) == 1
        assert paths[0] == ["a", "b", "c"]

    def test_paths_a_to_d(self) -> None:
        _, store = _build_graph()
        paths = store.paths_between("a", "d")
        assert len(paths) == 1
        assert paths[0] == ["a", "d"]

    def test_no_path(self) -> None:
        _, store = _build_graph()
        assert store.paths_between("c", "a") == []

    def test_paths_nonexistent_src(self) -> None:
        _, store = _build_graph()
        assert store.paths_between("zzz", "c") == []

    def test_paths_nonexistent_dst(self) -> None:
        _, store = _build_graph()
        assert store.paths_between("a", "zzz") == []

    def test_paths_both_nonexistent(self) -> None:
        _, store = _build_graph()
        assert store.paths_between("zzz", "yyy") == []

    def test_paths_max_depth_too_short(self) -> None:
        _, store = _build_graph()
        paths = store.paths_between("a", "c", max_depth=1)
        assert len(paths) == 0

    def test_paths_max_depth_sufficient(self) -> None:
        """max_depth=3 should find path a->b->c (2 edges)."""
        _, store = _build_graph()
        paths = store.paths_between("a", "c", max_depth=3)
        assert len(paths) == 1
        assert paths[0] == ["a", "b", "c"]

    def test_paths_default_max_depth(self) -> None:
        """Default max_depth=20 should find all paths."""
        _, store = _build_graph()
        paths = store.paths_between("a", "c")
        assert len(paths) == 1

    def test_paths_default_max_depth_is_20(self) -> None:
        """Verify the default cutoff is exactly 20."""
        import inspect

        sig = inspect.signature(GraphStore.paths_between)
        assert sig.parameters["max_depth"].default == 20

    def test_paths_direct(self) -> None:
        _, store = _build_graph()
        paths = store.paths_between("a", "b")
        assert len(paths) == 1
        assert paths[0] == ["a", "b"]

    def test_paths_self(self) -> None:
        """Path from node to itself has no simple path."""
        _, store = _build_graph()
        paths = store.paths_between("a", "a")
        assert len(paths) == 0

    def test_paths_ignore_non_call_edges(self) -> None:
        """Structural edges must not create call paths."""
        _, store = _build_contains_bridge_graph()
        assert store.paths_between("mod", "b") == []
        assert store.paths_between("a", "b") == [["a", "b"]]

    def test_paths_can_use_explicit_projection(self) -> None:
        nodes = {
            "src": _make_node("src", "src"),
            "proxy": _make_node("proxy", "proxy"),
            "bin": _make_node("bin", "bin"),
        }
        graph = CodeGraph(
            nodes=nodes,
            edges=[
                CodeEdge("src", "proxy", EdgeKind.CALLS),
                CodeEdge("proxy", "bin", EdgeKind.CORRESPONDS_TO),
            ],
        )
        store = GraphStore(graph)

        assert store.paths_between("src", "bin") == []
        assert store.paths_between(
            "src",
            "bin",
            edge_kinds=frozenset({EdgeKind.CALLS, EdgeKind.CORRESPONDS_TO}),
        ) == [["src", "proxy", "bin"]]


class TestGraphStoreReachability:
    def test_reachable_from_a(self) -> None:
        _, store = _build_graph()
        reachable = store.reachable_from("a")
        assert reachable == {"b", "c", "d"}

    def test_reachable_from_b(self) -> None:
        _, store = _build_graph()
        reachable = store.reachable_from("b")
        assert reachable == {"c"}

    def test_reachable_from_leaf(self) -> None:
        _, store = _build_graph()
        assert store.reachable_from("c") == set()

    def test_reachable_from_d(self) -> None:
        _, store = _build_graph()
        assert store.reachable_from("d") == set()

    def test_reachable_from_nonexistent(self) -> None:
        _, store = _build_graph()
        assert store.reachable_from("zzz") == set()

    def test_reachable_ignores_non_call_edges(self) -> None:
        _, store = _build_contains_bridge_graph()
        assert store.reachable_from("mod") == set()
        assert store.reachable_from("a") == {"b"}

    def test_reachable_can_use_explicit_projection(self) -> None:
        nodes = {
            "a": _make_node("a", "a"),
            "b": _make_node("b", "b"),
            "c": _make_node("c", "c"),
        }
        graph = CodeGraph(
            nodes=nodes,
            edges=[
                CodeEdge("a", "b", EdgeKind.TYPE_USES),
                CodeEdge("b", "c", EdgeKind.CALLS),
            ],
        )
        store = GraphStore(graph)

        assert store.reachable_from("a") == set()
        assert store.reachable_from(
            "a",
            edge_kinds=frozenset({EdgeKind.TYPE_USES, EdgeKind.CALLS}),
        ) == {"b", "c"}


class TestGraphStoreAncestors:
    def test_ancestors_ignore_non_call_edges(self) -> None:
        _, store = _build_contains_bridge_graph()
        assert store.ancestors_of("b") == {"a"}
        assert store.ancestors_of("a") == set()


class TestGraphStoreSubgraphConnections:
    def test_read_membership_sets_are_copies(self) -> None:
        graph, store = _build_graph()
        graph.subgraphs["group"] = {"a", "b"}

        store.subgraph("group").clear()
        groups = store.all_subgraphs()
        groups["group"].add("outside")
        groups["extra"] = {"c"}

        assert graph.subgraphs == {"group": {"a", "b"}}

    def test_subgraph_edges_induced(self) -> None:
        nodes = {
            "a": _make_node("a", "a"),
            "b": _make_node("b", "b"),
            "c": _make_node("c", "c"),
        }
        graph = CodeGraph(
            nodes=nodes,
            edges=[
                CodeEdge("a", "b", EdgeKind.CALLS),
                CodeEdge("b", "c", EdgeKind.CALLS),
            ],
            subgraphs={"pair": {"a", "b"}},
        )
        store = GraphStore(graph)

        edges = store.subgraph_edges("pair")
        assert [(edge.source_id, edge.target_id) for edge in edges] == [("a", "b")]

    def test_connect_subgraphs(self) -> None:
        graph, store = _build_graph()
        graph.subgraphs["sources"] = {"a"}
        graph.subgraphs["sinks"] = {"c", "d"}

        paths = store.connect_subgraphs("sources", "sinks")
        assert ["a", "b", "c"] in paths
        assert ["a", "d"] in paths

    def test_connect_subgraphs_overlap(self) -> None:
        graph, store = _build_graph()
        graph.subgraphs["left"] = {"a", "b"}
        graph.subgraphs["right"] = {"b"}

        assert ["b"] in store.connect_subgraphs("left", "right")

    def test_connect_subgraphs_missing(self) -> None:
        _, store = _build_graph()
        assert store.connect_subgraphs("missing", "also_missing") == []


class TestGraphStoreAnnotations:
    def test_nodes_with_annotation(self) -> None:
        graph, _ = _build_graph()
        graph.annotations["b"] = [
            Annotation(
                kind=AnnotationKind.ASSUMPTION,
                description="x > 0",
                source="docstring",
            ),
        ]
        store = GraphStore(graph)
        nodes = store.nodes_with_annotation(AnnotationKind.ASSUMPTION)
        assert len(nodes) == 1
        assert nodes[0].id == "b"

    def test_no_matching_annotations(self) -> None:
        _, store = _build_graph()
        nodes = store.nodes_with_annotation(AnnotationKind.ASSUMPTION)
        assert len(nodes) == 0

    def test_annotation_wrong_kind(self) -> None:
        """Querying for a different kind should find nothing."""
        graph, _ = _build_graph()
        graph.annotations["a"] = [
            Annotation(
                kind=AnnotationKind.ASSUMPTION,
                description="test",
                source="test",
            ),
        ]
        store = GraphStore(graph)
        nodes = store.nodes_with_annotation(AnnotationKind.INVARIANT)
        assert len(nodes) == 0


class TestGraphStoreEntrypoints:
    def test_all_entrypoints(self) -> None:
        graph, _ = _build_graph()
        graph.entrypoints["a"] = EntrypointTag(
            kind=EntrypointKind.USER_INPUT,
            trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
        )
        store = GraphStore(graph)
        eps = store.all_entrypoints()
        assert len(eps) == 1
        assert eps[0][0] == "a"
        assert eps[0][1].kind == EntrypointKind.USER_INPUT
        assert eps[0][1].trust_level == TrustLevel.UNTRUSTED_EXTERNAL

    def test_all_entrypoints_empty(self) -> None:
        _, store = _build_graph()
        assert store.all_entrypoints() == []

    def test_entrypoint_paths_to(self) -> None:
        graph, _ = _build_graph()
        graph.entrypoints["a"] = EntrypointTag(
            kind=EntrypointKind.API,
        )
        store = GraphStore(graph)
        paths = store.entrypoint_paths_to("c")
        assert len(paths) == 1
        assert paths[0] == ["a", "b", "c"]

    def test_entrypoint_paths_to_direct(self) -> None:
        graph, _ = _build_graph()
        graph.entrypoints["a"] = EntrypointTag(
            kind=EntrypointKind.API,
        )
        store = GraphStore(graph)
        paths = store.entrypoint_paths_to("b")
        assert len(paths) == 1
        assert paths[0] == ["a", "b"]

    def test_entrypoint_paths_to_unreachable(self) -> None:
        """If entrypoint can't reach target, no paths returned."""
        graph, _ = _build_graph()
        graph.entrypoints["c"] = EntrypointTag(
            kind=EntrypointKind.API,
        )
        store = GraphStore(graph)
        paths = store.entrypoint_paths_to("a")
        assert paths == []

    def test_entrypoint_paths_to_self(self) -> None:
        graph, _ = _build_graph()
        graph.entrypoints["a"] = EntrypointTag(
            kind=EntrypointKind.API,
        )
        store = GraphStore(graph)
        paths = store.entrypoint_paths_to("a")
        assert paths == []

    def test_entrypoint_paths_max_depth(self) -> None:
        graph, _ = _build_graph()
        graph.entrypoints["a"] = EntrypointTag(
            kind=EntrypointKind.API,
        )
        store = GraphStore(graph)
        # max_depth=1 should not find a->b->c (depth 2)
        paths = store.entrypoint_paths_to("c", max_depth=1)
        assert paths == []

    def test_entrypoint_paths_default_max_depth_is_20(self) -> None:
        """Verify the default cutoff is exactly 20."""
        import inspect

        sig = inspect.signature(GraphStore.entrypoint_paths_to)
        assert sig.parameters["max_depth"].default == 20

    def test_multiple_entrypoints(self) -> None:
        """Multiple entrypoints can produce paths to same target."""
        graph, _ = _build_graph()
        graph.entrypoints["a"] = EntrypointTag(
            kind=EntrypointKind.API,
        )
        graph.entrypoints["b"] = EntrypointTag(
            kind=EntrypointKind.USER_INPUT,
        )
        store = GraphStore(graph)
        paths = store.entrypoint_paths_to("c")
        assert len(paths) == 2
        path_strs = [tuple(p) for p in paths]
        assert ("a", "b", "c") in path_strs
        assert ("b", "c") in path_strs

    def test_entrypoint_paths_ignore_non_call_edges(self) -> None:
        graph, _ = _build_contains_bridge_graph()
        graph.entrypoints["mod"] = EntrypointTag(kind=EntrypointKind.API)
        store = GraphStore(graph)
        assert store.entrypoint_paths_to("b") == []


class TestGraphStoreComplexity:
    def test_nodes_by_complexity(self) -> None:
        graph, _ = _build_graph()
        graph.nodes["a"] = CodeUnit(
            id="a",
            name="a",
            kind=NodeKind.FUNCTION,
            location=_LOC,
            cyclomatic_complexity=15,
        )
        graph.nodes["b"] = CodeUnit(
            id="b",
            name="b",
            kind=NodeKind.FUNCTION,
            location=_LOC,
            cyclomatic_complexity=3,
        )
        store = GraphStore(graph)
        hot = store.nodes_by_complexity(10)
        assert len(hot) == 1
        assert hot[0].id == "a"
        assert hot[0].cyclomatic_complexity == 15

    def test_boundary_complexity(self) -> None:
        graph, _ = _build_graph()
        graph.nodes["a"] = CodeUnit(
            id="a",
            name="a",
            kind=NodeKind.FUNCTION,
            location=_LOC,
            cyclomatic_complexity=10,
        )
        store = GraphStore(graph)
        # Exactly at threshold should be included (>=)
        assert len(store.nodes_by_complexity(10)) == 1
        # One above threshold should exclude
        assert len(store.nodes_by_complexity(11)) == 0

    def test_none_complexity_excluded(self) -> None:
        """Nodes with None complexity should be excluded."""
        graph, _ = _build_graph()
        # Default nodes have None complexity
        store = GraphStore(graph)
        assert store.nodes_by_complexity(1) == []

    def test_complexity_multiple_matches(self) -> None:
        nodes = {
            "a": _make_node("a", "a", complexity=10),
            "b": _make_node("b", "b", complexity=20),
            "c": _make_node("c", "c", complexity=5),
        }
        graph = CodeGraph(nodes=nodes, edges=[])
        store = GraphStore(graph)
        result = store.nodes_by_complexity(10)
        ids = {n.id for n in result}
        assert ids == {"a", "b"}


class TestGraphStoreCallResolution:
    def test_reports_validate_endpoints_and_filter_callers(self) -> None:
        source = _make_node("f", "f")
        other = _make_node("g", "g")
        proxy = CodeUnit(
            id="proxy:unknown",
            name="unknown",
            kind=NodeKind.PROXY,
            location=_LOC,
            attributes=(("proxy_kind", "unresolved"),),
        )
        calls = [
            CodeEdge("f", proxy.id, EdgeKind.CALLS),
            CodeEdge("g", proxy.id, EdgeKind.CALLS),
            CodeEdge("f", "g", EdgeKind.CALLS),
        ]
        graph = CodeGraph(
            nodes={n.id: n for n in (source, other, proxy)},
            edges=[
                *calls,
                CodeEdge("missing", proxy.id, EdgeKind.CALLS),
                CodeEdge("f", "missing", EdgeKind.CALLS),
                CodeEdge("f", proxy.id, EdgeKind.TYPE_USES),
            ],
        )
        store = GraphStore(graph)
        assert store.call_edges() == calls
        assert store.unresolved_calls() == calls[:2]
        assert store.unresolved_calls("f") == calls[:1]
        assert store.unresolved_calls("g") == calls[1:2]
        assert store.unresolved_calls("missing") == []
        assert store.unresolved_calls(proxy.id) == []


class TestGraphStoreFindNode:
    @pytest.mark.parametrize("origin", [NodeOrigin.SOURCE, NodeOrigin.BINARY, NodeOrigin.SYNTHETIC])
    @pytest.mark.parametrize("reverse_order", [False, True])
    def test_declarations_precede_proxy_name_but_explicit_proxy_id_wins(
        self, origin: NodeOrigin, reverse_order: bool
    ) -> None:
        declaration = replace(_make_node("mod:Auth.check", "check"), origin=origin)
        proxy = replace(
            _make_node("proxy.unresolved:Auth.check", "Auth.check"),
            kind=NodeKind.PROXY,
            origin=NodeOrigin.PROXY,
        )
        units = [proxy, declaration]
        if reverse_order:
            units.reverse()
        store = GraphStore(CodeGraph(nodes={unit.id: unit for unit in units}))

        assert store.find_node_id("Auth.check") == declaration.id
        assert store.find_node_id(proxy.id) == proxy.id
        assert GraphStore(CodeGraph(nodes={proxy.id: proxy})).find_node_id("Auth.check") == proxy.id

    @pytest.mark.parametrize("reverse_order", [False, True])
    @pytest.mark.parametrize(
        ("lower_id", "lower_name", "higher_id", "higher_name"),
        [
            ("mod:target", "target", "target", "other"),
            ("mod:target", "other", "opaque_id", "target"),
            ("mod:Cls.target", "other", "opaque_id", "target"),
            ("mod:Cls.target", "other", "mod:target", "other"),
        ],
        ids=["id-over-name", "name-over-colon", "name-over-dot", "colon-over-dot"],
    )
    def test_lookup_precedence_is_independent_of_insertion_order(
        self,
        lower_id: str,
        lower_name: str,
        higher_id: str,
        higher_name: str,
        reverse_order: bool,
    ) -> None:
        lower = _make_node(lower_id, lower_name)
        higher = _make_node(higher_id, higher_name)
        units = [lower, higher]
        if reverse_order:
            units.reverse()
        store = GraphStore(CodeGraph(nodes={unit.id: unit for unit in units}))

        assert store.find_node("target") == higher
        assert store.find_node_id("target") == higher.id

    def test_find_by_exact_id(self) -> None:
        _, store = _build_graph()
        node = store.find_node("a")
        assert node is not None
        assert node.id == "a"
        assert node.name == "a"

    def test_find_by_name(self) -> None:
        _, store = _build_graph()
        node = store.find_node("b")
        assert node is not None
        assert node.id == "b"

    def test_find_by_colon_suffix(self) -> None:
        nodes = {"mod:func": _make_node("mod:func", "func")}
        graph = CodeGraph(nodes=nodes, edges=[])
        store = GraphStore(graph)
        node = store.find_node("func")
        assert node is not None
        assert node.id == "mod:func"

    def test_find_by_dot_method(self) -> None:
        nodes = {
            "mod:Cls.method": _make_node("mod:Cls.method", "method"),
        }
        graph = CodeGraph(nodes=nodes, edges=[])
        store = GraphStore(graph)
        node = store.find_node("method")
        assert node is not None
        assert node.id == "mod:Cls.method"

    def test_find_prefers_exact_id(self) -> None:
        """Exact ID match should win over name/suffix match."""
        nodes = {
            "a": _make_node("a", "different_name"),
        }
        graph = CodeGraph(nodes=nodes, edges=[])
        store = GraphStore(graph)
        node = store.find_node("a")
        assert node is not None
        assert node.id == "a"

    def test_find_by_name_without_colon_suffix(self) -> None:
        """Name match alone (no colon suffix) should still find the node."""
        nodes = {"opaque_id": _make_node("opaque_id", "target")}
        graph = CodeGraph(nodes=nodes, edges=[])
        store = GraphStore(graph)
        node = store.find_node("target")
        assert node is not None
        assert node.id == "opaque_id"

    def test_find_node_returns_none(self) -> None:
        _, store = _build_graph()
        assert store.find_node("nonexistent") is None

    def test_find_node_id(self) -> None:
        _, store = _build_graph()
        assert store.find_node_id("a") == "a"
        assert store.find_node_id("nonexistent") is None

    def test_find_node_id_colon_suffix(self) -> None:
        nodes = {"mod:func": _make_node("mod:func", "func")}
        graph = CodeGraph(nodes=nodes, edges=[])
        store = GraphStore(graph)
        assert store.find_node_id("func") == "mod:func"

    def test_find_node_id_dot_method(self) -> None:
        nodes = {
            "mod:Cls.method": _make_node("mod:Cls.method", "method"),
        }
        graph = CodeGraph(nodes=nodes, edges=[])
        store = GraphStore(graph)
        assert store.find_node_id("method") == "mod:Cls.method"


class TestGraphStoreAnnotationMutation:
    def test_add_annotation_to_existing_node(self) -> None:
        _, store = _build_graph()
        ann = Annotation(
            kind=AnnotationKind.ASSUMPTION,
            description="x > 0",
            source="llm",
        )
        assert store.add_annotation("a", ann) is True
        assert len(store.annotations_for("a")) == 1
        assert store.annotations_for("a")[0] == ann

    def test_add_annotation_to_nonexistent_node(self) -> None:
        _, store = _build_graph()
        ann = Annotation(
            kind=AnnotationKind.ASSUMPTION,
            description="x > 0",
            source="llm",
        )
        assert store.add_annotation("zzz", ann) is False

    def test_annotations_for_with_data(self) -> None:
        graph, _ = _build_graph()
        graph.annotations["b"] = [
            Annotation(
                kind=AnnotationKind.PRECONDITION,
                description="non-null",
                source="docstring",
            ),
        ]
        store = GraphStore(graph)
        anns = store.annotations_for("b")
        assert len(anns) == 1
        assert anns[0].kind == AnnotationKind.PRECONDITION

    def test_annotations_for_no_annotations(self) -> None:
        _, store = _build_graph()
        assert store.annotations_for("a") == []

    def test_annotations_for_nonexistent_node(self) -> None:
        _, store = _build_graph()
        assert store.annotations_for("zzz") == []

    def test_annotations_for_returns_copy(self) -> None:
        _, store = _build_graph()
        ann = Annotation(
            kind=AnnotationKind.ASSUMPTION,
            description="test",
            source="test",
        )
        store.add_annotation("a", ann)
        result = store.annotations_for("a")
        result.clear()
        assert len(store.annotations_for("a")) == 1

    def test_clear_annotations_existing_node(self) -> None:
        _, store = _build_graph()
        ann = Annotation(
            kind=AnnotationKind.ASSUMPTION,
            description="test",
            source="test",
        )
        store.add_annotation("a", ann)
        assert store.clear_annotations("a") is True
        assert store.annotations_for("a") == []

    def test_clear_annotations_nonexistent_node(self) -> None:
        _, store = _build_graph()
        assert store.clear_annotations("zzz") is False

    def test_clear_annotations_by_kind(self) -> None:
        _, store = _build_graph()
        store.add_annotation(
            "a",
            Annotation(
                kind=AnnotationKind.ASSUMPTION,
                description="assume",
                source="test",
            ),
        )
        store.add_annotation(
            "a",
            Annotation(
                kind=AnnotationKind.INVARIANT,
                description="invariant",
                source="test",
            ),
        )
        assert store.clear_annotations("a", AnnotationKind.ASSUMPTION) is True
        anns = store.annotations_for("a")
        assert len(anns) == 1
        assert anns[0].kind == AnnotationKind.INVARIANT
