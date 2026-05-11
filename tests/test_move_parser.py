"""Tests for the Move language parser."""

from __future__ import annotations

import os
import tempfile

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind
from trailmark.parsers.move.parser import MoveParser

SAMPLE_CODE = """\
module 0x1::Math {
    use 0x1::vector;

    /// 2D point structure.
    struct Point has copy, drop {
        x: u64,
        y: u64,
    }

    enum Sign has copy, drop {
        Pos,
        Neg,
    }

    fun helper(x: u64): u64 {
        x + 1
    }

    native fun native_hash(x: u64): u64;

    public fun add(x: u64, y: u64): u64 {
        let mut total = helper(x) + y;
        if (total > 0) {
            total = total + vector::length<u8>(&vector::empty<u8>());
        } else {
            total = 0;
        };

        while (total > 0) {
            total = total - 1;
        };

        total
    }
}
"""


def _parse_sample() -> tuple[MoveParser, CodeGraph]:
    parser = MoveParser()
    with tempfile.NamedTemporaryFile(
        suffix=".move",
        mode="w",
        delete=False,
    ) as f:
        f.write(SAMPLE_CODE)
        f.flush()
        graph = parser.parse_file(f.name)
    os.unlink(f.name)
    return parser, graph


class TestMoveParserNodes:
    def test_finds_file_module(self) -> None:
        _, graph = _parse_sample()
        modules = [n for n in graph.nodes.values() if n.kind == NodeKind.MODULE]
        assert len(modules) >= 2

    def test_finds_move_module(self) -> None:
        _, graph = _parse_sample()
        names = {n.name for n in graph.nodes.values() if n.kind == NodeKind.MODULE}
        assert "Math" in names

    def test_finds_struct(self) -> None:
        _, graph = _parse_sample()
        structs = [n for n in graph.nodes.values() if n.kind == NodeKind.STRUCT]
        assert {s.name for s in structs} == {"Point"}

    def test_finds_enum(self) -> None:
        _, graph = _parse_sample()
        enums = [n for n in graph.nodes.values() if n.kind == NodeKind.ENUM]
        assert {e.name for e in enums} == {"Sign"}

    def test_finds_functions_as_methods(self) -> None:
        _, graph = _parse_sample()
        methods = [n for n in graph.nodes.values() if n.kind == NodeKind.METHOD]
        names = {m.name for m in methods}
        assert "helper" in names
        assert "native_hash" in names
        assert "add" in names

    def test_extracts_docstring(self) -> None:
        _, graph = _parse_sample()
        point = next(n for n in graph.nodes.values() if n.name == "Point")
        assert point.docstring is not None
        assert "2D point" in point.docstring


class TestMoveParserParameters:
    def test_parameters_extracted(self) -> None:
        _, graph = _parse_sample()
        add = next(n for n in graph.nodes.values() if n.name == "add")
        assert len(add.parameters) == 2
        assert {p.name for p in add.parameters} == {"x", "y"}

    def test_parameter_types_extracted(self) -> None:
        _, graph = _parse_sample()
        helper = next(n for n in graph.nodes.values() if n.name == "helper")
        assert helper.parameters[0].type_ref is not None
        assert helper.parameters[0].type_ref.name == "u64"

    def test_return_type_extracted(self) -> None:
        _, graph = _parse_sample()
        add = next(n for n in graph.nodes.values() if n.name == "add")
        assert add.return_type is not None
        assert add.return_type.name == "u64"


class TestMoveParserComplexity:
    def test_simple_function_complexity(self) -> None:
        _, graph = _parse_sample()
        helper = next(n for n in graph.nodes.values() if n.name == "helper")
        assert helper.cyclomatic_complexity == 1

    def test_branching_function_complexity(self) -> None:
        _, graph = _parse_sample()
        add = next(n for n in graph.nodes.values() if n.name == "add")
        assert add.cyclomatic_complexity is not None
        assert add.cyclomatic_complexity >= 3

    def test_branches_tracked(self) -> None:
        _, graph = _parse_sample()
        add = next(n for n in graph.nodes.values() if n.name == "add")
        assert len(add.branches) >= 2


class TestMoveParserEdges:
    def test_contains_edges(self) -> None:
        _, graph = _parse_sample()
        contains = [e for e in graph.edges if e.kind == EdgeKind.CONTAINS]
        assert len(contains) > 0

    def test_call_edges(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        assert len(calls) > 0

    def test_certain_and_inferred_call_confidence(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        assert any(e.confidence == EdgeConfidence.CERTAIN for e in calls)
        assert any(e.confidence == EdgeConfidence.INFERRED for e in calls)


class TestMoveParserDependencies:
    def test_use_dependencies_tracked(self) -> None:
        _, graph = _parse_sample()
        assert "vector" in graph.dependencies


class TestMoveParseDirectory:
    def test_parses_multiple_files(self) -> None:
        parser = MoveParser()
        code_a = "module 0x1::A { fun from_a() {} }\n"
        code_b = "module 0x1::B { fun from_b() {} }\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            for name, code in [("a.move", code_a), ("b.move", code_b)]:
                path = os.path.join(tmpdir, name)
                with open(path, "w") as f:
                    f.write(code)
            graph = parser.parse_directory(tmpdir)
        assert graph.language == "move"
        assert graph.root_path == tmpdir
        names = {n.name for n in graph.nodes.values()}
        assert "from_a" in names
        assert "from_b" in names

    def test_ignores_wrong_extensions(self) -> None:
        parser = MoveParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "skip.txt")
            with open(path, "w") as f:
                f.write("not source code")
            graph = parser.parse_directory(tmpdir)
        assert len(graph.nodes) == 0
