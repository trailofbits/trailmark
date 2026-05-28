"""Tests for the QueryEngine."""

from __future__ import annotations

import json

from trailmark.models import (
    AnnotationKind,
    AssetValue,
    CodeEdge,
    CodeGraph,
    CodeUnit,
    EdgeKind,
    EntrypointKind,
    EntrypointTag,
    NodeKind,
    SourceLocation,
    TrustLevel,
    TypeParameter,
    TypeRef,
)
from trailmark.query.api import (
    QueryEngine,
    _annotation_to_dict,
    _edge_to_dict,
    _unit_to_dict,
)

_LOC = SourceLocation(file_path="test.py", start_line=1, end_line=10)


def _make_node(
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


def _build_engine() -> QueryEngine:
    """Build a test engine: entry -> handler -> db_query."""
    nodes = {
        "entry": _make_node("entry", "entry", complexity=2),
        "handler": _make_node("handler", "handler", complexity=12),
        "db_query": _make_node("db_query", "db_query", complexity=3),
    }
    edges = [
        CodeEdge(
            source_id="entry",
            target_id="handler",
            kind=EdgeKind.CALLS,
        ),
        CodeEdge(
            source_id="handler",
            target_id="db_query",
            kind=EdgeKind.CALLS,
        ),
    ]
    graph = CodeGraph(
        nodes=nodes,
        edges=edges,
        entrypoints={
            "entry": EntrypointTag(
                kind=EntrypointKind.USER_INPUT,
                trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
            ),
        },
        dependencies=["flask", "sqlalchemy"],
    )
    return QueryEngine.from_graph(graph)


class TestQueryEngineCallers:
    def test_callers_of(self) -> None:
        engine = _build_engine()
        callers = engine.callers_of("handler")
        assert len(callers) == 1
        assert callers[0]["name"] == "entry"
        assert callers[0]["id"] == "entry"
        assert callers[0]["kind"] == "function"

    def test_callers_of_missing(self) -> None:
        engine = _build_engine()
        assert engine.callers_of("nonexistent") == []


class TestQueryEngineCallees:
    def test_callees_of(self) -> None:
        engine = _build_engine()
        callees = engine.callees_of("entry")
        assert len(callees) == 1
        assert callees[0]["name"] == "handler"
        assert callees[0]["id"] == "handler"
        assert callees[0]["kind"] == "function"

    def test_callees_of_missing(self) -> None:
        engine = _build_engine()
        assert engine.callees_of("nonexistent") == []


class TestQueryEnginePaths:
    def test_paths_between(self) -> None:
        engine = _build_engine()
        paths = engine.paths_between("entry", "db_query")
        assert len(paths) == 1
        assert paths[0] == ["entry", "handler", "db_query"]

    def test_paths_direct(self) -> None:
        engine = _build_engine()
        paths = engine.paths_between("entry", "handler")
        assert len(paths) == 1
        assert paths[0] == ["entry", "handler"]

    def test_paths_missing_src(self) -> None:
        engine = _build_engine()
        assert engine.paths_between("zzz", "db_query") == []

    def test_paths_missing_dst(self) -> None:
        engine = _build_engine()
        assert engine.paths_between("entry", "zzz") == []

    def test_paths_both_missing(self) -> None:
        engine = _build_engine()
        assert engine.paths_between("zzz", "yyy") == []

    def test_paths_no_reverse(self) -> None:
        engine = _build_engine()
        assert engine.paths_between("db_query", "entry") == []


class TestQueryEngineSubgraphs:
    def test_subgraph_edges(self) -> None:
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
        engine = QueryEngine.from_graph(graph)

        edges = engine.subgraph_edges("pair")
        assert edges == [
            {
                "source": "a",
                "target": "b",
                "kind": "calls",
                "confidence": "certain",
            }
        ]

    def test_connect_subgraphs_with_bridge_edges(self) -> None:
        nodes = {
            "src": _make_node("src", "src"),
            "proxy": _make_node("proxy", "proxy"),
            "resolved": _make_node("resolved", "resolved"),
            "bin": _make_node("bin", "bin"),
        }
        graph = CodeGraph(
            nodes=nodes,
            edges=[
                CodeEdge("src", "proxy", EdgeKind.CALLS),
                CodeEdge("proxy", "resolved", EdgeKind.RESOLVES_TO),
                CodeEdge("resolved", "bin", EdgeKind.CORRESPONDS_TO),
            ],
            subgraphs={"source": {"src"}, "binary": {"bin"}},
        )
        engine = QueryEngine.from_graph(graph)

        assert engine.connect_subgraphs("source", "binary") == []
        assert engine.connect_subgraphs(
            "source",
            "binary",
            edge_kinds=("calls", "resolves_to", "corresponds_to"),
        ) == [["src", "proxy", "resolved", "bin"]]


class TestQueryEngineTypes:
    def test_generic_parameters(self) -> None:
        node = CodeUnit(
            id="mod:Box",
            name="Box",
            kind=NodeKind.CLASS,
            location=_LOC,
            type_parameters=(TypeParameter(name="T", constraints=(TypeRef(name="Sized"),)),),
        )
        engine = QueryEngine.from_graph(CodeGraph(nodes={"mod:Box": node}))

        assert engine.generic_parameters("Box") == [
            {
                "name": "T",
                "constraints": [{"name": "Sized", "module": None, "generic_args": []}],
                "default": None,
                "variance": None,
            }
        ]

    def test_type_references(self) -> None:
        node = CodeUnit(
            id="mod:make_box",
            name="make_box",
            kind=NodeKind.FUNCTION,
            location=_LOC,
            return_type=TypeRef(name="Box", generic_args=(TypeRef(name="int"),)),
        )
        engine = QueryEngine.from_graph(CodeGraph(nodes={"mod:make_box": node}))

        assert engine.type_references("make_box") == [
            {
                "name": "Box",
                "module": None,
                "generic_args": [
                    {"name": "int", "module": None, "generic_args": []},
                ],
            },
            {"name": "int", "module": None, "generic_args": []},
        ]


class TestQueryEngineAttackSurface:
    def test_attack_surface_exact(self) -> None:
        engine = _build_engine()
        surface = engine.attack_surface()
        assert len(surface) == 1
        ep = surface[0]
        assert ep["node_id"] == "entry"
        assert ep["trust_level"] == "untrusted_external"
        assert ep["kind"] == "user_input"
        assert ep["asset_value"] == AssetValue.LOW.value
        assert ep["asset_value"] == "low"
        assert ep["description"] is None
        assert set(ep.keys()) == {
            "node_id",
            "trust_level",
            "kind",
            "asset_value",
            "description",
        }

    def test_attack_surface_with_description(self) -> None:
        """Verify description is included when set."""
        nodes = {"ep": _make_node("ep", "ep")}
        graph = CodeGraph(
            nodes=nodes,
            edges=[],
            entrypoints={
                "ep": EntrypointTag(
                    kind=EntrypointKind.API,
                    trust_level=TrustLevel.TRUSTED_INTERNAL,
                    description="REST endpoint",
                ),
            },
        )
        engine = QueryEngine.from_graph(graph)
        surface = engine.attack_surface()
        assert len(surface) == 1
        ep = surface[0]
        assert ep["node_id"] == "ep"
        assert ep["trust_level"] == "trusted_internal"
        assert ep["kind"] == "api"
        assert ep["description"] == "REST endpoint"

    def test_attack_surface_empty(self) -> None:
        """No entrypoints means empty list."""
        graph = CodeGraph(
            nodes={"a": _make_node("a", "a")},
            edges=[],
        )
        engine = QueryEngine.from_graph(graph)
        assert engine.attack_surface() == []


class TestQueryEngineComplexity:
    def test_complexity_hotspots(self) -> None:
        engine = _build_engine()
        hotspots = engine.complexity_hotspots(threshold=10)
        assert len(hotspots) == 1
        assert hotspots[0]["name"] == "handler"
        assert hotspots[0]["cyclomatic_complexity"] == 12

    def test_complexity_multiple_sorted(self) -> None:
        """Multiple hotspots should be sorted descending."""
        nodes = {
            "a": _make_node("a", "a", complexity=5),
            "b": _make_node("b", "b", complexity=15),
            "c": _make_node("c", "c", complexity=10),
        }
        graph = CodeGraph(nodes=nodes, edges=[])
        engine = QueryEngine.from_graph(graph)
        hotspots = engine.complexity_hotspots(threshold=4)
        assert len(hotspots) == 3
        complexities = [h["cyclomatic_complexity"] for h in hotspots]
        assert complexities == [15, 10, 5]
        # Verify names match
        assert hotspots[0]["name"] == "b"
        assert hotspots[1]["name"] == "c"
        assert hotspots[2]["name"] == "a"

    def test_complexity_none_excluded(self) -> None:
        """Nodes with None complexity should not be included."""
        nodes = {
            "a": _make_node("a", "a", complexity=None),
            "b": _make_node("b", "b", complexity=10),
        }
        graph = CodeGraph(nodes=nodes, edges=[])
        engine = QueryEngine.from_graph(graph)
        hotspots = engine.complexity_hotspots(threshold=5)
        assert len(hotspots) == 1
        assert hotspots[0]["name"] == "b"

    def test_no_hotspots(self) -> None:
        engine = _build_engine()
        assert engine.complexity_hotspots(threshold=100) == []

    def test_complexity_default_threshold(self) -> None:
        """Default threshold is 10."""
        engine = _build_engine()
        hotspots = engine.complexity_hotspots()
        assert len(hotspots) == 1
        assert hotspots[0]["cyclomatic_complexity"] == 12


class TestQueryEngineSummary:
    def test_summary_exact(self) -> None:
        engine = _build_engine()
        s = engine.summary()
        assert s["total_nodes"] == 3
        assert s["functions"] == 3
        assert s["classes"] == 0
        assert s["proxies"] == 0
        assert s["call_edges"] == 2
        assert s["dependencies"] == ["flask", "sqlalchemy"]
        assert s["entrypoints"] == 1
        assert set(s.keys()) == {
            "total_nodes",
            "functions",
            "classes",
            "proxies",
            "call_edges",
            "dependencies",
            "entrypoints",
        }

    def test_summary_with_classes(self) -> None:
        """Verify classes are counted separately from functions."""
        nodes = {
            "f": _make_node("f", "f", kind=NodeKind.FUNCTION),
            "c": _make_node("c", "c", kind=NodeKind.CLASS),
            "m": _make_node("m", "m", kind=NodeKind.METHOD),
        }
        graph = CodeGraph(nodes=nodes, edges=[])
        engine = QueryEngine.from_graph(graph)
        s = engine.summary()
        assert s["total_nodes"] == 3
        assert s["functions"] == 2  # function + method
        assert s["classes"] == 1

    def test_summary_methods_counted_as_functions(self) -> None:
        """Methods should count in the functions total."""
        nodes = {
            "m": _make_node("m", "m", kind=NodeKind.METHOD),
        }
        graph = CodeGraph(nodes=nodes, edges=[])
        engine = QueryEngine.from_graph(graph)
        s = engine.summary()
        assert s["functions"] == 1
        assert s["classes"] == 0

    def test_summary_only_calls_counted(self) -> None:
        """Only CALLS edges should be in call_edges count."""
        nodes = {
            "a": _make_node("a", "a"),
            "b": _make_node("b", "b"),
        }
        edges = [
            CodeEdge(
                source_id="a",
                target_id="b",
                kind=EdgeKind.CALLS,
            ),
            CodeEdge(
                source_id="a",
                target_id="b",
                kind=EdgeKind.CONTAINS,
            ),
        ]
        graph = CodeGraph(nodes=nodes, edges=edges)
        engine = QueryEngine.from_graph(graph)
        s = engine.summary()
        assert s["call_edges"] == 1


class TestToJson:
    def test_to_json_structure(self) -> None:
        engine = _build_engine()
        data = json.loads(engine.to_json())
        assert set(data.keys()) == {
            "language",
            "root_path",
            "summary",
            "nodes",
            "edges",
            "subgraphs",
        }
        assert len(data["nodes"]) == 3
        assert len(data["edges"]) == 2

    def test_to_json_edge_dict_exact(self) -> None:
        """Each edge dict must have exact keys and values."""
        engine = _build_engine()
        data = json.loads(engine.to_json())
        for edge in data["edges"]:
            assert set(edge.keys()) == {
                "source",
                "target",
                "kind",
                "confidence",
            }
            assert edge["kind"] == "calls"
            assert edge["confidence"] == "certain"
        # Verify specific source/target
        sources = {e["source"] for e in data["edges"]}
        targets = {e["target"] for e in data["edges"]}
        assert "entry" in sources
        assert "handler" in sources
        assert "handler" in targets
        assert "db_query" in targets

    def test_to_json_node_dict_exact(self) -> None:
        """Each node dict must have kind as string value."""
        engine = _build_engine()
        data = json.loads(engine.to_json())
        for nid, node in data["nodes"].items():
            assert node["kind"] == "function"
            assert node["name"] == nid
            assert node["id"] == nid

    def test_to_json_language_and_root(self) -> None:
        """Language and root_path must be in output."""
        engine = _build_engine()
        data = json.loads(engine.to_json())
        assert data["language"] is not None
        assert "root_path" in data

    def test_to_json_summary_embedded(self) -> None:
        """Summary in JSON should match standalone summary."""
        engine = _build_engine()
        data = json.loads(engine.to_json())
        standalone = engine.summary()
        assert data["summary"]["total_nodes"] == standalone["total_nodes"]
        assert data["summary"]["functions"] == standalone["functions"]
        assert data["summary"]["call_edges"] == standalone["call_edges"]

    def test_to_json_indent_default(self) -> None:
        """Default indent is 2."""
        engine = _build_engine()
        output = engine.to_json()
        # Should be indented with 2 spaces
        assert "\n  " in output

    def test_to_json_indent_custom(self) -> None:
        engine = _build_engine()
        output = engine.to_json(indent=4)
        assert "\n    " in output

    def test_to_json_roundtrip(self) -> None:
        """JSON output should be valid and parseable."""
        engine = _build_engine()
        text = engine.to_json()
        data = json.loads(text)
        assert isinstance(data, dict)


class TestUnitToDict:
    def test_unit_to_dict_keys(self) -> None:
        """_unit_to_dict should include kind as string."""
        node = _make_node("x", "x", complexity=5)
        d = _unit_to_dict(node)
        assert d["id"] == "x"
        assert d["name"] == "x"
        assert d["kind"] == "function"
        assert d["cyclomatic_complexity"] == 5

    def test_unit_to_dict_class(self) -> None:
        node = _make_node("c", "c", kind=NodeKind.CLASS)
        d = _unit_to_dict(node)
        assert d["kind"] == "class"

    def test_unit_to_dict_method(self) -> None:
        node = _make_node("m", "m", kind=NodeKind.METHOD)
        d = _unit_to_dict(node)
        assert d["kind"] == "method"


class TestEdgeToDict:
    def test_edge_to_dict_calls(self) -> None:
        edge = CodeEdge(
            source_id="a",
            target_id="b",
            kind=EdgeKind.CALLS,
        )
        d = _edge_to_dict(edge)
        assert d == {
            "source": "a",
            "target": "b",
            "kind": "calls",
            "confidence": "certain",
        }

    def test_edge_to_dict_contains(self) -> None:
        edge = CodeEdge(
            source_id="mod",
            target_id="func",
            kind=EdgeKind.CONTAINS,
        )
        d = _edge_to_dict(edge)
        assert d["source"] == "mod"
        assert d["target"] == "func"
        assert d["kind"] == "contains"
        assert d["confidence"] == "certain"

    def test_edge_to_dict_inherits(self) -> None:
        from trailmark.models.edges import EdgeConfidence

        edge = CodeEdge(
            source_id="child",
            target_id="parent",
            kind=EdgeKind.INHERITS,
            confidence=EdgeConfidence.INFERRED,
        )
        d = _edge_to_dict(edge)
        assert d["source"] == "child"
        assert d["target"] == "parent"
        assert d["kind"] == "inherits"
        assert d["confidence"] == "inferred"

    def test_edge_to_dict_keys_exact(self) -> None:
        edge = CodeEdge(
            source_id="x",
            target_id="y",
            kind=EdgeKind.CALLS,
        )
        d = _edge_to_dict(edge)
        assert set(d.keys()) == {"source", "target", "kind", "confidence"}


class TestAnnotationToDict:
    def test_annotation_to_dict(self) -> None:
        from trailmark.models import Annotation

        ann = Annotation(
            kind=AnnotationKind.ASSUMPTION,
            description="x > 0",
            source="llm",
        )
        d = _annotation_to_dict(ann)
        assert d == {
            "kind": "assumption",
            "description": "x > 0",
            "source": "llm",
        }

    def test_annotation_to_dict_keys_exact(self) -> None:
        from trailmark.models import Annotation

        ann = Annotation(
            kind=AnnotationKind.PRECONDITION,
            description="test",
            source="manual",
        )
        d = _annotation_to_dict(ann)
        assert set(d.keys()) == {"kind", "description", "source"}


class TestQueryEngineAnnotate:
    def test_annotate_existing_node(self) -> None:
        engine = _build_engine()
        result = engine.annotate(
            "handler",
            AnnotationKind.ASSUMPTION,
            "input is sanitized",
            source="llm",
        )
        assert result is True

    def test_annotate_nonexistent_node(self) -> None:
        engine = _build_engine()
        result = engine.annotate(
            "nonexistent",
            AnnotationKind.ASSUMPTION,
            "test",
        )
        assert result is False

    def test_annotate_default_source(self) -> None:
        engine = _build_engine()
        engine.annotate(
            "handler",
            AnnotationKind.ASSUMPTION,
            "test",
        )
        anns = engine.annotations_of("handler")
        assert anns[0]["source"] == "manual"


class TestQueryEngineAnnotationsOf:
    def test_annotations_of_with_data(self) -> None:
        engine = _build_engine()
        engine.annotate(
            "handler",
            AnnotationKind.ASSUMPTION,
            "input is sanitized",
            source="llm",
        )
        anns = engine.annotations_of("handler")
        assert len(anns) == 1
        assert anns[0]["kind"] == "assumption"
        assert anns[0]["description"] == "input is sanitized"
        assert anns[0]["source"] == "llm"
        assert set(anns[0].keys()) == {"kind", "description", "source"}

    def test_annotations_of_filtered_by_kind(self) -> None:
        engine = _build_engine()
        engine.annotate("handler", AnnotationKind.ASSUMPTION, "a")
        engine.annotate("handler", AnnotationKind.INVARIANT, "b")
        anns = engine.annotations_of(
            "handler",
            kind=AnnotationKind.ASSUMPTION,
        )
        assert len(anns) == 1
        assert anns[0]["kind"] == "assumption"

    def test_annotations_of_nonexistent(self) -> None:
        engine = _build_engine()
        assert engine.annotations_of("nonexistent") == []

    def test_annotations_of_empty(self) -> None:
        engine = _build_engine()
        assert engine.annotations_of("handler") == []


class TestQueryEngineClearAnnotations:
    def test_clear_all(self) -> None:
        engine = _build_engine()
        engine.annotate("handler", AnnotationKind.ASSUMPTION, "a")
        engine.annotate("handler", AnnotationKind.INVARIANT, "b")
        assert engine.clear_annotations("handler") is True
        assert engine.annotations_of("handler") == []

    def test_clear_by_kind(self) -> None:
        engine = _build_engine()
        engine.annotate("handler", AnnotationKind.ASSUMPTION, "a")
        engine.annotate("handler", AnnotationKind.INVARIANT, "b")
        result = engine.clear_annotations(
            "handler",
            kind=AnnotationKind.ASSUMPTION,
        )
        assert result is True
        anns = engine.annotations_of("handler")
        assert len(anns) == 1
        assert anns[0]["kind"] == "invariant"

    def test_clear_nonexistent_node(self) -> None:
        engine = _build_engine()
        assert engine.clear_annotations("nonexistent") is False


class TestQueryEngineFromDirectory:
    def test_from_directory(self) -> None:
        engine = QueryEngine.from_directory("src/trailmark/models")
        s = engine.summary()
        assert s["total_nodes"] > 5
        assert s["classes"] > 0
