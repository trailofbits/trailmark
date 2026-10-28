"""Validate new native records without pre-existing identity conflicts hiding checks."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any

import pytest

from tests.test_binary_models import evidence
from trailmark.models import BinaryDiagnostic, CodeEdge, CodeGraph, EdgeKind, NodeKind, NodeOrigin
from trailmark.storage.graph_store import GraphStore


def _independent_image() -> CodeGraph:
    graph = evidence()
    binary = graph.nodes.pop("bin.image:sub_1000")
    binary = replace(binary, id="bin.second:helper")
    graph.nodes[binary.id] = binary
    artifact = replace(graph.binary_artifacts.pop("image"), id="second")
    graph.binary_artifacts[artifact.id] = artifact
    row = replace(
        graph.binary_ranges.pop("range"),
        id="second.range",
        artifact_id=artifact.id,
        node_id=binary.id,
    )
    graph.binary_ranges[row.id] = row
    graph.binary_source_mappings = {
        f"second.{key}": replace(mapping, id=f"second.{key}", range_id=row.id)
        for key, mapping in graph.binary_source_mappings.items()
    }
    graph.edges = [CodeEdge("a:helper", binary.id, EdgeKind.CORRESPONDS_TO)]
    graph.dependencies = ["new-native-dependency"]
    graph.subgraphs = {"new-native": {binary.id}}
    graph.source_file_counts = {"c": 17}
    return graph


@pytest.mark.parametrize(
    "collection,key,changes",
    [
        ("nodes", "bin.second:helper", {"id": "wrong-key"}),
        ("nodes", "a:helper", {"name": "collision"}),
        ("binary_artifacts", "second", {"id": "wrong-key"}),
        ("binary_artifacts", "second", {"sha256": "a" * 63}),
        ("binary_artifacts", "second", {"sha256": "g" * 64}),
        ("binary_ranges", "second.range", {"id": "wrong-key"}),
        ("binary_ranges", "second.range", {"artifact_id": "missing"}),
        ("binary_ranges", "second.range", {"node_id": "missing"}),
        ("binary_ranges", "second.range", {"node_id": "a:helper"}),
        ("nodes", "bin.second:helper", {"kind": NodeKind.CLASS}),
        ("binary_ranges", "second.range", {"start": 0xFFF, "rva": -1}),
        ("binary_ranges", "second.range", {"end": 0x1000}),
        ("binary_ranges", "second.range", {"end": 0xFFF}),
        ("binary_ranges", "second.range", {"rva": 1}),
        ("binary_ranges", "second.range", {"file_offset": -1}),
        ("binary_ranges", "second.range", {"file_offset": 49}),
        ("binary_source_mappings", "second.first", {"id": "wrong-key"}),
        ("binary_source_mappings", "second.first", {"range_id": "missing"}),
        ("binary_source_mappings", "second.first", {"start": 0xFFF}),
        ("binary_source_mappings", "second.first", {"end": 0x1011}),
        ("binary_source_mappings", "second.first", {"end": 0x1001}),
        ("binary_source_mappings", "second.first", {"end": 0x1000}),
        ("binary_source_mappings", "second.first", {"source_node_id": "missing"}),
        ("binary_source_mappings", "second.first", {"source_node_id": "bin.second:helper"}),
        ("binary_source_mappings", "second.first", {"method": "guessed"}),
        ("binary_source_mappings", "second.first", {"line": 0}),
        ("binary_source_mappings", "second.first", {"line": -1}),
    ],
)
def test_invalid_new_records_are_rejected_without_partial_commit(
    collection: str, key: str, changes: dict[str, Any]
) -> None:
    graph = evidence()
    store = GraphStore(graph)
    old_mappings = store.source_at_address("image", 0x1001)
    before = deepcopy(graph)
    incoming = _independent_image()
    rows = getattr(incoming, collection)
    rows[key] = replace(rows[key], **changes)
    incoming_before = deepcopy(incoming)
    with pytest.raises(ValueError):
        store.add_binary_graph(incoming)
    assert graph == before
    assert incoming == incoming_before
    assert store.source_at_address("image", 0x1001) == old_mappings
    assert store.binary_ranges_for_artifact("second") == []


@pytest.mark.parametrize("source,target", [("missing", "a:helper"), ("a:helper", "missing")])
def test_invalid_edge_endpoints_are_rejected_without_partial_commit(
    source: str, target: str
) -> None:
    graph = evidence()
    store = GraphStore(graph)
    before = deepcopy(graph)
    incoming = _independent_image()
    incoming.edges.append(CodeEdge(source, target, EdgeKind.CALLS))
    with pytest.raises(ValueError, match="unknown endpoint"):
        store.add_binary_graph(incoming)
    assert graph == before


@pytest.mark.parametrize("origin", [NodeOrigin.SOURCE, NodeOrigin.PROXY])
def test_binary_range_requires_a_binary_node(origin: NodeOrigin) -> None:
    graph = evidence()
    incoming = _independent_image()
    key = "bin.second:helper"
    incoming.nodes[key] = replace(incoming.nodes[key], origin=origin)
    with pytest.raises(ValueError, match="Invalid binary range"):
        GraphStore(graph).add_binary_graph(incoming)


def test_valid_independent_image_is_committed_and_reimported_once() -> None:
    graph = evidence()
    store = GraphStore(graph)
    incoming = _independent_image()
    store.add_binary_graph(incoming)
    assert set(graph.binary_artifacts) == {"image", "second"}
    assert set(graph.binary_ranges) == {"range", "second.range"}
    assert store.source_at_address("image", 0x1001) == [graph.binary_source_mappings["first"]]
    assert store.source_at_address("second", 0x1001) == [
        graph.binary_source_mappings["second.first"]
    ]
    assert graph.edges == incoming.edges
    before = deepcopy(graph)
    store.add_binary_graph(incoming)
    assert graph.nodes == before.nodes
    assert graph.edges == before.edges
    assert graph.binary_ranges == before.binary_ranges
    assert graph.binary_source_mappings == before.binary_source_mappings


def test_exact_slice_and_mapping_boundaries_are_valid() -> None:
    incoming = _independent_image()
    incoming.binary_artifacts["second"] = replace(
        incoming.binary_artifacts["second"], slice_offset=64, slice_size=16
    )
    incoming.binary_ranges["second.range"] = replace(
        incoming.binary_ranges["second.range"], file_offset=64
    )
    mapping = incoming.binary_source_mappings["second.first"]
    incoming.binary_source_mappings = {
        mapping.id: replace(mapping, start=0x1000, end=0x1010, line=1),
        "anonymous": replace(mapping, id="anonymous", source_node_id=None, line=None),
    }
    store = GraphStore(evidence())
    store.add_binary_graph(incoming)
    assert store.binary_ranges_for_artifact("second") == list(incoming.binary_ranges.values())
    assert store.source_at_address("second", 64, address_kind="file_offset") == [
        incoming.binary_source_mappings[mapping.id]
    ]


def test_reimport_merges_symbols_diagnostics_and_scanned_ranges() -> None:
    graph = evidence()
    store = GraphStore(graph)
    incoming = evidence()
    gap = BinaryDiagnostic("gap", "Unscanned bytes", 0x1010, 0x1020)
    incoming.binary_artifacts["image"] = replace(
        incoming.binary_artifacts["image"],
        diagnostics=(gap,),
        complete=False,
        scanned_ranges=((0x1000, 0x1010),),
    )
    incoming.binary_ranges["range"] = replace(incoming.binary_ranges["range"], symbols=("one",))
    store.add_binary_graph(incoming)
    incoming.binary_ranges["range"] = replace(incoming.binary_ranges["range"], symbols=("two",))
    incoming.binary_artifacts["image"] = replace(
        incoming.binary_artifacts["image"], scanned_ranges=((0x1020, 0x1030),)
    )
    store.add_binary_graph(incoming)
    assert graph.binary_ranges["range"].symbols == ("one", "two")
    artifact = graph.binary_artifacts["image"]
    assert artifact.diagnostics == (gap,)
    assert artifact.complete is False
    assert artifact.scanned_ranges == ((0x1000, 0x1010), (0x1020, 0x1030))
    before = deepcopy(graph)
    store.add_binary_graph(incoming)
    assert graph == before


def test_empty_binary_range_without_mappings_is_invalid() -> None:
    incoming = _independent_image()
    incoming.binary_source_mappings.clear()
    incoming.binary_ranges["second.range"] = replace(
        incoming.binary_ranges["second.range"], end=0x1000
    )
    with pytest.raises(ValueError, match="Invalid binary range"):
        GraphStore(evidence()).add_binary_graph(incoming)
