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


def test_thrift_service_inheritance_retains_local_and_included_bases(tmp_path: Path) -> None:
    (tmp_path / "base.thrift").write_text("service Parent { void ping() }\n")
    client = tmp_path / "client.thrift"
    client.write_text(
        'include "base.thrift"\n'
        "service Local {}\n"
        "service Child extends Local {}\n"
        "service Imported extends base.Parent {}\n"
    )

    graph = parse_directory(str(tmp_path), language="thrift")
    edges = [edge for edge in graph.edges if edge.kind == EdgeKind.INHERITS]

    assert {(edge.source_id, edge.target_id) for edge in edges} == {
        ("client:Child", "client:Local"),
        ("client:Imported", "base:Parent"),
    }
    assert all(edge.location is not None for edge in edges)
    assert sorted(
        (edge.location.start_line, edge.location.end_line)
        for edge in edges
        if edge.location is not None
    ) == [(3, 3), (4, 4)]


def test_thrift_service_base_comments_do_not_change_names(tmp_path: Path) -> None:
    (tmp_path / "base.thrift").write_text("service Parent {}\n")
    source = tmp_path / "client.thrift"
    source.write_text(
        'include "base.thrift"\nservice Local {}\n'
        "service Child extends /* local base */ Local {}\n"
        "service Imported extends /* alias */ base /* dot */ . /* name */ Parent {}\n"
    )

    graph = parse_directory(str(tmp_path), language="thrift")

    assert not graph.parse_diagnostics
    assert {
        (edge.source_id, edge.target_id) for edge in graph.edges if edge.kind == EdgeKind.INHERITS
    } == {("client:Child", "client:Local"), ("client:Imported", "base:Parent")}


def test_thrift_missing_included_base_does_not_bind_unrelated_service(tmp_path: Path) -> None:
    (tmp_path / "unrelated.thrift").write_text("service Parent {}\n")
    (tmp_path / "client.thrift").write_text(
        'include "missing.thrift"\nservice Child extends missing.Parent {}\n'
    )

    graph = parse_directory(str(tmp_path), language="thrift")
    edge = next(edge for edge in graph.edges if edge.kind == EdgeKind.INHERITS)

    assert edge.target_id != "unrelated:Parent"
    assert edge.target_id == "missing:Parent"
    assert edge.target_id not in graph.nodes


def test_thrift_unqualified_base_does_not_bind_another_file(tmp_path: Path) -> None:
    (tmp_path / "unrelated.thrift").write_text("service Parent {}\n")
    (tmp_path / "client.thrift").write_text("service Child extends Parent {}\n")

    graph = parse_directory(str(tmp_path), language="thrift")
    edge = next(edge for edge in graph.edges if edge.kind == EdgeKind.INHERITS)

    assert edge.target_id == "client:Parent"
    assert edge.target_id not in graph.nodes


def test_thrift_ambiguous_include_alias_does_not_pick_first_service(tmp_path: Path) -> None:
    for directory in ("one", "two"):
        (tmp_path / directory).mkdir()
        (tmp_path / directory / "base.thrift").write_text("service Parent {}\n")
    (tmp_path / "client.thrift").write_text(
        'include "one/base.thrift"\ninclude "two/base.thrift"\n'
        "service Child extends base.Parent {}\n"
    )

    graph = parse_directory(str(tmp_path), language="thrift")
    edge = next(edge for edge in graph.edges if edge.kind == EdgeKind.INHERITS)

    assert edge.target_id == "client:base.Parent"
    assert edge.target_id not in graph.nodes
