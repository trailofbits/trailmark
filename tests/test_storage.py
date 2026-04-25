"""Tests for the rustworkx-backed graph store."""

from __future__ import annotations

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
    SourceLocation,
    TrustLevel,
)
from trailmark.storage.graph_store import GraphStore

_LOC = SourceLocation(file_path="test.py", start_line=1, end_line=10)


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


class TestGraphStoreAncestors:
    def test_ancestors_ignore_non_call_edges(self) -> None:
        _, store = _build_contains_bridge_graph()
        assert store.ancestors_of("b") == {"a"}
        assert store.ancestors_of("a") == set()


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


class TestGraphStoreFindNode:
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
