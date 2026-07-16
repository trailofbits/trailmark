"""Tests for PostgreSQL-oriented SQL graph extraction."""

from __future__ import annotations

from pathlib import Path

from trailmark.models.edges import EdgeKind
from trailmark.models.nodes import NodeKind
from trailmark.parse import detect_languages, parse_directory, parse_file, supported_languages


def test_sql_schema_routines_and_dependencies(tmp_path: Path) -> None:
    source = tmp_path / "schema.sql"
    source.write_text(
        "CREATE SCHEMA app;\n"
        "CREATE TABLE app.users (id bigint);\n"
        "CREATE VIEW app.active AS SELECT id FROM app.users;\n"
        "CREATE FUNCTION app.lookup(x int) RETURNS bigint LANGUAGE SQL "
        "AS $$ SELECT id FROM app.users $$;\n"
        "CREATE PROCEDURE app.refresh() LANGUAGE SQL AS $$ SELECT id FROM app.users $$;\n",
    )

    graph = parse_file(str(source), "sql")

    assert graph.nodes["schema:app"].kind == NodeKind.SCHEMA
    assert graph.nodes["schema:app.users"].kind == NodeKind.TABLE
    assert graph.nodes["schema:app.active"].kind == NodeKind.VIEW
    assert graph.nodes["schema:app.lookup"].kind == NodeKind.FUNCTION
    assert graph.nodes["schema:app.refresh"].kind == NodeKind.PROCEDURE
    assert graph.nodes["schema:app.lookup"].parameters[0].name == "x"
    dependencies = {
        (edge.source_id, edge.target_id)
        for edge in graph.edges
        if edge.kind == EdgeKind.CORRESPONDS_TO
    }
    assert ("schema:app.active", "schema:app.users") in dependencies
    assert ("schema:app.lookup", "schema:app.users") in dependencies
    assert ("schema:app.refresh", "schema:app.users") in dependencies


def test_sql_is_registered_and_auto_detected(tmp_path: Path) -> None:
    (tmp_path / "schema.sql").write_text("CREATE TABLE users (id bigint);")
    assert "sql" in supported_languages()
    assert "sql" in detect_languages(str(tmp_path))


def test_malformed_sql_returns_partial_graph(tmp_path: Path) -> None:
    source = tmp_path / "broken.sql"
    source.write_text("CREATE TABLE users (id bigint")
    graph = parse_file(str(source), "sql")
    assert graph.language == "sql"
    assert any(node.kind == NodeKind.MODULE for node in graph.nodes.values())


def test_cross_file_dependency_keeps_real_table(tmp_path: Path) -> None:
    (tmp_path / "a_table.sql").write_text("CREATE TABLE app.users (id bigint);")
    (tmp_path / "z_view.sql").write_text("CREATE VIEW app.active AS SELECT id FROM app.users;")
    graph = parse_directory(str(tmp_path), "sql")
    assert graph.nodes["a_table:app.users"].kind == NodeKind.TABLE
    assert any(
        edge.source_id == "z_view:app.active"
        and edge.target_id == "a_table:app.users"
        and edge.kind == EdgeKind.CORRESPONDS_TO
        for edge in graph.edges
    )
    assert not any(
        node.kind == NodeKind.PROXY and node.name.endswith("app.users")
        for node in graph.nodes.values()
    )


def test_single_file_unresolved_dependency_is_materialized(tmp_path: Path) -> None:
    source = tmp_path / "views.sql"
    source.write_text("CREATE VIEW app.active AS SELECT id FROM app.users;")
    graph = parse_file(str(source), "sql")

    edge = next(
        edge
        for edge in graph.edges
        if edge.source_id == "views:app.active"
        and edge.target_id == "views:app.users"
        and edge.kind == EdgeKind.CORRESPONDS_TO
    )
    assert graph.nodes[edge.target_id].kind == NodeKind.PROXY


def test_sql_function_invocation_is_not_relation_dependency(tmp_path: Path) -> None:
    source = tmp_path / "functions.sql"
    source.write_text(
        "CREATE TABLE app.events (id bigint);\n"
        "CREATE FUNCTION app.event_count() RETURNS bigint LANGUAGE SQL "
        "AS $$ SELECT count(*) FROM app.events $$;\n",
    )
    graph = parse_file(str(source), "sql")

    dependencies = {edge.target_id for edge in graph.edges if edge.kind == EdgeKind.CORRESPONDS_TO}
    assert "functions:app.events" in dependencies
    assert "functions:count" not in dependencies
    assert "functions:count" not in graph.nodes
