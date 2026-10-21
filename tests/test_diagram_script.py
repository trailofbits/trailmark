"""Tests for the diagram generation module."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from itertools import pairwise

import pytest

from trailmark import cli, diagram
from trailmark.models import (
    CodeEdge,
    CodeGraph,
    CodeUnit,
    EdgeConfidence,
    EdgeKind,
    EntrypointKind,
    EntrypointTag,
    NodeKind,
    NodeOrigin,
    SourceLocation,
    TrustLevel,
)
from trailmark.query.api import QueryEngine

_LOC = SourceLocation(file_path="test.py", start_line=1, end_line=10)


def _make_node(
    node_id: str,
    name: str,
    kind: NodeKind = NodeKind.FUNCTION,
    complexity: int | None = None,
) -> CodeUnit:
    return CodeUnit(
        id=node_id,
        name=name,
        kind=kind,
        location=_LOC,
        cyclomatic_complexity=complexity,
    )


def _build_call_graph_engine() -> QueryEngine:
    """Engine with call chain: entry -> handler -> db_query."""
    nodes = {
        "mod:entry": _make_node("mod:entry", "entry", complexity=2),
        "mod:handler": _make_node("mod:handler", "handler", complexity=12),
        "mod:db_query": _make_node("mod:db_query", "db_query", complexity=3),
    }
    edges = [
        CodeEdge(
            source_id="mod:entry",
            target_id="mod:handler",
            kind=EdgeKind.CALLS,
        ),
        CodeEdge(
            source_id="mod:handler",
            target_id="mod:db_query",
            kind=EdgeKind.CALLS,
        ),
    ]
    graph = CodeGraph(
        nodes=nodes,
        edges=edges,
        entrypoints={
            "mod:entry": EntrypointTag(
                kind=EntrypointKind.USER_INPUT,
                trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
            ),
        },
    )
    return QueryEngine.from_graph(graph)


def _build_class_hierarchy_engine() -> QueryEngine:
    """Engine with inheritance: Child inherits Parent, Impl implements Iface."""
    nodes = {
        "mod:Parent": _make_node("mod:Parent", "Parent", kind=NodeKind.CLASS),
        "mod:Child": _make_node("mod:Child", "Child", kind=NodeKind.CLASS),
        "mod:Iface": _make_node("mod:Iface", "Iface", kind=NodeKind.INTERFACE),
        "mod:Impl": _make_node("mod:Impl", "Impl", kind=NodeKind.CLASS),
    }
    edges = [
        CodeEdge(
            source_id="mod:Child",
            target_id="mod:Parent",
            kind=EdgeKind.INHERITS,
        ),
        CodeEdge(
            source_id="mod:Impl",
            target_id="mod:Iface",
            kind=EdgeKind.IMPLEMENTS,
        ),
    ]
    graph = CodeGraph(nodes=nodes, edges=edges)
    return QueryEngine.from_graph(graph)


def _build_containment_engine() -> QueryEngine:
    """Engine with a class containing methods."""
    nodes = {
        "mod:MyClass": _make_node("mod:MyClass", "MyClass", kind=NodeKind.CLASS),
        "mod:MyClass.do_thing": _make_node(
            "mod:MyClass.do_thing", "do_thing", kind=NodeKind.METHOD
        ),
        "mod:MyClass.other": _make_node("mod:MyClass.other", "other", kind=NodeKind.METHOD),
    }
    edges = [
        CodeEdge(
            source_id="mod:MyClass",
            target_id="mod:MyClass.do_thing",
            kind=EdgeKind.CONTAINS,
        ),
        CodeEdge(
            source_id="mod:MyClass",
            target_id="mod:MyClass.other",
            kind=EdgeKind.CONTAINS,
        ),
    ]
    graph = CodeGraph(nodes=nodes, edges=edges)
    return QueryEngine.from_graph(graph)


def _build_module_deps_engine() -> QueryEngine:
    """Engine with import edges between modules."""
    nodes = {
        "mod_a": _make_node("mod_a", "mod_a", kind=NodeKind.MODULE),
        "mod_b": _make_node("mod_b", "mod_b", kind=NodeKind.MODULE),
        "mod_c": _make_node("mod_c", "mod_c", kind=NodeKind.MODULE),
    }
    edges = [
        CodeEdge(
            source_id="mod_a",
            target_id="mod_b",
            kind=EdgeKind.IMPORTS,
        ),
        CodeEdge(
            source_id="mod_b",
            target_id="mod_c",
            kind=EdgeKind.IMPORTS,
        ),
    ]
    graph = CodeGraph(nodes=nodes, edges=edges)
    return QueryEngine.from_graph(graph)


# ── Utility function tests ───────────────────────────────────────


class TestSanitizeId:
    def test_dots_and_colons(self) -> None:
        assert diagram.sanitize_id("mod:Class.method") == "mod_Class_method"

    def test_leading_digit(self) -> None:
        assert diagram.sanitize_id("3rdparty:init") == "n_3rdparty_init"

    def test_already_safe(self) -> None:
        assert diagram.sanitize_id("simple_name") == "simple_name"

    def test_empty_string(self) -> None:
        assert diagram.sanitize_id("") == "n_empty"

    def test_slashes_and_hyphens(self) -> None:
        assert diagram.sanitize_id("a/b-c") == "a_b_c"


class TestNodeLabel:
    def test_basic(self) -> None:
        label = diagram.node_label({"name": "foo", "kind": "function"})
        assert label == "foo, function"

    def test_with_complexity(self) -> None:
        label = diagram.node_label({"name": "bar", "kind": "method", "cyclomatic_complexity": 15})
        assert label == "bar, method, CC=15"

    def test_no_complexity(self) -> None:
        label = diagram.node_label({"name": "baz", "kind": "function"})
        assert label == "baz, function"

    def test_fallback_to_id(self) -> None:
        label = diagram.node_label({"id": "mod:thing"})
        assert label == "mod:thing"

    def test_no_name_no_id(self) -> None:
        label = diagram.node_label({})
        assert label == "?"

    def test_no_kind_omits_kind(self) -> None:
        label = diagram.node_label({"name": "solo"})
        assert label == "solo"

    def test_name_only_with_cc(self) -> None:
        label = diagram.node_label({"name": "x", "cyclomatic_complexity": 3})
        assert label == "x, CC=3"


class TestMermaidLabel:
    def test_escapes_double_quotes(self) -> None:
        label = diagram.mermaid_label({"name": 'rs.getString("id").trim', "kind": "proxy"})
        assert label == "rs.getString(#quot;id#quot;).trim, proxy"

    def test_matches_node_label_without_quotes(self) -> None:
        node = {"name": "bar", "kind": "method", "cyclomatic_complexity": 15}
        assert diagram.mermaid_label(node) == diagram.node_label(node)

    def test_node_label_is_unescaped(self) -> None:
        assert diagram.node_label({"name": 'f("x")', "kind": "proxy"}) == 'f("x"), proxy'


class TestEdgeStyle:
    def test_certain(self) -> None:
        assert diagram.edge_style("certain") == "-->"

    def test_inferred(self) -> None:
        assert diagram.edge_style("inferred") == "-.->"

    def test_uncertain(self) -> None:
        assert diagram.edge_style("uncertain") == "-..->"


class TestComplexityClass:
    def test_none(self) -> None:
        assert diagram.complexity_class(None) == "low"

    def test_low(self) -> None:
        assert diagram.complexity_class(3) == "low"

    def test_medium(self) -> None:
        assert diagram.complexity_class(7) == "medium"

    def test_high(self) -> None:
        assert diagram.complexity_class(15) == "high"

    def test_boundary_5(self) -> None:
        assert diagram.complexity_class(5) == "medium"

    def test_boundary_10(self) -> None:
        assert diagram.complexity_class(10) == "medium"

    def test_boundary_11(self) -> None:
        assert diagram.complexity_class(11) == "high"


# ── Emitter tests ────────────────────────────────────────────────


class TestEmitCallGraph:
    def test_with_focus(self) -> None:
        engine = _build_call_graph_engine()
        result = diagram.emit_call_graph(engine, "handler", 2, "TB")
        assert result.startswith("flowchart TB")
        assert "mod_handler" in result
        assert "-->" in result

    def test_without_focus(self) -> None:
        engine = _build_call_graph_engine()
        result = diagram.emit_call_graph(engine, None, 2, "TB")
        assert result.startswith("flowchart TB")
        assert "mod_entry" in result
        assert "mod_db_query" in result

    def test_direction_lr(self) -> None:
        engine = _build_call_graph_engine()
        result = diagram.emit_call_graph(engine, None, 2, "LR")
        assert result.startswith("flowchart LR")


class TestEmitClassHierarchy:
    def test_inherits_and_implements(self) -> None:
        engine = _build_class_hierarchy_engine()
        result = diagram.emit_class_hierarchy(engine, "TB")
        assert "classDiagram" in result
        assert "<|--" in result
        assert "<|.." in result

    def test_no_classes(self) -> None:
        engine = _build_call_graph_engine()
        result = diagram.emit_class_hierarchy(engine, "TB")
        assert "No classes found" in result


class TestEmitModuleDeps:
    def test_imports(self) -> None:
        engine = _build_module_deps_engine()
        result = diagram.emit_module_deps(engine, "LR")
        assert result.startswith("flowchart LR")
        assert "mod_a" in result
        assert "mod_b" in result

    def test_no_imports(self) -> None:
        engine = _build_call_graph_engine()
        result = diagram.emit_module_deps(engine, "TB")
        assert "No import edges found" in result


class TestEmitContainment:
    def test_class_members(self) -> None:
        engine = _build_containment_engine()
        result = diagram.emit_containment(engine, "TB")
        assert "classDiagram" in result
        assert "do_thing" in result
        assert "other" in result

    def test_no_containment(self) -> None:
        engine = _build_module_deps_engine()
        result = diagram.emit_containment(engine, "TB")
        assert "No containment found" in result


@pytest.mark.parametrize(
    ("emitter", "build"),
    [
        (diagram.emit_class_hierarchy, _build_class_hierarchy_engine),
        (diagram.emit_containment, _build_containment_engine),
    ],
    ids=["class-hierarchy", "containment"],
)
@pytest.mark.parametrize("direction", ["TB", "LR"])
def test_class_diagrams_honor_direction(
    emitter: Callable[[QueryEngine, str], str], build: Callable[[], QueryEngine], direction: str
) -> None:
    result = emitter(build(), direction)
    assert result.splitlines()[:2] == ["classDiagram", f"    direction {direction}"]


@pytest.mark.parametrize("emitter", [diagram.emit_class_hierarchy, diagram.emit_containment])
@pytest.mark.parametrize("class_count", [100, 101])
def test_class_diagram_size_warning_counts_rendered_classes(
    emitter: Callable[[QueryEngine, str], str],
    class_count: int,
    capsys: pytest.CaptureFixture[str],
) -> None:
    graph = CodeGraph()
    for i in range(class_count):
        owner = f"mod:C{i}"
        member = f"{owner}.method"
        graph.nodes[owner] = _make_node(owner, f"C{i}", NodeKind.CLASS)
        graph.nodes[member] = _make_node(member, "method", NodeKind.METHOD)
        graph.edges.append(CodeEdge(owner, member, EdgeKind.CONTAINS))
    graph.nodes["mod:unrelated"] = _make_node("mod:unrelated", "unrelated")
    result = emitter(QueryEngine.from_graph(graph), "LR")
    assert result.count("    class ") == class_count
    stderr = capsys.readouterr().err
    if class_count > 100:
        assert "101 nodes exceeds 100" in stderr
    else:
        assert stderr == ""


class TestEmitComplexity:
    def test_above_threshold(self) -> None:
        engine = _build_call_graph_engine()
        result = diagram.emit_complexity(engine, 10, "TB")
        assert result.startswith("flowchart TB")
        assert "handler" in result
        assert ":::high" in result
        assert "classDef high" in result

    def test_below_threshold(self) -> None:
        engine = _build_call_graph_engine()
        result = diagram.emit_complexity(engine, 20, "TB")
        assert "No nodes with CC >= 20" in result


class TestEmitDataFlow:
    def test_focus_uses_the_same_declaration_ranking_as_queries(self) -> None:
        units = [
            _make_node("mod:C.check", "C.check"),
            CodeUnit(
                id="proxy.unresolved:check",
                name="check",
                kind=NodeKind.PROXY,
                location=_LOC,
                origin=NodeOrigin.PROXY,
            ),
            _make_node("mod:check", "check"),
        ]
        engine = QueryEngine.from_graph(CodeGraph(nodes={unit.id: unit for unit in units}))
        graph = json.loads(engine.to_json())
        for reference in ("check", "C.check", "proxy.unresolved:check", "missing"):
            expected = engine._store.find_node_id(reference)  # noqa: SLF001
            assert diagram._find_focus_node(graph, reference) == expected
        assert diagram._find_focus_node(graph, "check") == "mod:check"

    @pytest.mark.parametrize("reverse_order", [False, True])
    def test_preserves_weakest_parallel_call_confidence(self, reverse_order: bool) -> None:
        nodes = {name: _make_node(name, name) for name in ("entry", "middle", "sink")}
        parallel = [
            CodeEdge("entry", "middle", EdgeKind.CALLS, EdgeConfidence.UNCERTAIN),
            CodeEdge("entry", "middle", EdgeKind.CALLS, EdgeConfidence.CERTAIN),
        ]
        if reverse_order:
            parallel.reverse()
        graph = CodeGraph(
            nodes=nodes,
            edges=[
                *parallel,
                CodeEdge("entry", "middle", EdgeKind.CONTAINS),
                CodeEdge("middle", "sink", EdgeKind.CALLS, EdgeConfidence.INFERRED),
            ],
            entrypoints={"entry": EntrypointTag(kind=EntrypointKind.API)},
        )
        engine = QueryEngine.from_graph(graph)

        result = diagram.emit_data_flow(engine, "sink", 2, "TB")

        assert "entry -..-> middle" in result
        assert "middle -.-> sink" in result
        assert "entry --> middle" not in result
        assert len(graph.edges) == 4

    def test_depth_bounds_the_number_of_edges(self) -> None:
        engine = _build_call_graph_engine()
        assert "No paths" in diagram.emit_data_flow(engine, "db_query", 1, "TB")
        assert "mod_handler --> mod_db_query" in diagram.emit_data_flow(engine, "db_query", 2, "TB")

    @pytest.mark.parametrize("depth", [0, -1])
    def test_nonpositive_depth_rejected_even_without_entrypoints(self, depth: int) -> None:
        with pytest.raises(ValueError, match="depth.*at least 1"):
            diagram.emit_data_flow(_build_class_hierarchy_engine(), None, depth, "TB")

    def test_with_focus(self) -> None:
        engine = _build_call_graph_engine()
        result = diagram.emit_data_flow(engine, "db_query", 5, "TB")
        assert result.startswith("flowchart TB")
        assert "entrypoint" in result

    def test_without_focus(self) -> None:
        engine = _build_call_graph_engine()
        result = diagram.emit_data_flow(engine, None, 5, "TB")
        assert result.startswith("flowchart TB")

    def test_no_entrypoints(self) -> None:
        engine = _build_class_hierarchy_engine()
        result = diagram.emit_data_flow(engine, None, 5, "TB")
        assert "No entrypoints found" in result


def _build_quoted_name_engine() -> QueryEngine:
    """Engine whose nodes have ``"`` in their names, like call-text proxies."""
    nodes = {
        "mod:entry": _make_node("mod:entry", "entry", complexity=2),
        "mod:load": _make_node("mod:load", 'load("users")', complexity=12),
    }
    edges = [CodeEdge(source_id="mod:entry", target_id="mod:load", kind=EdgeKind.CALLS)]
    graph = CodeGraph(
        nodes=nodes,
        edges=edges,
        entrypoints={"mod:entry": EntrypointTag(kind=EntrypointKind.USER_INPUT)},
    )
    return QueryEngine.from_graph(graph)


def _quoted_labels(result: str) -> list[str]:
    """Text between ``["`` and ``"]`` on each node line."""
    return [
        line.split('["', 1)[1].rsplit('"]', 1)[0] for line in result.splitlines() if '["' in line
    ]


class TestQuotedNodeNames:
    @pytest.mark.parametrize(
        "render",
        [
            lambda e: diagram.emit_call_graph(e, "entry", 2, "TB"),
            lambda e: diagram.emit_call_graph(e, None, 2, "TB"),
            lambda e: diagram.emit_complexity(e, 1, "TB"),
            lambda e: diagram.emit_data_flow(e, "mod:load", 5, "TB"),
        ],
        ids=["call-graph-focus", "call-graph", "complexity", "data-flow"],
    )
    def test_labels_never_contain_raw_quotes(self, render: Callable[[QueryEngine], str]) -> None:
        result = render(_build_quoted_name_engine())
        labels = _quoted_labels(result)
        assert any("#quot;users#quot;" in label for label in labels)
        assert all('"' not in label for label in labels)


# ── CLI argument parsing ─────────────────────────────────────────


class TestParseArgs:
    def test_required_args(self) -> None:
        args = diagram.parse_args(["--target", "/some/dir", "--type", "call-graph"])
        assert args.target == "/some/dir"
        assert args.diagram_type == "call-graph"
        assert args.language == "python"
        assert args.direction == "TB"
        assert args.depth is None

    def test_all_options(self) -> None:
        args = diagram.parse_args(
            [
                "--target",
                "/src",
                "--language",
                "rust",
                "--type",
                "complexity",
                "--focus",
                "parse",
                "--depth",
                "3",
                "--direction",
                "LR",
                "--threshold",
                "5",
            ]
        )
        assert args.language == "rust"
        assert args.diagram_type == "complexity"
        assert args.focus == "parse"
        assert args.depth == 3
        assert args.direction == "LR"
        assert args.threshold == 5

    def test_invalid_type_rejected(self) -> None:
        with pytest.raises(SystemExit):
            diagram.parse_args(["--target", "/some/dir", "--type", "invalid"])


@pytest.mark.parametrize("subcommand", [False, True])
@pytest.mark.parametrize(
    ("diagram_type", "depth", "edge_count", "includes_sink"),
    [
        ("call-graph", None, 3, False),
        ("call-graph", 3, 3, True),
        ("data-flow", None, 3, True),
        ("data-flow", None, 20, True),
        ("data-flow", None, 21, False),
        ("data-flow", 2, 3, False),
        ("data-flow", 3, 3, True),
    ],
)
def test_cli_depth_defaults_depend_on_diagram_type(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    subcommand: bool,
    diagram_type: str,
    depth: int | None,
    edge_count: int,
    includes_sink: bool,
) -> None:
    ids = ["mod:entry", *(f"mod:n{i}" for i in range(edge_count - 1)), "mod:sink"]
    graph = CodeGraph(
        nodes={
            node_id: _make_node(
                node_id, node_id.split(":")[1], complexity=13 if i == edge_count else 1
            )
            for i, node_id in enumerate(ids)
        },
        edges=[CodeEdge(src, dst, EdgeKind.CALLS) for src, dst in pairwise(ids)],
        entrypoints={
            ids[0]: EntrypointTag(
                kind=EntrypointKind.USER_INPUT,
                trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
            )
        },
    )
    engine = QueryEngine.from_graph(graph)
    args = ["--target", "/unused", "--type", diagram_type]
    if diagram_type == "call-graph":
        args.extend(["--focus", "entry"])
    if depth is not None:
        args.extend(["--depth", str(depth)])
    if subcommand:
        monkeypatch.setattr(
            cli, "build_engine", lambda target, language, *, allow_empty=False: engine
        )
        monkeypatch.setattr(sys, "argv", ["trailmark", "diagram", *args])
        cli.main()
    else:
        monkeypatch.setattr(
            diagram, "build_engine", lambda target, language, *, allow_empty=False: engine
        )
        assert diagram.main(args) == 0

    assert ("mod_sink" in capsys.readouterr().out) is includes_sink


# ── Large graph warning ──────────────────────────────────────────


class TestWarnIfLarge:
    def test_no_warning_for_small(self, capsys: pytest.CaptureFixture[str]) -> None:
        diagram._warn_if_large({"a": 1, "b": 2})
        assert capsys.readouterr().err == ""

    def test_warning_for_large(self, capsys: pytest.CaptureFixture[str]) -> None:
        big = {str(i): i for i in range(150)}
        diagram._warn_if_large(big)
        assert "150 nodes exceeds 100" in capsys.readouterr().err
