"""Snapshot vectors for real binary evidence and generated proxy locations."""

from dataclasses import replace

import pytest

from tests.test_binary_models import evidence
from trailmark.models import (
    CodeEdge,
    CodeGraph,
    CodeUnit,
    EdgeKind,
    NodeKind,
    NodeOrigin,
    SourceLocation,
)
from trailmark.query.api import QueryEngine


def test_snapshot_reloads_binary_nodes_with_unknown_source_lines() -> None:
    engine = QueryEngine.from_graph(evidence())
    restored = QueryEngine.from_snapshot(engine.to_snapshot())
    assert restored.binary_artifacts() == engine.binary_artifacts()
    assert restored.binary_ranges("a:helper") == engine.binary_ranges("a:helper")
    assert restored.source_at_address("image", 0x1001) == engine.source_at_address("image", 0x1001)
    binary = restored._store.unit("bin.image:sub_1000")
    assert binary is not None
    assert binary.location == SourceLocation("image", 0, 0)


def test_snapshot_reloads_generated_proxy_locations() -> None:
    owner = CodeUnit("app:owner", "owner", NodeKind.FUNCTION, SourceLocation("app.py", 1, 1))
    engine = QueryEngine.from_graph(
        CodeGraph(
            nodes={owner.id: owner}, edges=[CodeEdge(owner.id, "external:callee", EdgeKind.CALLS)]
        )
    )
    restored = QueryEngine.from_snapshot(engine.to_snapshot())
    assert restored.unresolved_calls() == engine.unresolved_calls()
    assert restored.callees_of("owner") == engine.callees_of("owner")


def test_snapshot_reloads_unlocated_synthetic_nodes() -> None:
    node = CodeUnit(
        "synthetic",
        "synthetic",
        NodeKind.FUNCTION,
        SourceLocation("", 0, 0),
        origin=NodeOrigin.SYNTHETIC,
    )
    graph = CodeGraph(nodes={node.id: node})
    assert CodeGraph.from_snapshot(graph.to_snapshot()).nodes[node.id] == node


@pytest.mark.parametrize(
    "location",
    [
        SourceLocation("image", 0, 1),
        SourceLocation("image", -1, 0),
        SourceLocation("image", 0, 0, -1, 0),
    ],
)
def test_snapshot_rejects_malformed_binary_location_sentinels(location: SourceLocation) -> None:
    graph = evidence()
    node = graph.nodes["bin.image:sub_1000"]
    graph.nodes[node.id] = replace(node, location=location)
    with pytest.raises(ValueError, match="source location"):
        CodeGraph.from_snapshot(graph.to_snapshot())


def test_snapshot_still_rejects_zero_line_source_edge_locations() -> None:
    graph = CodeGraph(
        edges=[
            CodeEdge(
                "missing-a", "missing-b", EdgeKind.CALLS, location=SourceLocation("app.py", 0, 0)
            )
        ]
    )
    with pytest.raises(ValueError, match=r"edges\.location"):
        CodeGraph.from_snapshot(graph.to_snapshot())
