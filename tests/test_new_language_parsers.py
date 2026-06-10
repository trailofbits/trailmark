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
