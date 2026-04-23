"""Tests for the JavaScript language parser."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind
from trailmark.parsers.javascript.parser import JavaScriptParser

SAMPLE_CODE = """\
import { EventEmitter } from 'events';

/** Base shape class. */
class Shape extends EventEmitter {
    constructor(name) {
        super();
        this.name = name;
    }

    /** Calculate area. */
    area() {
        return 0;
    }
}

class Circle extends Shape {
    constructor(radius) {
        super("circle");
        this.radius = radius;
    }

    area() {
        return Math.PI * this.radius * this.radius;
    }
}

function processShapes(shapes, threshold = 10) {
    const results = [];
    for (const shape of shapes) {
        if (shape.area() > threshold) {
            results.push(shape);
        }
    }
    if (results.length === 0) {
        throw new Error("no shapes above threshold");
    }
    return results;
}

function greet(name) {
    return "hello " + name;
}

const double = (x) => {
    return x * 2;
};

function run() {
    const shapes = [];
    greet("world");
    processShapes(shapes);
}
"""


def _parse_sample() -> tuple[JavaScriptParser, CodeGraph]:
    parser = JavaScriptParser()
    with tempfile.NamedTemporaryFile(
        suffix=".js",
        mode="w",
        delete=False,
    ) as f:
        f.write(SAMPLE_CODE)
        f.flush()
        graph = parser.parse_file(f.name)
    os.unlink(f.name)
    return parser, graph


class TestJavaScriptParserNodes:
    def test_finds_module(self) -> None:
        _, graph = _parse_sample()
        modules = [n for n in graph.nodes.values() if n.kind == NodeKind.MODULE]
        assert len(modules) == 1

    def test_finds_classes(self) -> None:
        _, graph = _parse_sample()
        classes = [n for n in graph.nodes.values() if n.kind == NodeKind.CLASS]
        names = {c.name for c in classes}
        assert "Shape" in names
        assert "Circle" in names

    def test_finds_functions(self) -> None:
        _, graph = _parse_sample()
        funcs = [n for n in graph.nodes.values() if n.kind == NodeKind.FUNCTION]
        names = {f.name for f in funcs}
        assert "processShapes" in names
        assert "greet" in names
        assert "run" in names

    def test_finds_arrow_function(self) -> None:
        _, graph = _parse_sample()
        funcs = [n for n in graph.nodes.values() if n.kind == NodeKind.FUNCTION]
        names = {f.name for f in funcs}
        assert "double" in names

    def test_finds_methods(self) -> None:
        _, graph = _parse_sample()
        methods = [n for n in graph.nodes.values() if n.kind == NodeKind.METHOD]
        names = {m.name for m in methods}
        assert "constructor" in names
        assert "area" in names

    def test_class_jsdoc(self) -> None:
        _, graph = _parse_sample()
        shape = next(n for n in graph.nodes.values() if n.name == "Shape")
        assert shape.docstring is not None
        assert "shape" in shape.docstring.lower()


class TestJavaScriptParserParameters:
    def test_simple_parameter(self) -> None:
        _, graph = _parse_sample()
        greet = next(n for n in graph.nodes.values() if n.name == "greet")
        assert len(greet.parameters) == 1
        assert greet.parameters[0].name == "name"

    def test_default_parameter(self) -> None:
        _, graph = _parse_sample()
        process = next(n for n in graph.nodes.values() if n.name == "processShapes")
        params = {p.name: p for p in process.parameters}
        assert "shapes" in params
        assert "threshold" in params
        assert params["threshold"].default == "10"

    def test_no_return_type(self) -> None:
        _, graph = _parse_sample()
        greet = next(n for n in graph.nodes.values() if n.name == "greet")
        assert greet.return_type is None


class TestJavaScriptParserComplexity:
    def test_simple_function_complexity(self) -> None:
        _, graph = _parse_sample()
        greet = next(n for n in graph.nodes.values() if n.name == "greet")
        assert greet.cyclomatic_complexity == 1

    def test_branching_function_complexity(self) -> None:
        _, graph = _parse_sample()
        process = next(n for n in graph.nodes.values() if n.name == "processShapes")
        assert process.cyclomatic_complexity is not None
        assert process.cyclomatic_complexity >= 3

    def test_branches_tracked(self) -> None:
        _, graph = _parse_sample()
        process = next(n for n in graph.nodes.values() if n.name == "processShapes")
        assert len(process.branches) > 0


class TestJavaScriptParserEdges:
    def test_contains_edges(self) -> None:
        _, graph = _parse_sample()
        contains = [e for e in graph.edges if e.kind == EdgeKind.CONTAINS]
        assert len(contains) > 0

    def test_inherits_edge(self) -> None:
        _, graph = _parse_sample()
        inherits = [e for e in graph.edges if e.kind == EdgeKind.INHERITS]
        assert len(inherits) >= 1
        targets = {e.target_id for e in inherits}
        has_shape = any("Shape" in t for t in targets)
        assert has_shape

    def test_call_edges(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        assert len(calls) > 0

    def test_throw_exception_type(self) -> None:
        _, graph = _parse_sample()
        process = next(n for n in graph.nodes.values() if n.name == "processShapes")
        assert len(process.exception_types) == 1
        assert process.exception_types[0].name == "Error"

    def test_edge_confidence(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        certain = [e for e in calls if e.confidence == EdgeConfidence.CERTAIN]
        inferred = [e for e in calls if e.confidence == EdgeConfidence.INFERRED]
        assert len(certain) > 0 or len(inferred) > 0


class TestJavaScriptParserDependencies:
    def test_imports_tracked(self) -> None:
        _, graph = _parse_sample()
        assert "events" in graph.dependencies


EXTRA_JS_CODE = """\
export function exported() {
    return 42;
}

export class ExportedWidget {
    render() { return "html"; }
}

function withRest(...args) {
    return args.length;
}
"""

ASSIGN_JS_CODE = """\
var handler;
handler = function() { return 1; };
"""


def _parse_extra() -> CodeGraph:
    parser = JavaScriptParser()
    with tempfile.NamedTemporaryFile(
        suffix=".js",
        mode="w",
        delete=False,
    ) as f:
        f.write(EXTRA_JS_CODE)
        f.flush()
        graph = parser.parse_file(f.name)
    os.unlink(f.name)
    return graph


def _parse_js_snippet(code: str) -> CodeGraph:
    parser = JavaScriptParser()
    with tempfile.NamedTemporaryFile(
        suffix=".js",
        mode="w",
        delete=False,
    ) as f:
        f.write(code)
        f.flush()
        graph = parser.parse_file(f.name)
    os.unlink(f.name)
    return graph


class TestJavaScriptExtraFeatures:
    def test_export_function_extracted(self) -> None:
        graph = _parse_extra()
        names = {n.name for n in graph.nodes.values()}
        assert "exported" in names

    def test_export_class_extracted(self) -> None:
        graph = _parse_extra()
        names = {n.name for n in graph.nodes.values()}
        assert "ExportedWidget" in names


class TestJavaScriptParserExtensions:
    def test_parse_directory_includes_mjs_and_cjs(self, tmp_path: Path) -> None:
        (tmp_path / "alpha.mjs").write_text("export function alpha() { return 1; }\n")
        (tmp_path / "beta.cjs").write_text("function beta() { return 2; }\n")

        graph = JavaScriptParser().parse_directory(str(tmp_path))

        names = {n.name for n in graph.nodes.values()}
        assert "alpha" in names
        assert "beta" in names

    def test_rest_parameter(self) -> None:
        graph = _parse_extra()
        fn = next(n for n in graph.nodes.values() if n.name == "withRest")
        assert len(fn.parameters) == 1
        assert fn.parameters[0].name == "...args"

    def test_expression_assignment_function(self) -> None:
        graph = _parse_js_snippet(ASSIGN_JS_CODE)
        names = {n.name for n in graph.nodes.values()}
        assert "handler" in names


class TestJavaScriptParseDirectory:
    def test_parses_multiple_files(self) -> None:
        parser = JavaScriptParser()
        code_a = "function fromA() {}\n"
        code_b = "function fromB() {}\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            for name, code in [("a.js", code_a), ("b.js", code_b)]:
                path = os.path.join(tmpdir, name)
                with open(path, "w") as f:
                    f.write(code)
            graph = parser.parse_directory(tmpdir)
        assert graph.language == "javascript"
        assert graph.root_path == tmpdir
        names = {n.name for n in graph.nodes.values()}
        assert "fromA" in names
        assert "fromB" in names

    def test_ignores_wrong_extensions(self) -> None:
        parser = JavaScriptParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "skip.txt")
            with open(path, "w") as f:
                f.write("not source code")
            graph = parser.parse_directory(tmpdir)
        assert len(graph.nodes) == 0
