"""Tests for the Cairo language parser."""

from __future__ import annotations

import os
import tempfile

import pytest
from tree_sitter import Node

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind
from trailmark.parsers.cairo.parser import CairoParser, _first_path_segment

SAMPLE_CODE = """\
use starknet::ContractAddress;

/// A counter interface.
#[starknet::interface]
trait ICounter<TContractState> {
    fn get_counter(self: @TContractState) -> u128;
    fn increase_counter(ref self: TContractState);
}

#[starknet::contract]
mod Counter {
    use starknet::get_caller_address;

    struct Storage {
        counter: u128,
        owner: ContractAddress,
    }

    enum Event {
        CounterIncreased: CounterIncreased,
    }

    struct CounterIncreased {
        amount: u128,
    }

    impl CounterImpl of super::ICounter<ContractState> {
        fn get_counter(self: @ContractState) -> u128 {
            self.counter.read()
        }

        fn increase_counter(ref self: ContractState) {
            let current = self.counter.read();
            if current > 100 {
                self.counter.write(0);
            } else {
                self.counter.write(current + 1);
            }
        }
    }

    fn internal_helper(x: u128, y: u128) -> u128 {
        if x > y {
            x - y
        } else {
            y - x
        }
    }
}

/// Adds one to the input.
fn standalone_func(a: u128) -> u128 {
    a + 1
}

struct ExternalPoint {
    x: u128,
    y: u128,
}

enum Color {
    Red,
    Green,
    Blue,
}
"""


def _parse_sample() -> tuple[CairoParser, CodeGraph]:
    parser = CairoParser()
    with tempfile.NamedTemporaryFile(
        suffix=".cairo",
        mode="w",
        delete=False,
    ) as f:
        f.write(SAMPLE_CODE)
        f.flush()
        graph = parser.parse_file(f.name)
    os.unlink(f.name)
    return parser, graph


class TestCairoParserNodes:
    def test_finds_module(self) -> None:
        _, graph = _parse_sample()
        modules = [n for n in graph.nodes.values() if n.kind == NodeKind.MODULE]
        assert len(modules) == 1

    def test_finds_contract(self) -> None:
        _, graph = _parse_sample()
        contracts = [n for n in graph.nodes.values() if n.kind == NodeKind.CONTRACT]
        names = {c.name for c in contracts}
        assert "Counter" in names

    def test_finds_trait(self) -> None:
        _, graph = _parse_sample()
        traits = [n for n in graph.nodes.values() if n.kind == NodeKind.TRAIT]
        names = {t.name for t in traits}
        assert "ICounter" in names

    def test_finds_structs(self) -> None:
        _, graph = _parse_sample()
        structs = [n for n in graph.nodes.values() if n.kind == NodeKind.STRUCT]
        names = {s.name for s in structs}
        assert "Storage" in names
        assert "CounterIncreased" in names
        assert "ExternalPoint" in names

    def test_finds_enums(self) -> None:
        _, graph = _parse_sample()
        enums = [n for n in graph.nodes.values() if n.kind == NodeKind.ENUM]
        names = {e.name for e in enums}
        assert "Event" in names
        assert "Color" in names

    def test_finds_functions(self) -> None:
        _, graph = _parse_sample()
        funcs = [n for n in graph.nodes.values() if n.kind == NodeKind.FUNCTION]
        names = {f.name for f in funcs}
        assert "standalone_func" in names

    def test_finds_methods(self) -> None:
        _, graph = _parse_sample()
        methods = [n for n in graph.nodes.values() if n.kind == NodeKind.METHOD]
        names = {m.name for m in methods}
        assert "get_counter" in names
        assert "increase_counter" in names

    def test_finds_internal_function_in_mod(self) -> None:
        _, graph = _parse_sample()
        methods = [n for n in graph.nodes.values() if n.kind == NodeKind.METHOD]
        names = {m.name for m in methods}
        assert "internal_helper" in names

    def test_function_docstring(self) -> None:
        _, graph = _parse_sample()
        func = next(n for n in graph.nodes.values() if n.name == "standalone_func")
        assert func.docstring is not None
        assert "Adds one" in func.docstring

    def test_method_id_includes_impl(self) -> None:
        _, graph = _parse_sample()
        method_ids = [n.id for n in graph.nodes.values() if n.kind == NodeKind.METHOD]
        has_impl = any("CounterImpl" in mid for mid in method_ids)
        assert has_impl


class TestCairoParserParameters:
    def test_function_params(self) -> None:
        _, graph = _parse_sample()
        standalone = next(n for n in graph.nodes.values() if n.name == "standalone_func")
        assert len(standalone.parameters) == 1
        assert standalone.parameters[0].name == "a"

    def test_param_type(self) -> None:
        _, graph = _parse_sample()
        standalone = next(n for n in graph.nodes.values() if n.name == "standalone_func")
        assert standalone.parameters[0].type_ref is not None
        assert "u128" in standalone.parameters[0].type_ref.name

    def test_method_params_skip_self(self) -> None:
        _, graph = _parse_sample()
        get_counter = next(n for n in graph.nodes.values() if n.name == "get_counter")
        assert len(get_counter.parameters) == 0

    def test_helper_params(self) -> None:
        _, graph = _parse_sample()
        helper = next(n for n in graph.nodes.values() if n.name == "internal_helper")
        assert len(helper.parameters) == 2
        names = {p.name for p in helper.parameters}
        assert "x" in names
        assert "y" in names

    def test_return_type(self) -> None:
        _, graph = _parse_sample()
        standalone = next(n for n in graph.nodes.values() if n.name == "standalone_func")
        assert standalone.return_type is not None
        assert "u128" in standalone.return_type.name


class TestCairoParserComplexity:
    def test_simple_function_complexity(self) -> None:
        _, graph = _parse_sample()
        standalone = next(n for n in graph.nodes.values() if n.name == "standalone_func")
        assert standalone.cyclomatic_complexity == 1

    def test_branching_function_complexity(self) -> None:
        _, graph = _parse_sample()
        helper = next(n for n in graph.nodes.values() if n.name == "internal_helper")
        assert helper.cyclomatic_complexity is not None
        assert helper.cyclomatic_complexity >= 2

    def test_branches_tracked(self) -> None:
        _, graph = _parse_sample()
        # Find the impl version (has a body), not the trait signature.
        increase = next(
            n
            for n in graph.nodes.values()
            if n.name == "increase_counter" and "CounterImpl" in n.id
        )
        assert len(increase.branches) > 0


class TestCairoParserEdges:
    def test_contains_edges(self) -> None:
        _, graph = _parse_sample()
        contains = [e for e in graph.edges if e.kind == EdgeKind.CONTAINS]
        assert len(contains) > 0

    def test_implements_edge(self) -> None:
        _, graph = _parse_sample()
        implements = [e for e in graph.edges if e.kind == EdgeKind.IMPLEMENTS]
        assert len(implements) == 1
        assert "CounterImpl" in implements[0].source_id

    def test_call_edges(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        assert len(calls) > 0

    def test_call_confidence(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        inferred = [e for e in calls if e.confidence == EdgeConfidence.INFERRED]
        assert len(inferred) > 0

    def test_contract_contains_struct(self) -> None:
        _, graph = _parse_sample()
        contains = [e for e in graph.edges if e.kind == EdgeKind.CONTAINS]
        counter_contains = [e for e in contains if "Counter" in e.source_id]
        assert len(counter_contains) > 0


class TestCairoParserDependencies:
    def test_imports_tracked(self) -> None:
        _, graph = _parse_sample()
        assert "starknet" in graph.dependencies

    def test_deeply_nested_import(self) -> None:
        """Triple-nested use path should extract the first segment."""
        parser = CairoParser()
        code = "use core::starknet::storage::StorageAccess;\n"
        with tempfile.NamedTemporaryFile(
            suffix=".cairo",
            mode="w",
            delete=False,
        ) as f:
            f.write(code)
            f.flush()
            graph = parser.parse_file(f.name)
        os.unlink(f.name)
        assert "core" in graph.dependencies


class TestFirstPathSegment:
    def _parse_use(self, code: str) -> Node:
        """Parse a use declaration and return the scoped_identifier node."""
        from tree_sitter import Parser
        from tree_sitter_language_pack import get_language

        p = Parser(get_language("cairo"))
        tree = p.parse(code.encode())
        # Navigate: program > cairo_1_file > use_declaration > scoped_identifier
        file_node = tree.root_node.children[0]
        use_decl = file_node.children[0]
        for child in use_decl.children:
            if child.type in ("scoped_identifier", "identifier"):
                return child
        return use_decl

    def test_simple_identifier(self) -> None:
        node = self._parse_use("use foo;")
        assert _first_path_segment(node) == "foo"

    def test_scoped_two_levels(self) -> None:
        node = self._parse_use("use starknet::ContractAddress;")
        assert _first_path_segment(node) == "starknet"

    def test_scoped_three_levels(self) -> None:
        node = self._parse_use("use core::starknet::storage;")
        assert _first_path_segment(node) == "core"

    def test_returns_empty_for_no_identifier(self) -> None:
        """A synthetic node with no identifier children returns empty string."""
        from tree_sitter import Parser
        from tree_sitter_language_pack import get_language

        p = Parser(get_language("cairo"))
        tree = p.parse(b"fn f() {}")
        # Use the function_definition node, which has no identifier
        # in the scoped_identifier sense - just pass a node that
        # won't match any branch
        fn_node = tree.root_node.children[0].children[0]
        # Get the block node (has no identifier children in path context)
        for child in fn_node.children:
            if child.type == "block":
                result = _first_path_segment(child)
                assert result == ""
                return
        pytest.skip("Could not find a block node")


class TestCairoParseDirectory:
    def test_parses_multiple_files(self) -> None:
        parser = CairoParser()
        code_a = "fn from_a() {}\n"
        code_b = "fn from_b() {}\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            for name, code in [("a.cairo", code_a), ("b.cairo", code_b)]:
                path = os.path.join(tmpdir, name)
                with open(path, "w") as f:
                    f.write(code)
            graph = parser.parse_directory(tmpdir)
        assert graph.language == "cairo"
        assert graph.root_path == tmpdir
        names = {n.name for n in graph.nodes.values()}
        assert "from_a" in names
        assert "from_b" in names

    def test_ignores_wrong_extensions(self) -> None:
        parser = CairoParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "skip.txt")
            with open(path, "w") as f:
                f.write("not source code")
            graph = parser.parse_directory(tmpdir)
        assert len(graph.nodes) == 0
