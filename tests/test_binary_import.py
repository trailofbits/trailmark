"""Tests for importing external binary-analysis graphs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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


@pytest.mark.parametrize(
    "data",
    [
        None,
        {"artifact": []},
        {"functions": {}},
        {"functions": [None]},
        {"functions": [{"name": "f"}], "calls": {}},
        {
            "functions": [{"name": "f"}, {"name": "g"}],
            "calls": [{"source": "f", "target": "g"}, None],
        },
    ],
)
def test_invalid_binary_records_preserve_graph_and_indexes(tmp_path: Path, data: object) -> None:
    engine = _source_engine()
    path = tmp_path / "binary.json"
    path.write_text(
        json.dumps(
            {
                "artifact": {"key": "original"},
                "functions": [{"name": "main"}, {"name": "helper"}],
                "calls": [{"source": "main", "target": "helper"}],
            }
        )
    )
    engine.augment_binary(str(path))
    before = engine.to_json()
    reachable = engine.reachable_from("bin.original:main")
    assert {node["id"] for node in reachable} == {"bin.original:helper"}
    path.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="Binary graph"):
        engine.augment_binary(str(path))

    assert engine.to_json() == before
    assert engine.reachable_from("bin.original:main") == reachable


@pytest.mark.parametrize(
    "data",
    [
        {
            "functions": [{"name": "a+b", "address": 16}, {"name": "a/b", "address": 32}],
            "calls": [{"source": "a+b", "target": "a/b"}],
        },
        {
            "functions": [{"name": "main"}],
            "calls": [
                {"source": "main", "target": "a+b"},
                {"source": "main", "target": "a/b"},
            ],
        },
    ],
)
def test_binary_sanitized_symbol_collisions_preserve_existing_graph(
    tmp_path: Path, data: dict[str, object]
) -> None:
    engine = _source_engine()
    before = engine.to_json()
    path = tmp_path / "binary.json"
    path.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="collide at node ID"):
        engine.augment_binary(str(path))

    assert engine.to_json() == before
    assert engine.callees_of("main") == []


@pytest.mark.parametrize("collision", ["function", "artifact", "external"])
def test_binary_collisions_with_prior_import_preserve_graph(tmp_path: Path, collision: str) -> None:
    engine = _source_engine()
    path = tmp_path / "binary.json"
    original = {
        "artifact": {"key": "a+b" if collision == "artifact" else "app"},
        "functions": [{"name": "a+b" if collision == "function" else "main"}],
        "calls": [{"source": "main", "target": "a+b"}] if collision == "external" else [],
    }
    path.write_text(json.dumps(original))
    engine.augment_binary(str(path))
    before = engine.to_json()
    incoming_name = {"function": "a/b", "artifact": "other", "external": "main"}[collision]
    incoming = {
        "artifact": {"key": "a/b" if collision == "artifact" else "app"},
        "functions": [{"name": incoming_name}],
        "calls": [{"source": "main", "target": "a/b"}] if collision == "external" else [],
    }
    path.write_text(json.dumps(incoming))

    with pytest.raises(ValueError, match="collide at (node|artifact) ID"):
        engine.augment_binary(str(path))

    assert engine.to_json() == before


@pytest.mark.parametrize(
    ("field", "conflicting"), [("architecture", "arm64"), ("hash", "new-build")]
)
def test_conflicting_binary_build_metadata_preserves_prior_build(
    tmp_path: Path, field: str, conflicting: str
) -> None:
    engine = _source_engine()
    path = tmp_path / "binary.json"
    artifact = {"key": "lib", "architecture": "x86_64", "hash": "old-build"}
    path.write_text(
        json.dumps(
            {
                "artifact": artifact,
                "functions": [{"name": "f", "address": 16}, {"name": "old_helper", "address": 24}],
                "calls": [{"source": "f", "target": "old_helper", "callsite": 17}],
            }
        )
    )
    engine.augment_binary(str(path))
    before = engine.to_json()
    path.write_text(
        json.dumps(
            {
                "artifact": {**artifact, field: conflicting},
                "functions": [{"name": "f", "address": 64}, {"name": "new_helper", "address": 72}],
                "calls": [{"source": "f", "target": "new_helper", "callsite": 65}],
            }
        )
    )

    with pytest.raises(ValueError, match="Binary artifact 'lib' has conflicting"):
        engine.augment_binary(str(path))

    assert engine.to_json() == before
    assert {node["id"] for node in engine.callees_of("bin.lib:f")} == {"bin.lib:old_helper"}


def test_binary_metadata_can_be_enriched_and_survives_omission(tmp_path: Path) -> None:
    engine = _source_engine()
    path = tmp_path / "binary.json"
    for metadata in ({}, {"architecture": "x86_64", "hash": "known-build"}, {}):
        path.write_text(
            json.dumps(
                {
                    "artifact": {"key": "lib", **metadata},
                    "functions": [{"name": "f", "address": 16}],
                }
            )
        )
        engine.augment_binary(str(path))

    attrs = json.loads(engine.to_json())["nodes"]["bin.lib:f"]["attributes"]
    assert attrs["architecture"] == "x86_64"
    assert attrs["artifact_hash"] == "known-build"


@pytest.mark.parametrize("node_id", ["bin.server:main", "proxy.external:puts"])
def test_binary_namespaces_cannot_overwrite_source_nodes(tmp_path: Path, node_id: str) -> None:
    graph = CodeGraph(nodes={node_id: CodeUnit(node_id, "source", NodeKind.FUNCTION, _LOC)})
    engine = QueryEngine.from_graph(graph)
    before = engine.to_json()
    path = tmp_path / "binary.json"
    path.write_text(
        json.dumps(
            {
                "artifact": {"key": "server"},
                "functions": [{"name": "main"}],
                "calls": [{"source": "main", "target": "puts"}],
            }
        )
    )

    with pytest.raises(ValueError, match="collide at node ID"):
        engine.augment_binary(str(path))

    assert engine.to_json() == before


@pytest.mark.parametrize("reference", [{"name": "printf"}, {"symbol": "printf"}, {"id": "printf"}])
def test_dict_external_references_share_the_scalar_proxy(tmp_path: Path, reference: object) -> None:
    engine = _source_engine()
    path = tmp_path / "binary.json"
    path.write_text(
        json.dumps(
            {
                "artifact": {"key": "lib"},
                "functions": [{"name": "f"}],
                "calls": [
                    {"source": "f", "target": reference},
                    {"source": "f", "target": "printf"},
                ],
            }
        )
    )

    result = engine.augment_binary(str(path))
    exported = json.loads(engine.to_json())

    assert result["external_proxies"] == 1
    assert exported["nodes"]["proxy.external:printf"]["name"] == "printf"
    calls = [edge for edge in exported["edges"] if edge["kind"] == "calls"]
    assert len(calls) == 2
    assert {edge["target"] for edge in calls} == {"proxy.external:printf"}


@pytest.mark.parametrize("reference", [[], True, {"name": []}, {"address": {}}])
def test_invalid_call_reference_shapes_are_contextual_and_atomic(
    tmp_path: Path, reference: object
) -> None:
    engine = _source_engine()
    before = engine.to_json()
    path = tmp_path / "binary.json"
    path.write_text(
        json.dumps(
            {
                "functions": [{"name": "main"}],
                "calls": [{"source": "main", "target": reference}],
            }
        )
    )

    with pytest.raises(ValueError, match=r"Binary graph calls\[0\].target"):
        engine.augment_binary(str(path))

    assert engine.to_json() == before
