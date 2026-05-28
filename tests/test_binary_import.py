"""Tests for importing external binary-analysis graphs."""

from __future__ import annotations

import json
from pathlib import Path

from trailmark.models import CodeGraph, CodeUnit, NodeKind, SourceLocation
from trailmark.query.api import QueryEngine

_LOC = SourceLocation(file_path="src/main.c", start_line=1, end_line=20)


def _source_engine() -> QueryEngine:
    graph = CodeGraph(
        nodes={
            "src.main:main": CodeUnit(
                id="src.main:main",
                name="main",
                kind=NodeKind.FUNCTION,
                location=_LOC,
            )
        },
        root_path=".",
    )
    return QueryEngine.from_graph(graph)


def test_augment_binary_imports_nodes_calls_and_correspondence(tmp_path: Path) -> None:
    binary_graph = {
        "artifact": {
            "key": "app",
            "architecture": "x86_64",
            "hash": "abc123",
            "path": "build/app",
        },
        "functions": [
            {
                "name": "main",
                "address": "0x1000",
                "size": 32,
                "section": ".text",
                "source": {"symbol": "main"},
            },
            {"name": "helper", "address": "0x1040", "size": 16},
        ],
        "calls": [
            {"source": "main", "target": "helper", "confidence": "certain"},
            {"source": "helper", "target": "puts", "confidence": "uncertain"},
        ],
    }
    graph_path = tmp_path / "binary.json"
    graph_path.write_text(json.dumps(binary_graph))

    engine = _source_engine()
    result = engine.augment_binary(str(graph_path))

    assert result["binary_nodes"] == 2
    assert result["call_edges"] == 2
    assert result["external_proxies"] == 1
    assert result["correspondences"] == 1
    assert "binary:app" in engine.subgraph_names()

    data = json.loads(engine.to_json())
    assert "bin.app:main" in data["nodes"]
    assert data["nodes"]["bin.app:main"]["origin"] == "binary"
    assert data["nodes"]["bin.app:main"]["attributes"]["architecture"] == "x86_64"
    assert "proxy.external:puts" in data["nodes"]
    assert any(edge["kind"] == "corresponds_to" for edge in data["edges"])


def test_duplicate_binary_symbols_fall_back_to_rva_ids(tmp_path: Path) -> None:
    binary_graph = {
        "artifact": {"key": "dup"},
        "functions": [
            {"name": "thunk", "rva": 16},
            {"name": "thunk", "rva": 32},
        ],
        "calls": [],
    }
    graph_path = tmp_path / "binary.json"
    graph_path.write_text(json.dumps(binary_graph))

    engine = _source_engine()
    engine.augment_binary(str(graph_path), connect_sources=False)
    data = json.loads(engine.to_json())

    assert "bin.dup:sub_10" in data["nodes"]
    assert "bin.dup:sub_20" in data["nodes"]


def test_duplicate_binary_symbols_do_not_resolve_by_ambiguous_name(tmp_path: Path) -> None:
    binary_graph = {
        "artifact": {"key": "dup"},
        "functions": [
            {"name": "thunk", "rva": 16},
            {"name": "thunk", "rva": 32},
        ],
        "calls": [{"source": {"rva": 16}, "target": "thunk"}],
    }
    graph_path = tmp_path / "binary.json"
    graph_path.write_text(json.dumps(binary_graph))

    engine = QueryEngine.from_graph(CodeGraph())
    engine.augment_binary(str(graph_path), connect_sources=False)
    calls = [edge for edge in json.loads(engine.to_json())["edges"] if edge["kind"] == "calls"]

    assert calls == [
        {
            "source": "bin.dup:sub_10",
            "target": "proxy.external:thunk",
            "kind": "calls",
            "confidence": "certain",
        }
    ]


def test_duplicate_binary_aliases_do_not_resolve_by_ambiguous_name(tmp_path: Path) -> None:
    binary_graph = {
        "artifact": {"key": "dup"},
        "functions": [
            {"symbol": "_Z1av", "name": "shared", "rva": 16},
            {"symbol": "_Z1bv", "name": "shared", "rva": 32},
        ],
        "calls": [{"source": "_Z1av", "target": "shared"}],
    }
    graph_path = tmp_path / "binary.json"
    graph_path.write_text(json.dumps(binary_graph))

    engine = QueryEngine.from_graph(CodeGraph())
    engine.augment_binary(str(graph_path), connect_sources=False)
    calls = [edge for edge in json.loads(engine.to_json())["edges"] if edge["kind"] == "calls"]

    assert calls == [
        {
            "source": "bin.dup:_Z1av",
            "target": "proxy.external:shared",
            "kind": "calls",
            "confidence": "certain",
        }
    ]


def test_binary_file_line_source_mapping_prefers_tightest_node(tmp_path: Path) -> None:
    graph = CodeGraph(
        nodes={
            "src.main": CodeUnit(
                id="src.main",
                name="src.main",
                kind=NodeKind.MODULE,
                location=SourceLocation(
                    file_path="src/main.c",
                    start_line=1,
                    end_line=100,
                ),
            ),
            "src.main:main": CodeUnit(
                id="src.main:main",
                name="main",
                kind=NodeKind.FUNCTION,
                location=SourceLocation(
                    file_path="src/main.c",
                    start_line=10,
                    end_line=20,
                ),
            ),
        },
        root_path=".",
    )
    binary_graph = {
        "artifact": {"key": "app"},
        "functions": [
            {
                "name": "main",
                "rva": 16,
                "source": {"file": "src/main.c", "line": 12},
            }
        ],
        "calls": [],
    }
    graph_path = tmp_path / "binary.json"
    graph_path.write_text(json.dumps(binary_graph))

    engine = QueryEngine.from_graph(graph)
    engine.augment_binary(str(graph_path))
    correspondences = [
        edge for edge in json.loads(engine.to_json())["edges"] if edge["kind"] == "corresponds_to"
    ]

    assert correspondences[0]["source"] == "src.main:main"


def test_binary_symbol_source_mapping_prefers_function_over_container(
    tmp_path: Path,
) -> None:
    graph = CodeGraph(
        nodes={
            "src.main": CodeUnit(
                id="src.main",
                name="src.main",
                kind=NodeKind.MODULE,
                location=SourceLocation(
                    file_path="src/main.c",
                    start_line=1,
                    end_line=100,
                ),
            ),
            "src.main:main": CodeUnit(
                id="src.main:main",
                name="main",
                kind=NodeKind.FUNCTION,
                location=SourceLocation(
                    file_path="src/main.c",
                    start_line=10,
                    end_line=20,
                ),
            ),
        },
        root_path=".",
    )
    binary_graph = {
        "artifact": {"key": "app"},
        "functions": [
            {
                "name": "main",
                "rva": 16,
                "source": {"symbol": "main"},
            }
        ],
        "calls": [],
    }
    graph_path = tmp_path / "binary.json"
    graph_path.write_text(json.dumps(binary_graph))

    engine = QueryEngine.from_graph(graph)
    engine.augment_binary(str(graph_path))
    correspondences = [
        edge for edge in json.loads(engine.to_json())["edges"] if edge["kind"] == "corresponds_to"
    ]

    assert correspondences[0]["source"] == "src.main:main"


def test_binary_symbol_source_mapping_skips_ambiguous_functions(
    tmp_path: Path,
) -> None:
    graph = CodeGraph(
        nodes={
            "a:main": CodeUnit(
                id="a:main",
                name="main",
                kind=NodeKind.FUNCTION,
                location=SourceLocation(file_path="a.c", start_line=1, end_line=5),
            ),
            "b:main": CodeUnit(
                id="b:main",
                name="main",
                kind=NodeKind.FUNCTION,
                location=SourceLocation(file_path="b.c", start_line=1, end_line=5),
            ),
        }
    )
    binary_graph = {
        "artifact": {"key": "app"},
        "functions": [
            {
                "name": "main",
                "rva": 16,
                "source": {"symbol": "main"},
            }
        ],
        "calls": [],
    }
    graph_path = tmp_path / "binary.json"
    graph_path.write_text(json.dumps(binary_graph))

    engine = QueryEngine.from_graph(graph)
    result = engine.augment_binary(str(graph_path))
    correspondences = [
        edge for edge in json.loads(engine.to_json())["edges"] if edge["kind"] == "corresponds_to"
    ]

    assert result["correspondences"] == 0
    assert correspondences == []
