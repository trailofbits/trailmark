"""Tests for the Go language parser."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind
from trailmark.parsers.go.parser import GoParser
from trailmark.query.api import QueryEngine


def test_cross_file_calls_require_both_the_same_directory_and_package(tmp_path: Path) -> None:
    files = {
        "caller.go": "package p\nfunc caller() { target() }\n",
        "provider.go": "package p\nfunc target() {}\n",
        "other_test.go": "package p_test\nfunc target() {}\n",
        "sub/provider.go": "package p\nfunc target() {}\n",
    }
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(source)
    engine = QueryEngine.from_directory(str(tmp_path), language="go")
    assert [node["id"] for node in engine.callees_of("caller:caller")] == ["provider:target"]


SAMPLE_CODE = """\
package main

import (
\t"fmt"
\t"strings"
)

// Point represents a 2D coordinate.
type Point struct {
\tX int
\tY int
}

// Mover defines something that can move.
type Mover interface {
\tMove(dx int, dy int)
}

// Translate shifts a point by dx and dy.
func (p *Point) Translate(dx int, dy int) {
\tp.X += dx
\tp.Y += dy
}

// Distance returns the Manhattan distance from origin.
func (p *Point) Distance() int {
\tresult := p.X + p.Y
\tif result < 0 {
\t\tresult = -result
\t}
\treturn result
}

func abs(x int) int {
\tif x < 0 {
\t\treturn -x
\t}
\treturn x
}

func process(items []int) int {
\ttotal := 0
\tfor _, item := range items {
\t\tif item > 0 {
\t\t\ttotal += item
\t\t}
\t}
\tswitch {
\tcase total > 100:
\t\tfmt.Println("big")
\tcase total > 10:
\t\tfmt.Println("medium")
\tdefault:
\t\tfmt.Println("small")
\t}
\treturn total
}

func greet(name string) string {
\t_ = abs(0)
\tmsg := fmt.Sprintf("Hello, %s", name)
\treturn strings.ToUpper(msg)
}
"""


def _parse_sample() -> tuple[GoParser, CodeGraph]:
    parser = GoParser()
    with tempfile.NamedTemporaryFile(
        suffix=".go",
        mode="w",
        delete=False,
    ) as f:
        f.write(SAMPLE_CODE)
        f.flush()
        graph = parser.parse_file(f.name)
    os.unlink(f.name)
    return parser, graph


class TestGoParserNodes:
    def test_finds_module(self) -> None:
        _, graph = _parse_sample()
        modules = [n for n in graph.nodes.values() if n.kind == NodeKind.MODULE]
        assert len(modules) == 1

    def test_finds_struct(self) -> None:
        _, graph = _parse_sample()
        structs = [n for n in graph.nodes.values() if n.kind == NodeKind.STRUCT]
        names = {s.name for s in structs}
        assert "Point" in names

    def test_finds_interface(self) -> None:
        _, graph = _parse_sample()
        interfaces = [n for n in graph.nodes.values() if n.kind == NodeKind.INTERFACE]
        names = {i.name for i in interfaces}
        assert "Mover" in names

    def test_finds_functions(self) -> None:
        _, graph = _parse_sample()
        funcs = [n for n in graph.nodes.values() if n.kind == NodeKind.FUNCTION]
        names = {f.name for f in funcs}
        assert "abs" in names
        assert "process" in names
        assert "greet" in names

    def test_finds_methods(self) -> None:
        _, graph = _parse_sample()
        methods = [n for n in graph.nodes.values() if n.kind == NodeKind.METHOD]
        names = {m.name for m in methods}
        assert "Translate" in names
        assert "Distance" in names

    def test_method_id_includes_receiver(self) -> None:
        _, graph = _parse_sample()
        method_ids = [n.id for n in graph.nodes.values() if n.kind == NodeKind.METHOD]
        has_point = any("Point" in mid for mid in method_ids)
        assert has_point

    def test_struct_docstring(self) -> None:
        _, graph = _parse_sample()
        point = next(n for n in graph.nodes.values() if n.name == "Point")
        assert point.docstring is not None
        assert "2D" in point.docstring


class TestGoParserParameters:
    def test_function_params(self) -> None:
        _, graph = _parse_sample()
        greet = next(n for n in graph.nodes.values() if n.name == "greet")
        assert len(greet.parameters) == 1
        assert greet.parameters[0].name == "name"

    def test_param_type(self) -> None:
        _, graph = _parse_sample()
        greet = next(n for n in graph.nodes.values() if n.name == "greet")
        assert greet.parameters[0].type_ref is not None
        assert greet.parameters[0].type_ref.name == "string"

    def test_method_params(self) -> None:
        _, graph = _parse_sample()
        translate = next(n for n in graph.nodes.values() if n.name == "Translate")
        assert len(translate.parameters) == 2
        names = {p.name for p in translate.parameters}
        assert "dx" in names
        assert "dy" in names

    def test_return_type(self) -> None:
        _, graph = _parse_sample()
        abs_fn = next(n for n in graph.nodes.values() if n.name == "abs")
        assert abs_fn.return_type is not None
        assert abs_fn.return_type.name == "int"


class TestGoParserComplexity:
    def test_simple_function_complexity(self) -> None:
        _, graph = _parse_sample()
        greet = next(n for n in graph.nodes.values() if n.name == "greet")
        assert greet.cyclomatic_complexity == 1

    def test_branching_function_complexity(self) -> None:
        _, graph = _parse_sample()
        process = next(n for n in graph.nodes.values() if n.name == "process")
        assert process.cyclomatic_complexity is not None
        assert process.cyclomatic_complexity >= 4

    def test_branches_tracked(self) -> None:
        _, graph = _parse_sample()
        process = next(n for n in graph.nodes.values() if n.name == "process")
        assert len(process.branches) > 0


class TestGoParserEdges:
    def test_contains_edges(self) -> None:
        _, graph = _parse_sample()
        contains = [e for e in graph.edges if e.kind == EdgeKind.CONTAINS]
        assert len(contains) > 0

    def test_method_contained_by_struct(self) -> None:
        _, graph = _parse_sample()
        contains = [e for e in graph.edges if e.kind == EdgeKind.CONTAINS]
        struct_contains = [e for e in contains if "Point" in e.source_id]
        assert len(struct_contains) > 0

    def test_call_edges(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        assert len(calls) > 0

    def test_call_confidence_certain(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        certain = [e for e in calls if e.confidence == EdgeConfidence.CERTAIN]
        assert len(certain) > 0

    def test_call_confidence_inferred(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        inferred = [e for e in calls if e.confidence == EdgeConfidence.INFERRED]
        assert len(inferred) > 0


class TestGoParserDependencies:
    def test_imports_tracked(self) -> None:
        _, graph = _parse_sample()
        assert "fmt" in graph.dependencies
        assert "strings" in graph.dependencies


class TestGoParseDirectory:
    def test_parses_multiple_files(self) -> None:
        parser = GoParser()
        code_a = "package main\n\nfunc fromA() {}\n"
        code_b = "package main\n\nfunc fromB() {}\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            for name, code in [("a.go", code_a), ("b.go", code_b)]:
                path = os.path.join(tmpdir, name)
                with open(path, "w") as f:
                    f.write(code)
            graph = parser.parse_directory(tmpdir)
        assert graph.language == "go"
        assert graph.root_path == tmpdir
        names = {n.name for n in graph.nodes.values()}
        assert "fromA" in names
        assert "fromB" in names

    def test_ignores_wrong_extensions(self) -> None:
        parser = GoParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "skip.txt")
            with open(path, "w") as f:
                f.write("not source code")
            graph = parser.parse_directory(tmpdir)
        assert len(graph.nodes) == 0
