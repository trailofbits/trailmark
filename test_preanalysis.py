"""Tests for the pre-analysis passes."""

from __future__ import annotations

from trailmark.models import (
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
from trailmark.query.api import QueryEngine

_LOC = SourceLocation(file_path="test.py", start_line=1, end_line=10)


def _node(
    node_id: str,
    name: str,
    complexity: int | None = None,
    kind: NodeKind = NodeKind.FUNCTION,
) -> CodeUnit:
    return CodeUnit(
        id=node_id,
        name=name,
        kind=kind,
        location=_LOC,
        cyclomatic_complexity=complexity,
    )


def _build_chain_graph() -> QueryEngine:
    """Build: ep_untrusted -> handler -> db_query -> logger.

    ep_untrusted is an untrusted entrypoint.
    """
    nodes = {
        "ep_untrusted": _node("ep_untrusted", "ep_untrusted", 2),
        "handler": _node("handler", "handler", 12),
        "db_query": _node("db_query", "db_query", 8),
        "logger": _node("logger", "logger", 1),
    }
    edges = [
        CodeEdge("ep_untrusted", "handler", EdgeKind.CALLS),
        CodeEdge("handler", "db_query", EdgeKind.CALLS),
        CodeEdge("handler", "logger", EdgeKind.CALLS),
    ]
    graph = CodeGraph(
        nodes=nodes,
        edges=edges,
        entrypoints={
            "ep_untrusted": EntrypointTag(
                kind=EntrypointKind.USER_INPUT,
                trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
            ),
        },
    )
    return QueryEngine.from_graph(graph)


def _build_mixed_trust_graph() -> QueryEngine:
    """Build a graph with both trusted and untrusted entrypoints.

    ep_untrusted -> shared_handler -> db_write
    ep_trusted   -> shared_handler -> db_write
    """
    nodes = {
        "ep_untrusted": _node("ep_untrusted", "ep_untrusted", 2),
        "ep_trusted": _node("ep_trusted", "ep_trusted", 1),
        "shared_handler": _node("shared_handler", "shared_handler", 6),
        "db_write": _node("db_write", "db_write", 3),
    }
    edges = [
        CodeEdge("ep_untrusted", "shared_handler", EdgeKind.CALLS),
        CodeEdge("ep_trusted", "shared_handler", EdgeKind.CALLS),
        CodeEdge("shared_handler", "db_write", EdgeKind.CALLS),
    ]
    graph = CodeGraph(
        nodes=nodes,
        edges=edges,
        entrypoints={
            "ep_untrusted": EntrypointTag(
                kind=EntrypointKind.USER_INPUT,
                trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
            ),
            "ep_trusted": EntrypointTag(
                kind=EntrypointKind.API,
                trust_level=TrustLevel.TRUSTED_INTERNAL,
            ),
        },
    )
    return QueryEngine.from_graph(graph)


def _build_no_entrypoints_graph() -> QueryEngine:
    """Build a simple graph with no entrypoints."""
    nodes = {
        "a": _node("a", "a", 3),
        "b": _node("b", "b", 5),
    }
    edges = [CodeEdge("a", "b", EdgeKind.CALLS)]
    graph = CodeGraph(nodes=nodes, edges=edges)
    return QueryEngine.from_graph(graph)


class TestPreanalysisReturnsSummary:
    def test_returns_all_keys(self) -> None:
        engine = _build_chain_graph()
        result = engine.preanalysis()
        assert "blast_radius" in result
        assert "entrypoints" in result
        assert "privilege_boundaries" in result
        assert "taint_propagation" in result


class TestBlastRadius:
    def test_annotates_all_nodes(self) -> None:
        engine = _build_chain_graph()
        result = engine.preanalysis()
        assert result["blast_radius"]["annotated_nodes"] == 4

    def test_high_blast_radius_subgraph(self) -> None:
        """Nodes with many descendants should be in subgraph."""
        # Build a wider graph so ep has >= 10 descendants
        nodes = {"ep": _node("ep", "ep", 1)}
        edges = []
        for i in range(12):
            nid = f"f{i}"
            nodes[nid] = _node(nid, nid, 1)
            edges.append(CodeEdge("ep", nid, EdgeKind.CALLS))
        graph = CodeGraph(
            nodes=nodes,
            edges=edges,
            entrypoints={
                "ep": EntrypointTag(
                    kind=EntrypointKind.USER_INPUT,
                    trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
                ),
            },
        )
        engine = QueryEngine.from_graph(graph)
        engine.preanalysis()
        high = engine.subgraph("high_blast_radius")
        ep_ids = {n["id"] for n in high}
        assert "ep" in ep_ids

    def test_max_radius(self) -> None:
        engine = _build_chain_graph()
        result = engine.preanalysis()
        # ep_untrusted -> handler -> (db_query, logger) = 3 descendants
        assert result["blast_radius"]["max_radius"] == 3

    def test_blast_radius_annotation_content(self) -> None:
        engine = _build_chain_graph()
        engine.preanalysis()
        anns = engine.annotations_of(
            "ep_untrusted",
            kind=AnnotationKind.BLAST_RADIUS,
        )
        assert len(anns) == 1
        assert "downstream" in anns[0]["description"]
        assert "upstream" in anns[0]["description"]

    def test_critical_descendants_in_annotation(self) -> None:
        engine = _build_chain_graph()
        engine.preanalysis()
        anns = engine.annotations_of(
            "ep_untrusted",
            kind=AnnotationKind.BLAST_RADIUS,
        )
        # handler has CC=12, should appear as critical
        assert "handler" in anns[0]["description"]

    def test_blast_radius_ignores_non_call_edges(self) -> None:
        nodes = {
            "mod": _node("mod", "mod", kind=NodeKind.MODULE),
            "f": _node("f", "f", 7),
        }
        graph = CodeGraph(
            nodes=nodes,
            edges=[CodeEdge("mod", "f", EdgeKind.CONTAINS)],
        )
        engine = QueryEngine.from_graph(graph)
        result = engine.preanalysis()

        assert result["blast_radius"]["max_radius"] == 0
        anns = engine.annotations_of("mod", kind=AnnotationKind.BLAST_RADIUS)
        assert anns[0]["description"].startswith("0 downstream, 0 upstream")


class TestEntrypointEnumeration:
    def test_entrypoints_subgraph(self) -> None:
        engine = _build_chain_graph()
        engine.preanalysis()
        ep_nodes = engine.subgraph("entrypoints")
        ep_ids = {n["id"] for n in ep_nodes}
        assert ep_ids == {"ep_untrusted"}

    def test_reachable_subgraph(self) -> None:
        engine = _build_chain_graph()
        engine.preanalysis()
        reachable = engine.subgraph("entrypoint_reachable")
        reachable_ids = {n["id"] for n in reachable}
        assert "handler" in reachable_ids
        assert "db_query" in reachable_ids
        assert "ep_untrusted" in reachable_ids

    def test_by_trust_level_subgraph(self) -> None:
        engine = _build_mixed_trust_graph()
        engine.preanalysis()
        untrusted = engine.subgraph("entrypoints:untrusted_external")
        assert len(untrusted) == 1
        assert untrusted[0]["id"] == "ep_untrusted"

    def test_summary_counts(self) -> None:
        engine = _build_mixed_trust_graph()
        result = engine.preanalysis()
        ep = result["entrypoints"]
        assert ep["total_entrypoints"] == 2
        assert ep["reachable_nodes"] >= 4

    def test_no_entrypoints(self) -> None:
        engine = _build_no_entrypoints_graph()
        result = engine.preanalysis()
        assert result["entrypoints"]["total_entrypoints"] == 0
        assert result["entrypoints"]["reachable_nodes"] == 0


class TestPrivilegeBoundary:
    def test_detects_boundary_in_mixed_trust(self) -> None:
        engine = _build_mixed_trust_graph()
        result = engine.preanalysis()
        assert result["privilege_boundaries"]["boundary_nodes"] > 0

    def test_boundary_subgraph_populated(self) -> None:
        engine = _build_mixed_trust_graph()
        engine.preanalysis()
        boundary = engine.subgraph("privilege_boundary")
        boundary_ids = {n["id"] for n in boundary}
        # shared_handler and db_write are reachable from both
        # trust levels, so they should be at the boundary
        assert len(boundary_ids) > 0

    def test_no_boundary_with_single_trust(self) -> None:
        engine = _build_chain_graph()
        result = engine.preanalysis()
        assert result["privilege_boundaries"]["boundary_nodes"] == 0

    def test_boundary_annotation_content(self) -> None:
        engine = _build_mixed_trust_graph()
        engine.preanalysis()
        boundary = engine.subgraph("privilege_boundary")
        if boundary:
            node_id = boundary[0]["id"]
            anns = engine.annotations_of(
                node_id,
                kind=AnnotationKind.PRIVILEGE_BOUNDARY,
            )
            assert len(anns) >= 1
            assert "trust transition" in anns[0]["description"]


class TestTaintPropagation:
    def test_tainted_subgraph(self) -> None:
        engine = _build_chain_graph()
        engine.preanalysis()
        tainted = engine.subgraph("tainted")
        tainted_ids = {n["id"] for n in tainted}
        assert "ep_untrusted" in tainted_ids
        assert "handler" in tainted_ids
        assert "db_query" in tainted_ids

    def test_taint_annotation_content(self) -> None:
        engine = _build_chain_graph()
        engine.preanalysis()
        anns = engine.annotations_of(
            "handler",
            kind=AnnotationKind.TAINT_PROPAGATION,
        )
        assert len(anns) == 1
        assert "ep_untrusted" in anns[0]["description"]

    def test_trusted_entrypoint_not_taint_source(self) -> None:
        """Trusted entrypoints should not propagate taint."""
        nodes = {
            "ep_trusted": _node("ep_trusted", "ep_trusted", 1),
            "internal": _node("internal", "internal", 2),
        }
        edges = [
            CodeEdge("ep_trusted", "internal", EdgeKind.CALLS),
        ]
        graph = CodeGraph(
            nodes=nodes,
            edges=edges,
            entrypoints={
                "ep_trusted": EntrypointTag(
                    kind=EntrypointKind.API,
                    trust_level=TrustLevel.TRUSTED_INTERNAL,
                ),
            },
        )
        engine = QueryEngine.from_graph(graph)
        result = engine.preanalysis()
        assert result["taint_propagation"]["tainted_nodes"] == 0

    def test_semi_trusted_does_taint(self) -> None:
        """Semi-trusted entrypoints should propagate taint."""
        nodes = {
            "ep_semi": _node("ep_semi", "ep_semi", 1),
            "target": _node("target", "target", 2),
        }
        edges = [
            CodeEdge("ep_semi", "target", EdgeKind.CALLS),
        ]
        graph = CodeGraph(
            nodes=nodes,
            edges=edges,
            entrypoints={
                "ep_semi": EntrypointTag(
                    kind=EntrypointKind.THIRD_PARTY,
                    trust_level=TrustLevel.SEMI_TRUSTED_EXTERNAL,
                ),
            },
        )
        engine = QueryEngine.from_graph(graph)
        result = engine.preanalysis()
        assert result["taint_propagation"]["tainted_nodes"] == 2

    def test_no_entrypoints_no_taint(self) -> None:
        engine = _build_no_entrypoints_graph()
        result = engine.preanalysis()
        assert result["taint_propagation"]["tainted_nodes"] == 0

    def test_taint_source_count(self) -> None:
        engine = _build_mixed_trust_graph()
        result = engine.preanalysis()
        # Only ep_untrusted is a taint source (trusted is excluded)
        assert result["taint_propagation"]["taint_sources"] == 1


class TestSubgraphAPI:
    def test_subgraph_names_populated(self) -> None:
        engine = _build_chain_graph()
        engine.preanalysis()
        names = engine.subgraph_names()
        assert "entrypoints" in names
        assert "tainted" in names
        assert "high_blast_radius" in names

    def test_subgraph_empty_name(self) -> None:
        engine = _build_chain_graph()
        result = engine.subgraph("nonexistent")
        assert result == []

    def test_subgraphs_in_json(self) -> None:
        """Subgraphs should appear in JSON export."""
        import json

        engine = _build_chain_graph()
        engine.preanalysis()
        data = json.loads(engine.to_json())
        assert "subgraphs" in data
        assert "tainted" in data["subgraphs"]
