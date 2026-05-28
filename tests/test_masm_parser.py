"""Tests for the Miden assembly language parser."""

from __future__ import annotations

import os
import tempfile

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind, NodeOrigin
from trailmark.parsers.masm.parser import MasmParser

SAMPLE_CODE = """\
#! This is a sample Miden assembly module
#! for testing the parser.

use std::math::u64
use std::crypto::hashes::blake3

const FOO = 42
const BAR = FOO + 1

#! Adds two field elements on the stack.
pub proc add_values
    add
end

#! Performs conditional logic.
pub proc conditional_branch
    push.1
    push.2
    if.true
        add
    else
        sub
    end
    while.true
        dup
        neq.0
        drop
    end
    repeat.3
        dup
    end
end

@locals(2)
proc helper_proc
    loc_store.0
    loc_store.1
    loc_load.0
    loc_load.1
    add
end

#! Main entrypoint.
begin
    push.10
    push.20
    exec.add_values
    call.std::math::u64::checked_add
    exec.helper_proc
end
"""


def _parse_sample() -> tuple[MasmParser, CodeGraph]:
    parser = MasmParser()
    with tempfile.NamedTemporaryFile(
        suffix=".masm",
        mode="w",
        delete=False,
    ) as f:
        f.write(SAMPLE_CODE)
        f.flush()
        graph = parser.parse_file(f.name)
    os.unlink(f.name)
    return parser, graph


class TestMasmParserNodes:
    def test_finds_module(self) -> None:
        _, graph = _parse_sample()
        modules = [n for n in graph.nodes.values() if n.kind == NodeKind.MODULE]
        assert len(modules) == 1

    def test_finds_procedures(self) -> None:
        _, graph = _parse_sample()
        funcs = [
            n
            for n in graph.nodes.values()
            if n.kind == NodeKind.FUNCTION and n.name not in ("begin", "FOO", "BAR")
        ]
        names = {f.name for f in funcs}
        assert "add_values" in names
        assert "conditional_branch" in names
        assert "helper_proc" in names

    def test_finds_entrypoint(self) -> None:
        _, graph = _parse_sample()
        begin = next(
            (n for n in graph.nodes.values() if n.name == "begin"),
            None,
        )
        assert begin is not None
        assert begin.kind == NodeKind.FUNCTION
        assert begin.origin == NodeOrigin.SYNTHETIC

    def test_finds_constants(self) -> None:
        _, graph = _parse_sample()
        names = {n.name for n in graph.nodes.values()}
        assert "FOO" in names
        assert "BAR" in names

    def test_procedure_id_format(self) -> None:
        _, graph = _parse_sample()
        funcs = [n for n in graph.nodes.values() if n.kind == NodeKind.FUNCTION]
        for func in funcs:
            assert ":" in func.id

    def test_procedure_docstring(self) -> None:
        _, graph = _parse_sample()
        add_vals = next(n for n in graph.nodes.values() if n.name == "add_values")
        assert add_vals.docstring is not None
        assert "Adds two field elements" in add_vals.docstring

    def test_export_visibility_in_docstring(self) -> None:
        _, graph = _parse_sample()
        add_vals = next(n for n in graph.nodes.values() if n.name == "add_values")
        assert add_vals.docstring is not None
        assert "[export]" in add_vals.docstring

    def test_private_proc_no_export_tag(self) -> None:
        _, graph = _parse_sample()
        helper = next(n for n in graph.nodes.values() if n.name == "helper_proc")
        if helper.docstring:
            assert "[export]" not in helper.docstring

    def test_entrypoint_docstring(self) -> None:
        _, graph = _parse_sample()
        begin = next(n for n in graph.nodes.values() if n.name == "begin")
        assert begin.docstring is not None
        assert "Main entrypoint" in begin.docstring


class TestMasmParserComplexity:
    def test_simple_proc_complexity(self) -> None:
        _, graph = _parse_sample()
        add_vals = next(n for n in graph.nodes.values() if n.name == "add_values")
        assert add_vals.cyclomatic_complexity == 1

    def test_branching_proc_complexity(self) -> None:
        _, graph = _parse_sample()
        cond = next(n for n in graph.nodes.values() if n.name == "conditional_branch")
        assert cond.cyclomatic_complexity is not None
        # if + while + repeat = 3 branches, so complexity = 4
        assert cond.cyclomatic_complexity >= 4

    def test_branches_tracked(self) -> None:
        _, graph = _parse_sample()
        cond = next(n for n in graph.nodes.values() if n.name == "conditional_branch")
        assert len(cond.branches) >= 3
        conditions = {b.condition for b in cond.branches}
        assert "if.true" in conditions
        assert "while.true" in conditions

    def test_repeat_branch_has_count(self) -> None:
        _, graph = _parse_sample()
        cond = next(n for n in graph.nodes.values() if n.name == "conditional_branch")
        repeat_branches = [b for b in cond.branches if b.condition.startswith("repeat")]
        assert len(repeat_branches) == 1
        assert "3" in repeat_branches[0].condition


class TestMasmParserEdges:
    def test_contains_edges(self) -> None:
        _, graph = _parse_sample()
        contains = [e for e in graph.edges if e.kind == EdgeKind.CONTAINS]
        assert len(contains) > 0

    def test_call_edges_from_entrypoint(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        begin_calls = [e for e in calls if "begin" in e.source_id]
        # exec.add_values, call.std::math::u64::checked_add, exec.helper_proc
        assert len(begin_calls) == 3

    def test_local_call_is_certain(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        local_calls = [e for e in calls if "begin" in e.source_id and "add_values" in e.target_id]
        assert len(local_calls) == 1
        assert local_calls[0].confidence == EdgeConfidence.CERTAIN

    def test_cross_module_call_is_inferred(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        cross_calls = [e for e in calls if "begin" in e.source_id and "checked_add" in e.target_id]
        assert len(cross_calls) == 1
        assert cross_calls[0].confidence == EdgeConfidence.INFERRED


class TestMasmParserDependencies:
    def test_imports_tracked(self) -> None:
        _, graph = _parse_sample()
        assert "std" in graph.dependencies


class TestMasmParserNumLocals:
    def test_num_locals_as_parameter(self) -> None:
        _, graph = _parse_sample()
        helper = next(n for n in graph.nodes.values() if n.name == "helper_proc")
        assert len(helper.parameters) == 1
        assert helper.parameters[0].name == "num_locals"
        assert helper.parameters[0].default == "2"


class TestMasmParseDirectory:
    def test_parses_multiple_files(self) -> None:
        parser = MasmParser()
        code_a = "pub proc foo\n    nop\nend\n"
        code_b = "pub proc bar\n    nop\nend\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            for name, code in [("a.masm", code_a), ("b.masm", code_b)]:
                path = os.path.join(tmpdir, name)
                with open(path, "w") as f:
                    f.write(code)
            graph = parser.parse_directory(tmpdir)
        assert graph.language == "masm"
        assert graph.root_path == tmpdir
        names = {n.name for n in graph.nodes.values()}
        assert "foo" in names
        assert "bar" in names

    def test_ignores_wrong_extensions(self) -> None:
        parser = MasmParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "skip.txt")
            with open(path, "w") as f:
                f.write("not source code")
            graph = parser.parse_directory(tmpdir)
        assert len(graph.nodes) == 0
