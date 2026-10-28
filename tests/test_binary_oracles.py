"""Byte-level reference oracles for overlapping native evidence."""

from __future__ import annotations

from dataclasses import replace

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from tests.test_binary_models import evidence
from trailmark.models import (
    BinaryArtifact,
    BinaryRange,
    BinarySelection,
    BinarySourceMapping,
    CodeGraph,
    CodeUnit,
    NodeKind,
    NodeOrigin,
    SourceLocation,
)
from trailmark.storage.binary_index import IntervalIndex
from trailmark.storage.graph_store import GraphStore


@given(
    spans=st.lists(st.tuples(st.integers(0, 80), st.integers(1, 20)), max_size=25),
    start=st.integers(0, 100),
    width=st.integers(1, 25),
)
@example(spans=[(0, 100), (10, 10), (11, 1), (100, 10), (10, 10)], start=20, width=1)
@settings(max_examples=250, deadline=None)
def test_interval_lookup_matches_linear_scan(
    spans: list[tuple[int, int]], start: int, width: int
) -> None:
    rows = [BinarySelection(left, left + length) for left, length in spans]
    ordered = sorted(rows, key=lambda row: (row.start, row.end))
    end = start + width
    index = IntervalIndex(iter(rows))
    assert index.overlapping(start, end) == [
        row for row in ordered if row.start < end and start < row.end
    ]
    assert index.containing(start, end) == [
        row for row in ordered if row.start <= start and end <= row.end
    ]


@st.composite
def _native_evidence(draw: st.DrawFn) -> CodeGraph:
    source = CodeUnit("a:helper", "helper", NodeKind.FUNCTION, SourceLocation("a.c", 1, 10))
    binary = CodeUnit(
        "bin:helper",
        "helper",
        NodeKind.FUNCTION,
        SourceLocation("image", 0, 0),
        origin=NodeOrigin.BINARY,
    )
    artifact = BinaryArtifact(
        "image", "image", "a" * 64, "ELF", "x86_64", 64, "little", 0x1000, 0, 100
    )
    graph = CodeGraph(
        nodes={node.id: node for node in (source, binary)},
        binary_artifacts={artifact.id: artifact},
    )
    count = draw(st.integers(0, 8))
    for index in range(count):
        offset = draw(st.integers(0, 50))
        length = draw(st.integers(1, 15))
        file_offset = draw(st.integers(0, 50))
        start = artifact.image_base + offset
        row = BinaryRange(
            f"range{index}", artifact.id, binary.id, start, start + length, offset, file_offset
        )
        graph.binary_ranges[row.id] = row
        for frame in range(draw(st.integers(0, 3))):
            left = draw(st.integers(0, length - 1))
            right = draw(st.integers(left + 1, length))
            mapping = BinarySourceMapping(
                f"mapping{index}.{frame}",
                row.id,
                draw(st.sampled_from([source.id, None])),
                start + left,
                start + right,
                "debug",
                "a.c",
                frame + 1,
                inline_depth=frame,
            )
            graph.binary_source_mappings[mapping.id] = mapping
    return graph


@given(graph=_native_evidence())
@settings(max_examples=150, deadline=None)
def test_address_queries_match_byte_coverage_in_each_address_space(graph: CodeGraph) -> None:
    store = GraphStore(graph)
    mappings = sorted(graph.binary_source_mappings.values(), key=lambda row: (row.start, row.end))
    file_bytes: dict[int, set[int]] = {}
    for row in graph.binary_ranges.values():
        for position, va in enumerate(range(row.start, row.end), start=row.file_offset):
            file_bytes.setdefault(position, set()).add(va)
    for offset in range(70):
        address = 0x1000 + offset
        expected = [row for row in mappings if row.start <= address < row.end]
        assert store.source_at_address("image", address) == expected
        assert store.source_at_address("image", offset, address_kind="rva") == expected
        addresses = file_bytes.get(offset, set())
        if len(addresses) > 1:
            with pytest.raises(ValueError, match="ambiguous"):
                store.source_at_address("image", offset, address_kind="file_offset")
        else:
            expected = [
                row for row in mappings if any(row.start <= va < row.end for va in addresses)
            ]
            assert store.source_at_address("image", offset, address_kind="file_offset") == expected
    assert store.source_at_address("missing", 0) == []


@pytest.mark.parametrize("address", [-1, True, False, 1.5, "1", None])
@pytest.mark.parametrize("artifact", ["image", "missing"])
def test_address_queries_reject_invalid_addresses_even_for_missing_artifacts(
    address: object, artifact: str
) -> None:
    with pytest.raises(ValueError, match="nonnegative integer"):
        GraphStore(evidence()).source_at_address(artifact, address)  # ty: ignore[invalid-argument-type]


def test_source_ranges_clip_offsets_merge_adjacent_evidence_and_filter_artifacts() -> None:
    graph = evidence()
    first = graph.binary_source_mappings["first"]
    graph.binary_source_mappings["adjacent"] = replace(
        first, id="adjacent", start=first.end, end=0x1006
    )
    graph.binary_source_mappings["anonymous"] = replace(
        first, id="anonymous", source_node_id=None, start=0x1000, end=0x1010, line=None
    )
    graph.binary_artifacts["other"] = replace(graph.binary_artifacts["image"], id="other")
    other = replace(graph.binary_ranges["range"], id="other.range", artifact_id="other")
    graph.binary_ranges[other.id] = other
    graph.binary_source_mappings["other.first"] = replace(
        first, id="other.first", range_id=other.id
    )
    store = GraphStore(graph)
    source = store.binary_ranges_for_node("a:helper", artifact="image")
    assert [
        (
            row.artifact_id,
            row.start,
            row.end,
            row.rva,
            row.file_offset,
            tuple(m.id for m in mappings),
        )
        for row, mappings in source
    ] == [
        ("image", 0x1001, 0x1006, 1, 17, ("first", "adjacent")),
        ("image", 0x1008, 0x100A, 8, 24, ("second",)),
    ]
    assert [
        (row.artifact_id, row.start, row.end)
        for row, _ in store.binary_ranges_for_node("a:helper", line=2)
    ] == [("image", 0x1001, 0x1006), ("other", 0x1001, 0x1003)]
    assert store.binary_ranges_for_node("a:helper", artifact="missing") == []
    assert store.binary_ranges_for_node("a:helper", line=99) == []
    assert store.binary_ranges_for_node("missing") == []
    binary = store.binary_ranges_for_node("bin.image:sub_1000", artifact="image")
    assert binary == [
        (
            graph.binary_ranges["range"],
            tuple(
                graph.binary_source_mappings[key]
                for key in ("first", "second", "adjacent", "anonymous")
            ),
        )
    ]
    assert store.binary_ranges_for_artifact("image") == [graph.binary_ranges["range"]]
    assert store.binary_mappings_for_artifact("image") == sorted(
        (graph.binary_source_mappings[key] for key in ("first", "second", "adjacent", "anonymous")),
        key=lambda row: (row.start, row.end),
    )
    assert store.binary_ranges_for_artifact("missing") == []
    assert store.binary_mappings_for_artifact("missing") == []
