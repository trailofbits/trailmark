"""Regression tests for direct Solidity persistent-state dependencies."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trailmark.models import CodeGraph, EdgeConfidence, EdgeKind, NodeKind
from trailmark.parsers.solidity.parser import SolidityParser
from trailmark.query.api import QueryEngine
from trailmark.storage.graph_store import GraphStore


def _parse(tmp_path: Path, source: str) -> CodeGraph:
    path = tmp_path / "state.sol"
    path.write_text(source)
    return SolidityParser().parse_file(str(path))


def _accesses(graph: CodeGraph, function: str = "f") -> set[tuple[str, str]]:
    return {
        (graph.nodes[e.target_id].name, e.kind.value)
        for e in graph.edges
        if e.source_id == f"state:C.{function}" and e.kind in {EdgeKind.READS, EdgeKind.WRITES}
    }


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        ("x = y;", {("x", "writes"), ("y", "reads")}),
        ("x += y;", {("x", "reads"), ("x", "writes"), ("y", "reads")}),
        ("x++;", {("x", "reads"), ("x", "writes")}),
        ("--x;", {("x", "reads"), ("x", "writes")}),
        ("delete x;", {("x", "writes")}),
        ("return x;", {("x", "reads")}),
        ("x = ~y;", {("x", "writes"), ("y", "reads")}),
        ("items[x] = y;", {("items", "writes"), ("x", "reads"), ("y", "reads")}),
        ("items[x]++;", {("items", "reads"), ("items", "writes"), ("x", "reads")}),
        ("delete items[x];", {("items", "writes"), ("x", "reads")}),
        ("balances[x] = y;", {("balances", "writes"), ("x", "reads"), ("y", "reads")}),
        ("delete balances[x];", {("balances", "writes"), ("x", "reads")}),
        ("data.x = y;", {("data", "writes"), ("y", "reads")}),
        ("items.push(x);", {("items", "reads"), ("items", "writes"), ("x", "reads")}),
        ("items.pop();", {("items", "reads"), ("items", "writes")}),
        ("items.custom(x);", {("items", "reads"), ("x", "reads")}),
        ("getItems().push(x);", {("x", "reads")}),
        ("data.push(y);", {("data", "reads"), ("y", "reads")}),
        ("uint[] memory items; items.push(x);", {("x", "reads")}),
        ("x = items.length;", {("x", "writes"), ("items", "reads")}),
        (
            "(x, y) = (y, x);",
            {("x", "reads"), ("x", "writes"), ("y", "reads"), ("y", "writes")},
        ),
        ("g({x: y});", {("y", "reads")}),
        ("assembly { sstore(0, x) }", set()),
    ],
)
def test_direct_accesses(tmp_path: Path, statement: str, expected: set[tuple[str, str]]) -> None:
    graph = _parse(
        tmp_path,
        "contract C { uint x; uint y; uint[] items; mapping(uint => uint) balances;"
        "struct Data { uint x; } Data data;"
        f"function f() public returns (uint result) {{ {statement} }} }}",
    )
    assert _accesses(graph) == expected


def test_state_declarations_are_persistent_owned_and_order_independent(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        """contract C {
            function f() public { x = MAX + start + lock; }
            uint public x;
            uint constant MAX = 42;
            uint immutable start;
            uint transient lock;
        }""",
    )
    states = [node for node in graph.nodes.values() if node.kind == NodeKind.STATE_VARIABLE]
    assert [node.id for node in states] == ["state:C.x"]
    assert dict(states[0].attributes) == {"declared_type": "uint"}
    assert states[0].location.start_line == 3
    assert any(
        e.source_id == "state:C" and e.target_id == "state:C.x" and e.kind == EdgeKind.CONTAINS
        for e in graph.edges
    )
    assert _accesses(graph) == {("x", "writes")}


@pytest.mark.parametrize(
    ("signature", "body", "expected"),
    [
        ("uint x", "x = y;", {("y", "reads")}),
        ("", "uint x = y; x++;", {("y", "reads")}),
        ("", "{ uint x = y; x++; } x++;", {("x", "reads"), ("x", "writes"), ("y", "reads")}),
        ("", "for (uint x = 0; x < y; x++) { x; } x = y;", {("x", "writes"), ("y", "reads")}),
        ("", "(uint x, uint z) = (y, y); x = z;", {("y", "reads")}),
        ("Data memory other", "other.x = y;", {("y", "reads")}),
    ],
)
def test_lexical_shadowing(
    tmp_path: Path, signature: str, body: str, expected: set[tuple[str, str]]
) -> None:
    graph = _parse(
        tmp_path,
        "contract C { uint x; uint y; struct Data { uint x; }"
        f"function f({signature}) public {{ {body} }} }}",
    )
    assert _accesses(graph) == expected


def test_named_returns_and_catch_parameters_shadow_state(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        """contract C {
            uint x; string reason; uint failures;
            function f() public returns (uint x) { x++; }
            function attempt() public {
                try other() returns (uint x) { x++; }
                catch Error(string memory reason) { reason; failures++; }
                x++;
            }
        }""",
    )
    assert _accesses(graph) == set()
    assert _accesses(graph, "attempt") == {
        ("x", "reads"),
        ("x", "writes"),
        ("failures", "reads"),
        ("failures", "writes"),
    }


def test_constructor_modifier_and_modifier_arguments_keep_direct_ownership(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        """contract C {
            uint x;
            constructor() { x = 1; }
            modifier check(uint value) { require(x > value); _; }
            function f() public check(x) {}
            function g() public check(0) {}
        }""",
    )
    assert _accesses(graph, "constructor") == {("x", "writes")}
    assert _accesses(graph, "check") == {("x", "reads")}
    assert _accesses(graph) == {("x", "reads")}
    assert _accesses(graph, "g") == set()


def test_contracts_do_not_share_same_named_state(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        "contract C { uint x; function f() public { x = 1; } }"
        "contract D { uint x; function f() public { x = 2; } }",
    )
    writes = [e for e in graph.edges if e.kind == EdgeKind.WRITES]
    assert [(e.source_id, e.target_id) for e in writes] == [
        ("state:C.f", "state:C.x"),
        ("state:D.f", "state:D.x"),
    ]


def test_access_sites_survive_export_and_queries_deduplicate(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        "contract C {\n uint x;\n function f() public {\n x++;\n x++;\n }\n}",
    )
    edges = [e for e in graph.edges if e.kind in {EdgeKind.READS, EdgeKind.WRITES}]
    assert len(edges) == 4
    assert {e.location.start_line for e in edges if e.location} == {4, 5}
    assert all(e.confidence == EdgeConfidence.CERTAIN for e in edges)
    engine = QueryEngine.from_graph(graph)
    assert [n["id"] for n in engine.state_read_by("C.f")] == ["state:C.x"]
    assert [n["id"] for n in engine.state_written_by("C.f")] == ["state:C.x"]
    assert [n["id"] for n in engine.readers_of("C.x")] == ["state:C.f"]
    assert [n["id"] for n in engine.writers_of("C.x")] == ["state:C.f"]
    assert engine.callees_of("C.f") == []
    assert engine.reachable_from("C.f") == []
    assert {
        tuple(path)
        for path in GraphStore(graph).paths_between(
            "state:C.f", "state:C.x", edge_kinds=frozenset({EdgeKind.WRITES})
        )
    } == {("state:C.f", "state:C.x")}
    exported = json.loads(engine.to_json())
    assert exported["nodes"]["state:C.x"]["kind"] == "state_variable"
    exported_accesses = [e for e in exported["edges"] if e["kind"] in {"reads", "writes"}]
    assert len(exported_accesses) == 4
    assert {e["location"]["start_line"] for e in exported_accesses} == {4, 5}
