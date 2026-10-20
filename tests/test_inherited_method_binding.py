"""Preserve inherited Solidity reachability without guessing overloads."""

from pathlib import Path

import pytest

from trailmark.analysis.entrypoints import detect_entrypoints
from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeOrigin
from trailmark.parse import parse_directory, parse_file
from trailmark.parsers.solidity import SolidityParser
from trailmark.query.api import QueryEngine


def _calls(graph: CodeGraph, source: str) -> list[tuple[str, EdgeConfidence]]:
    return [
        (edge.target_id, edge.confidence)
        for edge in graph.edges
        if edge.kind == EdgeKind.CALLS and edge.source_id == source
    ]


@pytest.mark.parametrize("directory", [False, True])
def test_unique_inherited_method_remains_reachable(tmp_path: Path, directory: bool) -> None:
    base = "contract Base { uint total; function _mint(uint a) internal { total += a; } }"
    child = "contract Token is Base { function mint(uint a) external { _mint(a); } }"
    if directory:
        (tmp_path / "Base.sol").write_text(base)
        (tmp_path / "Token.sol").write_text('import "./Base.sol"; ' + child)
        graph = parse_directory(str(tmp_path), "solidity")
        target, source = "Base:Base._mint", "Token:Token.mint"
    else:
        path = tmp_path / "Token.sol"
        # The declaration order must not control reachability.
        path.write_text(child + "\n" + base)
        graph = parse_file(str(path), "solidity")
        target, source = "Token:Base._mint", "Token:Token.mint"
        assert _calls(SolidityParser().parse_file(str(path)), source) == [
            (target, EdgeConfidence.INFERRED)
        ]
    assert _calls(graph, source) == [(target, EdgeConfidence.INFERRED)]
    graph.entrypoints.update(detect_entrypoints(graph, str(tmp_path)))
    engine = QueryEngine.from_graph(graph)
    assert engine.entrypoint_paths_to("Base._mint") == [[source, target]]


def test_inherited_method_precedes_same_named_free_function(tmp_path: Path) -> None:
    (tmp_path / "Base.sol").write_text("contract Base { function work() internal {} }")
    (tmp_path / "Token.sol").write_text(
        "function work() pure {} contract Token is Base { function run() public { work(); } }"
    )
    graph = parse_directory(str(tmp_path), "solidity")
    assert _calls(graph, "Token:Token.run") == [("Base:Base.work", EdgeConfidence.INFERRED)]


@pytest.mark.parametrize("visibility", ["private", "external"])
@pytest.mark.parametrize("directory", [False, True])
def test_non_internal_base_methods_do_not_capture_free_calls(
    tmp_path: Path, visibility: str, directory: bool
) -> None:
    path = tmp_path / "Token.sol"
    base = f"contract Base {{ function work() {visibility} {{}} }}\n"
    child = "contract Token is Base { function run() public { work(); } }\nfunction work() pure {}"
    if directory:
        (tmp_path / "Base.sol").write_text(base)
        path.write_text(child)
        graph = parse_directory(str(tmp_path), "solidity")
    else:
        path.write_text(base + child)
        graph = parse_file(str(path), "solidity")
    assert _calls(graph, "Token:Token.run") == [("Token:work", EdgeConfidence.CERTAIN)]


def test_local_and_nearest_inherited_overrides_win(tmp_path: Path) -> None:
    path = tmp_path / "Token.sol"
    path.write_text(
        "contract A { function work(uint a) internal virtual {} }\n"
        "contract B is A { function work(uint b) internal override virtual {} }\n"
        "contract C is B { function run() public { work(1); } }\n"
        "contract D is B { function work(uint c) internal override {} "
        "function run() public { work(1); } }"
    )
    graph = parse_file(str(path), "solidity")
    assert _calls(graph, "Token:C.run") == [("Token:B.work", EdgeConfidence.INFERRED)]
    assert _calls(graph, "Token:D.run") == [("Token:D.work", EdgeConfidence.CERTAIN)]


@pytest.mark.parametrize(
    "bases",
    [
        "contract A { function work(uint a) internal {} "
        "function work(bytes memory b) internal {} } "
        "contract B is A {}",
        "contract A { function work(uint a) internal {} } "
        "contract B is A { function work(bytes memory b) internal {} }",
        "contract A { function work(uint a) internal virtual {} } "
        "contract B { function work(uint a) internal virtual {} }",
    ],
)
def test_inherited_ambiguity_does_not_fall_back_to_a_free_function(
    tmp_path: Path, bases: str
) -> None:
    path = tmp_path / "Token.sol"
    # Two independent base definitions are deliberately conservative without
    # compiler linearization; separate overloads never select by argument types.
    inheritance = "A, B" if "contract B {" in bases else "B"
    path.write_text(
        bases
        + f" contract Token is {inheritance} {{ function run() public {{ work(1); }} }}"
        + " function work(uint a) pure {}"
    )
    graph = parse_file(str(path), "solidity")
    [(target, confidence)] = _calls(graph, "Token:Token.run")
    assert graph.nodes[target].origin == NodeOrigin.PROXY
    assert confidence == EdgeConfidence.UNCERTAIN


def test_diamond_inheritance_deduplicates_one_method(tmp_path: Path) -> None:
    path = tmp_path / "Token.sol"
    path.write_text(
        "contract A { function work() internal {} }\n"
        "contract B is A {} contract C is A {}\n"
        "contract Token is B, C { function run() public { work(); } }"
    )
    graph = parse_file(str(path), "solidity")
    assert _calls(graph, "Token:Token.run") == [("Token:A.work", EdgeConfidence.INFERRED)]


def test_local_method_does_not_hide_a_different_inherited_signature(tmp_path: Path) -> None:
    (tmp_path / "Base.sol").write_text(
        "contract Base { function work(string memory x) internal {} }"
    )
    (tmp_path / "Token.sol").write_text(
        "contract Token is Base { function work(uint x) private {} "
        'function run() external { work("hello"); } }'
    )
    graph = parse_directory(str(tmp_path), "solidity")
    [(target, confidence)] = _calls(graph, "Token:Token.run")
    assert graph.nodes[target].origin == NodeOrigin.PROXY
    assert target != "Token:Token.work"
    assert confidence == EdgeConfidence.UNCERTAIN


def test_directory_reconsiders_a_binding_when_another_base_becomes_known(tmp_path: Path) -> None:
    (tmp_path / "Remote.sol").write_text(
        "contract Remote { function work(string memory x) internal {} }"
    )
    path = tmp_path / "Token.sol"
    path.write_text(
        "contract Local { function work(uint x) internal {} } "
        "contract Token is Local, Remote { "
        'function run() external { work("hello"); } }'
    )
    standalone = parse_file(str(path), "solidity")
    [(target, confidence)] = _calls(standalone, "Token:Token.run")
    assert target == "Token:Local.work"
    assert confidence == EdgeConfidence.INFERRED
    graph = parse_directory(str(tmp_path), "solidity")
    [(target, confidence)] = _calls(graph, "Token:Token.run")
    assert graph.nodes[target].origin == NodeOrigin.PROXY
    assert confidence == EdgeConfidence.UNCERTAIN


@pytest.mark.parametrize("directory", [False, True])
def test_unavailable_external_base_preserves_known_calls(tmp_path: Path, directory: bool) -> None:
    path = tmp_path / "Token.sol"
    path.write_text(
        'import "@openzeppelin/contracts/token/ERC20/ERC20.sol";\n'
        "function fee(uint a) pure returns (uint) { return a / 100; }\n"
        "contract Token is ERC20 { uint total;\n"
        " function _credit(uint a) internal { total += a; }\n"
        " function deposit(uint a) external { require(a > 0); _credit(fee(a)); } }"
    )
    graph = (
        parse_directory(str(tmp_path), "solidity")
        if directory
        else parse_file(str(path), "solidity")
    )
    calls = dict(_calls(graph, "Token:Token.deposit"))
    assert calls["Token:Token._credit"] == EdgeConfidence.INFERRED
    assert calls["Token:fee"] == EdgeConfidence.INFERRED
    unresolved = [
        graph.nodes[target] for target in calls if graph.nodes[target].origin == NodeOrigin.PROXY
    ]
    assert [unit.name for unit in unresolved] == ["Token:require"]
    graph.entrypoints.update(detect_entrypoints(graph, str(tmp_path)))
    engine = QueryEngine.from_graph(graph)
    assert engine.entrypoint_paths_to("Token._credit") == [
        ["Token:Token.deposit", "Token:Token._credit"]
    ]


def test_inheritance_cycle_terminates_without_binding_unrelated_methods(tmp_path: Path) -> None:
    path = tmp_path / "Token.sol"
    # Tree-sitter accepts syntactically valid input even when a compiler would
    # reject the inheritance cycle. Graph analysis still must terminate.
    path.write_text(
        "contract A is B { function run() public { work(); } } contract B is A {}\n"
        "contract Other { function work() internal {} }"
    )
    graph = parse_file(str(path), "solidity")
    [(target, _)] = _calls(graph, "Token:A.run")
    assert graph.nodes[target].origin == NodeOrigin.PROXY
