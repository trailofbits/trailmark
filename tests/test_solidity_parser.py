"""Tests for the Solidity language parser."""

from __future__ import annotations

import os
import tempfile

import pytest

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind
from trailmark.parsers.solidity.parser import SolidityParser

SAMPLE_CODE = """\
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "./IERC20.sol";

/// An ERC20-like token interface.
interface IERC20 {
    function totalSupply() external view returns (uint256);
    function transfer(address to, uint256 amount) external returns (bool);
    event Transfer(address indexed from, address indexed to, uint256 value);
}

library SafeMath {
    function add(uint256 a, uint256 b) internal pure returns (uint256) {
        uint256 c = a + b;
        require(c >= a, "overflow");
        return c;
    }
}

/// A simple token contract.
contract Token is IERC20 {
    string public name;
    mapping(address => uint256) private balances;
    address public owner;

    modifier onlyOwner() {
        require(msg.sender == owner, "not owner");
        _;
    }

    constructor(string memory _name) {
        name = _name;
        owner = msg.sender;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        if (balances[msg.sender] < amount) {
            revert("insufficient balance");
        }
        for (uint i = 0; i < 10; i++) {
            balances[to] += 1;
        }
        while (amount > 0) {
            amount--;
        }
        balances[msg.sender] -= amount;
        balances[to] += amount;
        return true;
    }

    function mint(address to, uint256 amount) public onlyOwner {
        balances[to] += amount;
    }

    function balanceOf(address account) public view returns (uint256) {
        return balances[account];
    }
}

struct Point {
    uint256 x;
    uint256 y;
}

enum Status { Active, Paused }
"""


def _parse_sample() -> tuple[SolidityParser, CodeGraph]:
    parser = SolidityParser()
    with tempfile.NamedTemporaryFile(
        suffix=".sol",
        mode="w",
        delete=False,
    ) as f:
        f.write(SAMPLE_CODE)
        f.flush()
        graph = parser.parse_file(f.name)
    os.unlink(f.name)
    return parser, graph


class TestSolidityParserNodes:
    @pytest.mark.mutation_fast
    def test_finds_module(self) -> None:
        _, graph = _parse_sample()
        modules = [n for n in graph.nodes.values() if n.kind == NodeKind.MODULE]
        assert len(modules) == 1

    def test_finds_contract(self) -> None:
        _, graph = _parse_sample()
        contracts = [n for n in graph.nodes.values() if n.kind == NodeKind.CONTRACT]
        names = {c.name for c in contracts}
        assert "Token" in names

    def test_finds_interface(self) -> None:
        _, graph = _parse_sample()
        interfaces = [n for n in graph.nodes.values() if n.kind == NodeKind.INTERFACE]
        names = {i.name for i in interfaces}
        assert "IERC20" in names

    def test_finds_library(self) -> None:
        _, graph = _parse_sample()
        libraries = [n for n in graph.nodes.values() if n.kind == NodeKind.LIBRARY]
        names = {lib.name for lib in libraries}
        assert "SafeMath" in names

    @pytest.mark.mutation_fast
    def test_finds_struct(self) -> None:
        _, graph = _parse_sample()
        structs = [n for n in graph.nodes.values() if n.kind == NodeKind.STRUCT]
        names = {s.name for s in structs}
        assert "Point" in names

    @pytest.mark.mutation_fast
    def test_finds_enum(self) -> None:
        _, graph = _parse_sample()
        enums = [n for n in graph.nodes.values() if n.kind == NodeKind.ENUM]
        names = {e.name for e in enums}
        assert "Status" in names

    @pytest.mark.mutation_fast
    def test_finds_methods(self) -> None:
        _, graph = _parse_sample()
        methods = [n for n in graph.nodes.values() if n.kind == NodeKind.METHOD]
        names = {m.name for m in methods}
        assert "transfer" in names
        assert "mint" in names
        assert "balanceOf" in names
        assert "constructor" in names
        assert "onlyOwner" in names

    def test_finds_library_methods(self) -> None:
        _, graph = _parse_sample()
        methods = [n for n in graph.nodes.values() if n.kind == NodeKind.METHOD]
        names = {m.name for m in methods}
        assert "add" in names

    @pytest.mark.mutation_fast
    def test_contract_docstring(self) -> None:
        _, graph = _parse_sample()
        token = next(n for n in graph.nodes.values() if n.name == "Token")
        assert token.docstring is not None
        assert "simple token" in token.docstring

    def test_interface_docstring(self) -> None:
        _, graph = _parse_sample()
        ierc20 = next(n for n in graph.nodes.values() if n.name == "IERC20")
        assert ierc20.docstring is not None
        assert "ERC20" in ierc20.docstring

    def test_method_id_includes_contract(self) -> None:
        _, graph = _parse_sample()
        method_ids = [n.id for n in graph.nodes.values() if n.kind == NodeKind.METHOD]
        has_token = any("Token" in mid for mid in method_ids)
        assert has_token


class TestSolidityParserParameters:
    @pytest.mark.mutation_fast
    def test_function_params(self) -> None:
        _, graph = _parse_sample()
        transfer = next(
            n
            for n in graph.nodes.values()
            if n.name == "transfer" and n.kind == NodeKind.METHOD and "Token" in n.id
        )
        assert len(transfer.parameters) == 2
        names = {p.name for p in transfer.parameters}
        assert "to" in names
        assert "amount" in names

    def test_param_type(self) -> None:
        _, graph = _parse_sample()
        transfer = next(
            n
            for n in graph.nodes.values()
            if n.name == "transfer" and n.kind == NodeKind.METHOD and "Token" in n.id
        )
        to_param = next(p for p in transfer.parameters if p.name == "to")
        assert to_param.type_ref is not None
        assert "address" in to_param.type_ref.name

    @pytest.mark.mutation_fast
    def test_return_type(self) -> None:
        _, graph = _parse_sample()
        transfer = next(
            n
            for n in graph.nodes.values()
            if n.name == "transfer" and n.kind == NodeKind.METHOD and "Token" in n.id
        )
        assert transfer.return_type is not None

    def test_constructor_params(self) -> None:
        _, graph = _parse_sample()
        ctor = next(n for n in graph.nodes.values() if n.name == "constructor")
        assert len(ctor.parameters) == 1
        assert ctor.parameters[0].name == "_name"

    def test_library_function_params(self) -> None:
        _, graph = _parse_sample()
        add_fn = next(n for n in graph.nodes.values() if n.name == "add")
        assert len(add_fn.parameters) == 2
        names = {p.name for p in add_fn.parameters}
        assert "a" in names
        assert "b" in names


class TestSolidityParserComplexity:
    def test_simple_function_complexity(self) -> None:
        _, graph = _parse_sample()
        balance = next(n for n in graph.nodes.values() if n.name == "balanceOf")
        assert balance.cyclomatic_complexity == 1

    def test_branching_function_complexity(self) -> None:
        _, graph = _parse_sample()
        transfer = next(
            n
            for n in graph.nodes.values()
            if n.name == "transfer" and n.kind == NodeKind.METHOD and "Token" in n.id
        )
        assert transfer.cyclomatic_complexity is not None
        assert transfer.cyclomatic_complexity >= 4

    def test_branches_tracked(self) -> None:
        _, graph = _parse_sample()
        transfer = next(
            n
            for n in graph.nodes.values()
            if n.name == "transfer" and n.kind == NodeKind.METHOD and "Token" in n.id
        )
        assert len(transfer.branches) > 0


class TestSolidityParserEdges:
    def test_contains_edges(self) -> None:
        _, graph = _parse_sample()
        contains = [e for e in graph.edges if e.kind == EdgeKind.CONTAINS]
        assert len(contains) > 0

    @pytest.mark.mutation_fast
    def test_inherits_edge(self) -> None:
        _, graph = _parse_sample()
        inherits = [e for e in graph.edges if e.kind == EdgeKind.INHERITS]
        assert len(inherits) == 1
        assert "Token" in inherits[0].source_id
        assert "IERC20" in inherits[0].target_id

    def test_call_edges(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        assert len(calls) > 0

    def test_call_confidence(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        certain = [e for e in calls if e.confidence == EdgeConfidence.CERTAIN]
        assert len(certain) > 0

    def test_method_contained_by_contract(self) -> None:
        _, graph = _parse_sample()
        contains = [e for e in graph.edges if e.kind == EdgeKind.CONTAINS]
        token_contains = [e for e in contains if "Token" in e.source_id]
        assert len(token_contains) > 0


class TestSolidityParserDependencies:
    @pytest.mark.mutation_fast
    def test_imports_tracked(self) -> None:
        _, graph = _parse_sample()
        assert "IERC20" in graph.dependencies


class TestSolidityParseDirectory:
    @pytest.mark.mutation_fast
    def test_parses_multiple_files(self) -> None:
        parser = SolidityParser()
        code_a = """\
contract A {
    function foo() public {}
}
"""
        code_b = """\
contract B {
    function bar() public {}
}
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            for name, code in [("a.sol", code_a), ("b.sol", code_b)]:
                path = os.path.join(tmpdir, name)
                with open(path, "w") as f:
                    f.write(code)
            graph = parser.parse_directory(tmpdir)
        assert graph.language == "solidity"
        assert graph.root_path == tmpdir
        names = {n.name for n in graph.nodes.values()}
        assert "A" in names
        assert "B" in names

    def test_ignores_wrong_extensions(self) -> None:
        parser = SolidityParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "skip.txt")
            with open(path, "w") as f:
                f.write("not source code")
            graph = parser.parse_directory(tmpdir)
        assert len(graph.nodes) == 0
