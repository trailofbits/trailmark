"""Tests for QueryEngine edge cases."""

from __future__ import annotations

import pytest

from trailmark.models import (
    CodeEdge,
    CodeGraph,
    CodeUnit,
    EdgeKind,
    NodeKind,
    SourceLocation,
)
from trailmark.query.api import QueryEngine

_LOC = SourceLocation(file_path="test.py", start_line=1, end_line=10)


def _make_node(node_id: str, name: str) -> CodeUnit:
    return CodeUnit(
        id=node_id,
        name=name,
        kind=NodeKind.FUNCTION,
        location=_LOC,
    )


def _simple_engine() -> QueryEngine:
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
    ]
    graph = CodeGraph(nodes=nodes, edges=edges)
    return QueryEngine.from_graph(graph)


class TestUnsupportedLanguage:
    def test_from_directory_bad_language(self) -> None:
        with pytest.raises(ValueError, match="Unsupported language"):
            QueryEngine.from_directory(".", language="cobol")


class TestCalleesOfMissing:
    def test_callees_of_nonexistent(self) -> None:
        engine = _simple_engine()
        assert engine.callees_of("nonexistent") == []


class TestPathsBetweenMissing:
    def test_paths_with_missing_src(self) -> None:
        engine = _simple_engine()
        assert engine.paths_between("zzz", "b") == []

    def test_paths_with_missing_dst(self) -> None:
        engine = _simple_engine()
        assert engine.paths_between("a", "zzz") == []


class TestProxyNodes:
    def test_from_graph_materializes_unresolved_call_proxy(self) -> None:
        graph = CodeGraph(
            nodes={"a": _make_node("a", "a")},
            edges=[CodeEdge("a", "missing:target", EdgeKind.CALLS)],
        )
        engine = QueryEngine.from_graph(graph)

        reachable = engine.reachable_from("a")
        ids = {node["id"] for node in reachable}
        assert ids == {"proxy.unresolved:missing:target"}
        proxy = reachable[0]
        assert proxy["kind"] == "proxy"
        assert proxy["origin"] == "proxy"
        assert proxy["attributes"]["raw_symbol"] == "missing:target"

    def test_unresolved_proxy_id_escapes_operator_characters(self) -> None:
        graph = CodeGraph(
            nodes={"a": _make_node("a", "a")},
            edges=[CodeEdge("a", "mod:func<T>", EdgeKind.CALLS)],
        )
        engine = QueryEngine.from_graph(graph)

        reachable = engine.reachable_from("a")
        assert reachable[0]["id"] == "proxy.unresolved:mod:func_T_"
        assert reachable[0]["attributes"]["raw_symbol"] == "mod:func<T>"
