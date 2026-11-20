"""Modifier bodies stay reachable from functions that invoke them."""

from pathlib import Path

from trailmark.models.edges import EdgeKind
from trailmark.parse import parse_directory, parse_file
from trailmark.query.api import QueryEngine


def test_modifier_invocations_preserve_inheritance_arguments_and_repeated_sites(
    tmp_path: Path,
) -> None:
    source = tmp_path / "auth.sol"
    source.write_text(
        "contract Base {\n"
        "  modifier requiresAuth(uint value) { check(value); _; }\n"
        "  function check(uint value) internal {}\n"
        "  function amount() internal returns (uint) { return 1; }\n"
        "}\n"
        "contract Child is Base {\n"
        "  function pay() external requiresAuth(amount()) requiresAuth(2) {}\n"
        "}\n"
    )

    graph = parse_file(str(source), language="solidity", strict=True)
    modifiers = [
        edge
        for edge in graph.edges
        if edge.kind == EdgeKind.CALLS
        and edge.source_id == "auth:Child.pay"
        and edge.target_id == "auth:Base.requiresAuth"
    ]

    assert len(modifiers) == 2
    assert len({edge.location.start_col for edge in modifiers if edge.location}) == 2
    engine = QueryEngine.from_graph(graph)
    assert engine.paths_between("auth:Child.pay", "auth:Base.check") == [
        ["auth:Child.pay", "auth:Base.requiresAuth", "auth:Base.check"]
    ]
    assert any(
        edge.source_id == "auth:Child.pay" and edge.target_id == "auth:Base.amount"
        for edge in graph.edges
        if edge.kind == EdgeKind.CALLS
    )


def test_constructor_base_invocations_are_not_mistaken_for_modifiers(tmp_path: Path) -> None:
    source = tmp_path / "constructor.sol"
    source.write_text(
        "contract Base { modifier guard() { _; } }\n"
        "contract Child is Base { constructor(uint guard) Base() guard {} }\n"
    )

    graph = parse_file(str(source), language="solidity", strict=True)
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]

    assert [(edge.source_id, edge.target_id) for edge in calls] == [
        ("constructor:Child.constructor", "constructor:Base.guard")
    ]
    assert dict(calls[0].attributes)["solidity_call_kind"] == "modifier"


def test_qualified_modifier_application_reaches_base_declaration(tmp_path: Path) -> None:
    source = tmp_path / "qualified.sol"
    source.write_text(
        "contract Base { modifier guard(uint value) { _; } }\n"
        "contract Child is Base { function run() external Base.guard(1) {} }\n"
    )

    graph = parse_file(str(source), language="solidity", strict=True)

    assert [
        (edge.source_id, edge.target_id) for edge in graph.edges if edge.kind == EdgeKind.CALLS
    ] == [("qualified:Child.run", "qualified:Base.guard")]


def test_missing_external_modifier_does_not_bind_free_function_decoy(tmp_path: Path) -> None:
    source = tmp_path / "missing.sol"
    source.write_text(
        'import "external.sol";\nfunction guard() {}\n'
        "contract Child is External { function run() external guard {} }\n"
    )

    graph = parse_file(str(source), language="solidity", strict=True)
    call = next(edge for edge in graph.edges if edge.kind == EdgeKind.CALLS)

    assert call.target_id != "missing:guard"
    assert graph.nodes[call.target_id].origin.value == "proxy"
    assert dict(call.attributes)["binding_blocked"] is True


def test_imported_base_modifier_resolves_after_directory_type_linking(tmp_path: Path) -> None:
    (tmp_path / "base.sol").write_text(
        "contract Base { modifier guard() { check(); _; } function check() internal {} }\n"
    )
    (tmp_path / "child.sol").write_text(
        'import {Base} from "./base.sol";\n'
        "contract Child is Base { function run() external guard {} }\n"
    )

    graph = parse_directory(str(tmp_path), language="solidity", strict=True)

    assert QueryEngine.from_graph(graph).paths_between("child:Child.run", "base:Base.check") == [
        ["child:Child.run", "base:Base.guard", "base:Base.check"]
    ]
