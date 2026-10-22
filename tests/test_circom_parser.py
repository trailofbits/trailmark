"""Tests for the Circom language parser."""

from __future__ import annotations

import os
import tempfile

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind
from trailmark.parsers.circom.parser import CircomParser

SAMPLE_CODE = """\
pragma circom 2.0.0;

include "circomlib/poseidon.circom";

/// A simple multiplier template.
template Multiplier(n) {
    signal input in1;
    signal input in2;
    signal output out;

    out <== in1 * in2;
}

/// Range check with branching logic.
template RangeCheck(bits) {
    signal input in;
    signal output out;

    component n2b = Num2Bits(bits);
    n2b.in <== in;

    var sum = 0;
    for (var i = 0; i < bits; i++) {
        sum += n2b.out[i];
    }

    if (sum > 0) {
        out <== 1;
    } else {
        out <== 0;
    }
}

function factorial(n) {
    if (n <= 1) {
        return 1;
    }
    return n * factorial(n - 1);
}

function add(a, b) {
    return a + b;
}

component main {public [in1, in2]} = Multiplier(32);
"""


def _parse_sample() -> tuple[CircomParser, CodeGraph]:
    parser = CircomParser()
    with tempfile.NamedTemporaryFile(
        suffix=".circom",
        mode="w",
        delete=False,
    ) as f:
        f.write(SAMPLE_CODE)
        f.flush()
        graph = parser.parse_file(f.name)
    os.unlink(f.name)
    return parser, graph


class TestCircomParserNodes:
    def test_finds_module(self) -> None:
        _, graph = _parse_sample()
        modules = [n for n in graph.nodes.values() if n.kind == NodeKind.MODULE]
        assert len(modules) == 1

    def test_finds_templates(self) -> None:
        _, graph = _parse_sample()
        templates = [n for n in graph.nodes.values() if n.kind == NodeKind.TEMPLATE]
        names = {t.name for t in templates}
        assert "Multiplier" in names
        assert "RangeCheck" in names

    def test_finds_functions(self) -> None:
        _, graph = _parse_sample()
        funcs = [
            n for n in graph.nodes.values() if n.kind == NodeKind.FUNCTION and n.name != "main"
        ]
        names = {f.name for f in funcs}
        assert "factorial" in names
        assert "add" in names

    def test_finds_main_component(self) -> None:
        _, graph = _parse_sample()
        main = graph.nodes.get(next(nid for nid, n in graph.nodes.items() if n.name == "main"))
        assert main is not None
        assert main.kind == NodeKind.FUNCTION

    def test_template_docstring(self) -> None:
        _, graph = _parse_sample()
        mult = next(n for n in graph.nodes.values() if n.name == "Multiplier")
        assert mult.docstring is not None
        assert "simple multiplier" in mult.docstring

    def test_template_id_format(self) -> None:
        _, graph = _parse_sample()
        templates = [n for n in graph.nodes.values() if n.kind == NodeKind.TEMPLATE]
        for tmpl in templates:
            assert ":" in tmpl.id


class TestCircomParserParameters:
    def test_template_params(self) -> None:
        _, graph = _parse_sample()
        mult = next(n for n in graph.nodes.values() if n.name == "Multiplier")
        assert len(mult.parameters) == 1
        assert mult.parameters[0].name == "n"

    def test_function_params(self) -> None:
        _, graph = _parse_sample()
        add_fn = next(n for n in graph.nodes.values() if n.name == "add")
        assert len(add_fn.parameters) == 2
        names = {p.name for p in add_fn.parameters}
        assert "a" in names
        assert "b" in names

    def test_params_are_untyped(self) -> None:
        _, graph = _parse_sample()
        mult = next(n for n in graph.nodes.values() if n.name == "Multiplier")
        assert mult.parameters[0].type_ref is None

    def test_no_return_type(self) -> None:
        _, graph = _parse_sample()
        factorial = next(n for n in graph.nodes.values() if n.name == "factorial")
        assert factorial.return_type is None


class TestCircomParserComplexity:
    def test_simple_template_complexity(self) -> None:
        _, graph = _parse_sample()
        mult = next(n for n in graph.nodes.values() if n.name == "Multiplier")
        assert mult.cyclomatic_complexity == 1

    def test_branching_template_complexity(self) -> None:
        _, graph = _parse_sample()
        rc = next(n for n in graph.nodes.values() if n.name == "RangeCheck")
        assert rc.cyclomatic_complexity is not None
        assert rc.cyclomatic_complexity >= 3

    def test_branches_tracked(self) -> None:
        _, graph = _parse_sample()
        rc = next(n for n in graph.nodes.values() if n.name == "RangeCheck")
        assert len(rc.branches) > 0

    def test_function_complexity(self) -> None:
        _, graph = _parse_sample()
        factorial = next(n for n in graph.nodes.values() if n.name == "factorial")
        assert factorial.cyclomatic_complexity is not None
        assert factorial.cyclomatic_complexity >= 2

    def test_simple_function_complexity(self) -> None:
        _, graph = _parse_sample()
        add_fn = next(n for n in graph.nodes.values() if n.name == "add")
        assert add_fn.cyclomatic_complexity == 1


class TestCircomParserEdges:
    def test_contains_edges(self) -> None:
        _, graph = _parse_sample()
        contains = [e for e in graph.edges if e.kind == EdgeKind.CONTAINS]
        assert len(contains) > 0

    def test_call_edges_from_template(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        rc_calls = [e for e in calls if "RangeCheck" in e.source_id]
        assert len(rc_calls) > 0

    def test_call_edges_from_function(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        fact_calls = [e for e in calls if "factorial" in e.source_id]
        assert len(fact_calls) > 0

    def test_main_calls_template(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        main_calls = [e for e in calls if "main" in e.source_id]
        assert len(main_calls) == 1
        assert "Multiplier" in main_calls[0].target_id

    def test_call_confidence(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        assert all(e.confidence == EdgeConfidence.CERTAIN for e in calls)


class TestCircomParserDependencies:
    def test_imports_tracked(self) -> None:
        _, graph = _parse_sample()
        assert "circomlib/poseidon.circom" in graph.dependencies


class TestCircomParseDirectory:
    def test_parses_multiple_files(self) -> None:
        parser = CircomParser()
        code_a = "template A() { signal input x; }\n"
        code_b = "template B() { signal output y; }\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            for name, code in [("a.circom", code_a), ("b.circom", code_b)]:
                path = os.path.join(tmpdir, name)
                with open(path, "w") as f:
                    f.write(code)
            graph = parser.parse_directory(tmpdir)
        assert graph.language == "circom"
        assert graph.root_path == tmpdir
        names = {n.name for n in graph.nodes.values()}
        assert "A" in names
        assert "B" in names

    def test_ignores_wrong_extensions(self) -> None:
        parser = CircomParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "skip.txt")
            with open(path, "w") as f:
                f.write("not source code")
            graph = parser.parse_directory(tmpdir)
        assert len(graph.nodes) == 0
