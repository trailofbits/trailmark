"""Tests for the Rust language parser."""

from __future__ import annotations

import os
import tempfile

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind, SourceLocation
from trailmark.parsers.rust.parser import RustParser

SAMPLE_CODE = """\
use std::collections::HashMap;
use std::fmt;

/// A 2D point in space.
struct Point {
    x: f64,
    y: f64,
}

/// Something that can describe itself.
trait Describable {
    fn describe(&self) -> String;
}

enum Color {
    Red,
    Green,
    Blue,
}

impl Describable for Point {
    fn describe(&self) -> String {
        format!("({}, {})", self.x, self.y)
    }
}

impl Point {
    fn new(x: f64, y: f64) -> Point {
        Point { x, y }
    }

    fn distance(&self, other: &Point) -> f64 {
        let dx = self.x - other.x;
        let dy = self.y - other.y;
        (dx * dx + dy * dy).sqrt()
    }

    fn translate(&mut self, dx: f64, dy: f64) {
        self.x += dx;
        self.y += dy;
    }
}

fn abs(x: f64) -> f64 {
    if x < 0.0 {
        -x
    } else {
        x
    }
}

fn process(items: Vec<i32>) -> i32 {
    let mut total = 0;
    for item in items.iter() {
        if *item > 0 {
            total += item;
        }
    }
    match total {
        0 => println!("zero"),
        1..=10 => println!("small"),
        _ => println!("big"),
    }
    total
}

fn greet(name: &str) -> String {
    let msg = format!("Hello, {}", name);
    msg.to_uppercase()
}
"""

VERUS_CODE = """\
fn host() -> u64 { 1 }

#[tokio::main]
fn attributed() -> u64 { host() }

verus! {
    pub fn verified(x: u64) -> u64
        requires x > 0,
        ensures result > x,
    {
        host();
        x + 1
    }

    pub open spec fn model(x: int) -> int { x + 1 }

    proof fn lemma() {
        assert(true);
    }
}
"""


def _parse_sample() -> tuple[RustParser, CodeGraph]:
    parser = RustParser()
    with tempfile.NamedTemporaryFile(
        suffix=".rs",
        mode="w",
        delete=False,
    ) as f:
        f.write(SAMPLE_CODE)
        f.flush()
        graph = parser.parse_file(f.name)
    os.unlink(f.name)
    return parser, graph


def _parse_verus_sample() -> tuple[RustParser, CodeGraph]:
    parser = RustParser()
    with tempfile.NamedTemporaryFile(
        suffix=".rs",
        mode="w",
        delete=False,
    ) as f:
        f.write(VERUS_CODE)
        f.flush()
        graph = parser.parse_file(f.name)
    os.unlink(f.name)
    return parser, graph


class TestRustParserNodes:
    def test_finds_module(self) -> None:
        _, graph = _parse_sample()
        modules = [n for n in graph.nodes.values() if n.kind == NodeKind.MODULE]
        assert len(modules) == 1

    def test_finds_struct(self) -> None:
        _, graph = _parse_sample()
        structs = [n for n in graph.nodes.values() if n.kind == NodeKind.STRUCT]
        names = {s.name for s in structs}
        assert "Point" in names

    def test_finds_trait(self) -> None:
        _, graph = _parse_sample()
        traits = [n for n in graph.nodes.values() if n.kind == NodeKind.TRAIT]
        names = {t.name for t in traits}
        assert "Describable" in names

    def test_finds_enum(self) -> None:
        _, graph = _parse_sample()
        enums = [n for n in graph.nodes.values() if n.kind == NodeKind.ENUM]
        names = {e.name for e in enums}
        assert "Color" in names

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
        assert "new" in names
        assert "distance" in names
        assert "translate" in names
        assert "describe" in names

    def test_method_id_includes_type(self) -> None:
        _, graph = _parse_sample()
        method_ids = [n.id for n in graph.nodes.values() if n.kind == NodeKind.METHOD]
        has_point = any("Point" in mid for mid in method_ids)
        assert has_point

    def test_struct_docstring(self) -> None:
        _, graph = _parse_sample()
        point = next(n for n in graph.nodes.values() if n.name == "Point")
        assert point.docstring is not None
        assert "2D" in point.docstring


class TestRustParserParameters:
    def test_function_params(self) -> None:
        _, graph = _parse_sample()
        greet = next(n for n in graph.nodes.values() if n.name == "greet")
        assert len(greet.parameters) == 1
        assert greet.parameters[0].name == "name"

    def test_param_type(self) -> None:
        _, graph = _parse_sample()
        greet = next(n for n in graph.nodes.values() if n.name == "greet")
        assert greet.parameters[0].type_ref is not None
        assert greet.parameters[0].type_ref.name == "&str"

    def test_method_params_skip_self(self) -> None:
        _, graph = _parse_sample()
        distance = next(n for n in graph.nodes.values() if n.name == "distance")
        assert len(distance.parameters) == 1
        assert distance.parameters[0].name == "other"

    def test_multiple_params(self) -> None:
        _, graph = _parse_sample()
        new_fn = next(n for n in graph.nodes.values() if n.name == "new")
        assert len(new_fn.parameters) == 2
        names = {p.name for p in new_fn.parameters}
        assert "x" in names
        assert "y" in names

    def test_return_type(self) -> None:
        _, graph = _parse_sample()
        abs_fn = next(n for n in graph.nodes.values() if n.name == "abs")
        assert abs_fn.return_type is not None
        assert abs_fn.return_type.name == "f64"


class TestRustParserComplexity:
    def test_simple_function_complexity(self) -> None:
        _, graph = _parse_sample()
        greet = next(n for n in graph.nodes.values() if n.name == "greet")
        assert greet.cyclomatic_complexity == 1

    def test_branching_function_complexity(self) -> None:
        _, graph = _parse_sample()
        process = next(n for n in graph.nodes.values() if n.name == "process")
        assert process.cyclomatic_complexity is not None
        assert process.cyclomatic_complexity >= 3

    def test_branches_tracked(self) -> None:
        _, graph = _parse_sample()
        process = next(n for n in graph.nodes.values() if n.name == "process")
        assert len(process.branches) > 0

    def test_if_else_complexity(self) -> None:
        _, graph = _parse_sample()
        abs_fn = next(n for n in graph.nodes.values() if n.name == "abs")
        assert abs_fn.cyclomatic_complexity is not None
        assert abs_fn.cyclomatic_complexity >= 2


class TestRustParserEdges:
    def test_contains_edges(self) -> None:
        _, graph = _parse_sample()
        contains = [e for e in graph.edges if e.kind == EdgeKind.CONTAINS]
        assert len(contains) > 0

    def test_implements_edge(self) -> None:
        _, graph = _parse_sample()
        implements = [e for e in graph.edges if e.kind == EdgeKind.IMPLEMENTS]
        assert len(implements) == 1
        assert "Point" in implements[0].source_id
        assert "Describable" in implements[0].target_id

    def test_call_edges(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        assert len(calls) > 0

    def test_call_confidence(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        certain = [e for e in calls if e.confidence == EdgeConfidence.CERTAIN]
        inferred = [e for e in calls if e.confidence == EdgeConfidence.INFERRED]
        assert len(certain) > 0 or len(inferred) > 0

    def test_method_contained_by_type(self) -> None:
        _, graph = _parse_sample()
        contains = [e for e in graph.edges if e.kind == EdgeKind.CONTAINS]
        point_contains = [e for e in contains if "Point" in e.source_id]
        assert len(point_contains) > 0


class TestRustParserDependencies:
    def test_imports_tracked(self) -> None:
        _, graph = _parse_sample()
        assert "std" in graph.dependencies


class TestRustParserVerus:
    def test_extracts_plain_attributed_and_verus_functions(self) -> None:
        _, graph = _parse_verus_sample()
        functions = {
            node.name: node for node in graph.nodes.values() if node.kind == NodeKind.FUNCTION
        }

        assert set(functions) == {"host", "attributed", "verified", "model", "lemma"}
        assert functions["host"].location == SourceLocation(
            file_path=graph.root_path,
            start_line=1,
            end_line=1,
            start_col=0,
            end_col=22,
        )
        assert functions["attributed"].location == SourceLocation(
            file_path=graph.root_path,
            start_line=4,
            end_line=4,
            start_col=0,
            end_col=33,
        )
        assert functions["verified"].location == SourceLocation(
            file_path=graph.root_path,
            start_line=7,
            end_line=13,
            start_col=4,
            end_col=5,
        )

    def test_extracts_verus_signature_metadata(self) -> None:
        _, graph = _parse_verus_sample()
        verified = next(node for node in graph.nodes.values() if node.name == "verified")

        assert [param.name for param in verified.parameters] == ["x"]
        assert verified.parameters[0].type_ref is not None
        assert verified.parameters[0].type_ref.name == "u64"
        assert verified.return_type is not None
        assert verified.return_type.name == "u64"

    def test_resolves_call_from_verus_function_to_host_function(self) -> None:
        _, graph = _parse_verus_sample()
        verified = next(node for node in graph.nodes.values() if node.name == "verified")
        host = next(node for node in graph.nodes.values() if node.name == "host")

        assert any(
            edge.kind == EdgeKind.CALLS
            and edge.source_id == verified.id
            and edge.target_id == host.id
            for edge in graph.edges
        )

    def test_loads_verus_grammar_only_when_needed(self) -> None:
        parser, _ = _parse_sample()
        assert parser._verus_parser is None

        parser, _ = _parse_verus_sample()
        assert parser._verus_parser is not None

    def test_parses_multiple_verus_ranges_and_ignores_other_macros(self) -> None:
        source = """\
verus! { spec fn first() -> bool { true } }
opaque! { fn hidden() {} }
verus! { proof fn second() {} }
"""
        with tempfile.NamedTemporaryFile(suffix=".rs", mode="w", delete=False) as f:
            f.write(source)
            f.flush()
            graph = RustParser().parse_file(f.name)
        os.unlink(f.name)

        names = {node.name for node in graph.nodes.values()}
        assert {"first", "second"} <= names
        assert "hidden" not in names


class TestRustParseDirectory:
    def test_parses_multiple_files(self) -> None:
        parser = RustParser()
        code_a = "fn from_a() {}\n"
        code_b = "fn from_b() {}\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            for name, code in [("a.rs", code_a), ("b.rs", code_b)]:
                path = os.path.join(tmpdir, name)
                with open(path, "w") as f:
                    f.write(code)
            graph = parser.parse_directory(tmpdir)
        assert graph.language == "rust"
        assert graph.root_path == tmpdir
        names = {n.name for n in graph.nodes.values()}
        assert "from_a" in names
        assert "from_b" in names

    def test_ignores_wrong_extensions(self) -> None:
        parser = RustParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "skip.txt")
            with open(path, "w") as f:
                f.write("not source code")
            graph = parser.parse_directory(tmpdir)
        assert len(graph.nodes) == 0
