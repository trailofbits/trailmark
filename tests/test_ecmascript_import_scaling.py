"""Repeated imports share export discovery and constructor lookup work."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from trailmark.models.edges import CodeEdge
from trailmark.models.graph import CodeGraph
from trailmark.parse import parse_directory
from trailmark.parsers import _ecmascript


@pytest.mark.parametrize("language,extension", [("javascript", ".js"), ("typescript", ".ts")])
def test_shared_exports_and_constructors_are_indexed_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language: str, extension: str
) -> None:
    source = tmp_path / f"widget{extension}"
    source.write_text("export class Widget { constructor() { sink(); } }\n")
    for index in range(8):
        (tmp_path / f"make{index}{extension}").write_text(
            "import { Widget } from './widget';\n"
            "export function make() { return [new Widget(), new Widget()]; }\n"
        )
    reads = 0
    original_read = Path.read_bytes

    def read_bytes(path: Path) -> bytes:
        nonlocal reads
        if path == source:
            reads += 1
        return original_read(path)

    class CountedEdges(list[CodeEdge]):
        visits = 0

        def __iter__(self) -> Iterator[CodeEdge]:
            for edge in super().__iter__():
                self.visits += 1
                yield edge

    original_bind = _ecmascript.bind_imported_constructors

    def bind(graph: CodeGraph) -> None:
        edges = CountedEdges(graph.edges)
        graph.edges = edges
        original_bind(graph)
        # One traversal builds the index; another rebinds the call sites.
        assert edges.visits <= 3 * len(edges)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(_ecmascript, "bind_imported_constructors", bind)
    graph = parse_directory(str(tmp_path), language)
    constructions = [e for e in graph.edges if dict(e.attributes).get("constructor_call")]
    assert len(constructions) == 16
    assert {e.target_id for e in constructions} == {"widget:Widget.constructor"}
    # One read extracts the file itself; one discovers its named exports.
    assert reads == 2

    source.write_text("class Widget { constructor() {} }\n")
    graph = parse_directory(str(tmp_path), language)
    assert all(
        graph.nodes[e.target_id].origin.value == "proxy"
        for e in graph.edges
        if dict(e.attributes).get("constructor_call")
    )
    assert reads == 4
