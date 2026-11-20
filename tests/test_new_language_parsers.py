"""Focused tests for newer language parser guarantees."""

from __future__ import annotations

from pathlib import Path

from trailmark.models.edges import EdgeKind
from trailmark.parse import parse_file


def test_tact_direct_calls_are_call_edges(tmp_path: Path) -> None:
    source = tmp_path / "wallet.tact"
    source.write_text(
        'contract Wallet { receive("ping") { self.reply(); } fun reply() { return; } }\n'
    )

    graph = parse_file(str(source), language="tact")
    call_edges = {
        (edge.source_id, edge.target_id) for edge in graph.edges if edge.kind == EdgeKind.CALLS
    }

    assert ("wallet:Wallet.receive", "wallet:Wallet.reply") in call_edges


def test_tact_repeated_receivers_get_distinct_nodes(tmp_path: Path) -> None:
    source = tmp_path / "wallet.tact"
    source.write_text(
        'contract Wallet { receive("ping") { } receive("pong") { } fun reply() { } }\n'
    )

    graph = parse_file(str(source), language="tact")

    assert "wallet:Wallet.receive" in graph.nodes
    assert "wallet:Wallet.receive_2" in graph.nodes


def test_tact_traits_retain_methods_and_inclusion_relationships(tmp_path: Path) -> None:
    source = tmp_path / "wallet.tact"
    source.write_text(
        "trait Base { fun check() {} }\n"
        "trait Trackable with Base {\n"
        "  fun track() { self.filter(); }\n"
        "  virtual fun filter(): Bool { return true; }\n"
        '  receive("track") { self.track(); }\n'
        "}\n"
        'contract Wallet with Trackable { receive("ping") { self.track(); } }\n'
    )

    graph = parse_file(str(source), language="tact", strict=True)

    assert graph.nodes["wallet:Trackable"].kind.value == "trait"
    assert graph.nodes["wallet:Trackable.track"].kind.value == "method"
    assert {
        (edge.source_id, edge.target_id) for edge in graph.edges if edge.kind == EdgeKind.INHERITS
    } == {("wallet:Trackable", "wallet:Base"), ("wallet:Wallet", "wallet:Trackable")}
    assert any(
        edge.source_id == "wallet:Trackable.track"
        and edge.target_id == "wallet:Trackable.filter"
        and edge.kind == EdgeKind.CALLS
        for edge in graph.edges
    )
    assert any(
        edge.source_id == "wallet:Trackable.receive"
        and edge.target_id == "wallet:Trackable.track"
        and edge.kind == EdgeKind.CALLS
        for edge in graph.edges
    )


def test_schema_languages_do_not_emit_runtime_call_edges(tmp_path: Path) -> None:
    samples = [
        (
            "auth.proto",
            "proto",
            'syntax = "proto3"; service Auth { rpc Login (Request) returns (Response); } '
            "message Request { string token = 1; } message Response { bool ok = 1; }\n",
        ),
        (
            "auth.thrift",
            "thrift",
            "struct Request { 1: string token }\nservice Auth { bool login(1: Request req) }\n",
        ),
        (
            "schema.graphql",
            "graphql",
            "type Query { user(id: ID!): User }\ntype User { id: ID! }\n",
        ),
    ]

    for filename, language, text in samples:
        path = tmp_path / filename
        path.write_text(text)
        graph = parse_file(str(path), language=language)
        assert [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS] == []


def test_thrift_generic_parameter_types_preserve_closing_brackets(tmp_path: Path) -> None:
    source = tmp_path / "store.thrift"
    source.write_text(
        "service Store { list<string> take("
        "1: list<string> names, 2: map<string, list<i64>> groups) }\n"
    )

    graph = parse_file(str(source))
    method = graph.nodes["store:Store.take"]

    assert [p.type_ref.name for p in method.parameters if p.type_ref is not None] == [
        "list<string>",
        "map<string, list<i64>>",
    ]
    assert method.return_type is not None
    assert method.return_type.name == "list<string>"
