"""Human references keep finding positioned declarations and overload bodies."""

from pathlib import Path

import pytest

from trailmark import diagram
from trailmark.models import CodeGraph, CodeUnit, NodeKind, NodeOrigin, SourceLocation
from trailmark.parse import parse_file
from trailmark.query.api import QueryEngine
from trailmark.storage.graph_store import GraphStore


@pytest.mark.parametrize("with_proxy", [False, True])
@pytest.mark.parametrize("reverse_order", [False, True])
def test_positioned_aliases_preserve_first_match_and_explicit_ids(
    with_proxy: bool, reverse_order: bool
) -> None:
    overloads = [
        CodeUnit(
            id=f"mod:C.transfer@{line}_2",
            name="transfer",
            kind=NodeKind.METHOD,
            location=SourceLocation("mod.sol", line, line, 2),
        )
        for line in (10, 20)
    ]
    if reverse_order:
        overloads.reverse()
    proxy = CodeUnit(
        id="proxy.unresolved:mod:C.transfer",
        name="C.transfer",
        kind=NodeKind.PROXY,
        origin=NodeOrigin.PROXY,
        location=SourceLocation("mod.sol", 1, 1),
    )
    units = [proxy, *overloads] if with_proxy else overloads
    store = GraphStore(CodeGraph(nodes={unit.id: unit for unit in units}))

    for reference in ("transfer", "C.transfer", "mod:C.transfer"):
        assert store.find_node_id(reference) == overloads[0].id
    for unit in overloads:
        assert store.find_node_id(unit.id) == unit.id
        assert store.find_node_id(unit.id.removeprefix("mod:")) == unit.id
    if with_proxy:
        assert store.find_node_id(proxy.id) == proxy.id


def test_other_at_suffixes_do_not_match_as_positions() -> None:
    unit = CodeUnit(
        id="mod:C.transfer@release_2",
        name="versioned_transfer",
        kind=NodeKind.METHOD,
        location=SourceLocation("mod.sol", 1, 1),
    )
    store = GraphStore(CodeGraph(nodes={unit.id: unit}))

    assert store.find_node_id("C.transfer") is None
    assert store.find_node_id("C.transfer@release_2") == unit.id


@pytest.mark.parametrize("with_call", [False, True])
def test_qualified_solidity_overload_queries_find_state_writer(
    tmp_path: Path, with_call: bool
) -> None:
    path = tmp_path / "NFT.sol"
    call = "safeTransferFrom(a);" if with_call else ""
    path.write_text(
        "contract NFT {\n"
        "uint owners;\n"
        "function safeTransferFrom(uint a) public { owners = a; }\n"
        f"function safeTransferFrom(uint a, bytes memory b) public {{ {call} }}\n"
        "}\n"
    )
    graph = parse_file(str(path), language="solidity")
    engine = QueryEngine.from_graph(graph)
    writer = engine.writers_of("NFT.owners")[0]
    overloads = [unit for unit in graph.nodes.values() if unit.name == "safeTransferFrom"]
    exported = diagram._load_graph(engine)

    assert len(overloads) == 2
    assert writer["id"] == overloads[0].id
    assert [unit["id"] for unit in engine.state_written_by("NFT.safeTransferFrom")] == [
        "NFT:NFT.owners"
    ]
    assert diagram._find_focus_node(exported, "NFT.safeTransferFrom") == writer["id"]
    assert engine.state_written_by(overloads[1].id) == []
    if with_call:
        proxy = next(unit for unit in graph.nodes.values() if unit.origin == NodeOrigin.PROXY)
        assert diagram._find_focus_node(exported, proxy.id) == proxy.id
