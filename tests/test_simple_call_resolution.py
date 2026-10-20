"""Preserve qualified call identities without changing other frontend bindings."""

from pathlib import Path

import pytest
from tree_sitter import Parser
from tree_sitter_language_pack import get_language

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeOrigin
from trailmark.parse import parse_file


def _parse_valid(tmp_path: Path, language: str, suffix: str, source: str) -> CodeGraph:
    tree = Parser(get_language(language)).parse(source.encode())
    assert not tree.root_node.has_error
    path = tmp_path / f"sample.{suffix}"
    path.write_text(source)
    return parse_file(str(path), language=language)


@pytest.mark.parametrize(
    ("language", "suffix", "source", "qualified_caller", "local_caller", "local_target", "raw"),
    [
        (
            "sway",
            "sw",
            "script; fn transfer() {} fn f() { coin::transfer(); transfer(); }",
            "sample:f",
            "sample:f",
            "sample:transfer",
            "coin::transfer",
        ),
        (
            "rego",
            "rego",
            "package p\ntransfer(x) { x = 1 }\nallow { coin.transfer(1) }\nlocal { transfer(1) }\n",
            "p:allow",
            "p:local",
            "p:transfer",
            "coin.transfer",
        ),
    ],
)
def test_qualified_calls_do_not_bind_to_local_namesakes(
    tmp_path: Path,
    language: str,
    suffix: str,
    source: str,
    qualified_caller: str,
    local_caller: str,
    local_target: str,
    raw: str,
) -> None:
    graph = _parse_valid(tmp_path, language, suffix, source)
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]

    assert len(calls) == 2
    unresolved = next(edge for edge in calls if dict(edge.attributes).get("raw_target") == raw)
    assert unresolved.source_id == qualified_caller
    assert unresolved.confidence == EdgeConfidence.UNCERTAIN
    proxy = graph.nodes[unresolved.target_id]
    assert proxy.origin == NodeOrigin.PROXY
    assert proxy.name == raw
    local = next(edge for edge in calls if edge.target_id == local_target)
    assert local.source_id == local_caller
    assert local.confidence == EdgeConfidence.CERTAIN


def test_tact_foreign_receiver_does_not_bind_to_receiver_named_method(tmp_path: Path) -> None:
    graph = _parse_valid(
        tmp_path,
        "tact",
        "tact",
        "contract C { fun other() {} fun transfer() {} "
        "fun f(other: C) { other.transfer(); self.transfer(); transfer(); } }",
    )
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]

    assert len(calls) == 3
    assert all(edge.source_id == "sample:C.f" for edge in calls)
    assert not any(edge.target_id == "sample:C.other" for edge in calls)
    unresolved = next(edge for edge in calls if "raw_target" in dict(edge.attributes))
    assert dict(unresolved.attributes)["raw_target"] == "other.transfer"
    assert graph.nodes[unresolved.target_id].name == "other.transfer"
    assert graph.nodes[unresolved.target_id].origin == NodeOrigin.PROXY
    assert unresolved.confidence == EdgeConfidence.UNCERTAIN
    local_calls = [edge for edge in calls if edge.target_id == "sample:C.transfer"]
    assert len(local_calls) == 2
    assert all(edge.confidence == EdgeConfidence.CERTAIN for edge in local_calls)


@pytest.mark.parametrize(
    ("language", "suffix", "source", "caller", "target", "count"),
    [
        (
            "func",
            "fc",
            "() transfer(slice s) { return (); } () f(slice s) { s.transfer(); transfer(s); }",
            "sample:f",
            "sample:transfer",
            2,
        ),
        (
            "move",
            "move",
            "module 0x1::m { fun transfer() {} fun f(){ transfer(); } }",
            "sample:0x1::m:f",
            "sample:0x1::m:transfer",
            1,
        ),
        (
            "tact",
            "tact",
            "fun transfer() {} fun f() { transfer(); }",
            "sample:f",
            "sample:transfer",
            1,
        ),
    ],
)
def test_local_call_controls(
    tmp_path: Path,
    language: str,
    suffix: str,
    source: str,
    caller: str,
    target: str,
    count: int,
) -> None:
    graph = _parse_valid(tmp_path, language, suffix, source)
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]

    assert len(calls) == count
    assert all(edge.source_id == caller and edge.target_id == target for edge in calls)
    assert all(edge.confidence == EdgeConfidence.CERTAIN for edge in calls)
