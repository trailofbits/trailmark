"""Focused tests for newer language parser guarantees."""

from __future__ import annotations

from pathlib import Path

from trailmark.models.edges import EdgeKind
from trailmark.parse import parse_directory, parse_file


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


def test_graphql_implements_relationships_include_interfaces_and_extensions(tmp_path: Path) -> None:
    (tmp_path / "types.graphql").write_text(
        "interface Node { id: ID! }\n"
        "interface Named implements Node { id: ID!, name: String }\n"
        "interface Extra { value: Int }\n"
        "type User implements Node & Named { id: ID!, name: String }\n"
    )
    (tmp_path / "extension.graphql").write_text("extend type User implements Extra\n")
    (tmp_path / "interface_extension.graphql").write_text(
        "extend interface Named implements Extra\n"
    )

    graph = parse_directory(str(tmp_path), language="graphql")

    edges = [edge for edge in graph.edges if edge.kind == EdgeKind.IMPLEMENTS]
    assert {(edge.source_id, edge.target_id) for edge in edges} == {
        ("types:Named", "types:Node"),
        ("types:User", "types:Node"),
        ("types:User", "types:Named"),
        ("types:User", "types:Extra"),
        ("types:Named", "types:Extra"),
    }
    assert all(edge.location is not None for edge in edges)
    assert not [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]


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
