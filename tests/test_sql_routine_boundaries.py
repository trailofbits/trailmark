"""SQL routine recovery respects top-level statement and body boundaries."""

from pathlib import Path

import pytest

from trailmark.models.edges import EdgeKind
from trailmark.models.nodes import NodeKind
from trailmark.parse import parse_file


@pytest.mark.mutation_fast
def test_adjacent_functions_keep_separate_spans_and_parameters(tmp_path: Path) -> None:
    source = tmp_path / "routines.sql"
    source.write_text(
        "CREATE FUNCTION app.first(x INT) RETURNS SETOF app.row AS $$\n"
        "BEGIN EXECUTE $QUERY$ SELECT * FROM app.items $QUERY$; END;\n"
        "$$ LANGUAGE plpgsql;\n"
        "CREATE FUNCTION app.second(y INT, z INT) RETURNS SETOF app.row AS $$\n"
        "BEGIN NULL; END;\n"
        "$$ LANGUAGE plpgsql;\n"
        "CREATE FUNCTION app.last(q TEXT) RETURNS TEXT AS $$\n"
        "BEGIN IF q ~ '\\$|;|--|''' THEN RAISE EXCEPTION 'bad'; END IF; RETURN q; END;\n"
        "$$ LANGUAGE plpgsql;\n",
    )
    graph = parse_file(str(source), "sql")

    routines = [graph.nodes[f"routines:app.{name}"] for name in ("first", "second", "last")]
    assert [(unit.location.start_line, unit.location.end_line) for unit in routines] == [
        (1, 3),
        (4, 6),
        (7, 9),
    ]
    assert [[parameter.name for parameter in unit.parameters] for unit in routines] == [
        ["x"],
        ["y", "z"],
        ["q"],
    ]
    assert all(unit.kind == NodeKind.FUNCTION for unit in routines)


@pytest.mark.mutation_fast
@pytest.mark.parametrize("returns", [("integer", "void"), ("void", "integer")])
def test_recovered_and_native_replacements_follow_source_order(
    tmp_path: Path, returns: tuple[str, str]
) -> None:
    source = tmp_path / "replace.sql"
    source.write_text(
        f"CREATE FUNCTION app.refresh(x INT) RETURNS {returns[0]} "  # noqa: S608 -- test source
        "LANGUAGE SQL AS $$ SELECT id FROM app.old_items $$;\n"
        f"CREATE OR REPLACE FUNCTION app.refresh(x INT) RETURNS {returns[1]} "
        "LANGUAGE SQL AS $$ SELECT id FROM app.new_items $$;\n",
    )
    graph = parse_file(str(source), "sql")

    functions = [unit for unit in graph.nodes.values() if unit.kind == NodeKind.FUNCTION]
    assert len(functions) == 1
    assert functions[0].location.start_line == 2
    assert {
        edge.target_id
        for edge in graph.edges
        if edge.source_id == functions[0].id and edge.kind == EdgeKind.CORRESPONDS_TO
    } == {"replace:app.new_items"}


def test_recovered_function_body_does_not_create_quoted_declarations_or_call_relations(
    tmp_path: Path,
) -> None:
    source = tmp_path / "body.sql"
    source.write_text(
        "-- CREATE FUNCTION app.disabled() RETURNS void AS 'SELECT 1' LANGUAGE SQL;\n"
        "CREATE FUNCTION app.refresh(x INT) RETURNS void LANGUAGE SQL AS $body$\n"
        "SELECT id FROM app.items JOIN app.lookup(x) AS item ON true;\n"
        "SELECT 'CREATE FUNCTION app.quoted() RETURNS void AS ''SELECT 1'' LANGUAGE SQL;';\n"
        "$body$;\n",
    )
    graph = parse_file(str(source), "sql")

    assert {unit.name for unit in graph.nodes.values() if unit.kind == NodeKind.FUNCTION} == {
        "refresh"
    }
    assert {edge.target_id for edge in graph.edges if edge.kind == EdgeKind.CORRESPONDS_TO} == {
        "body:app.items"
    }


@pytest.mark.mutation_fast
def test_recovered_function_columns_are_utf8_bytes(tmp_path: Path) -> None:
    source = tmp_path / "unicode.sql"
    prefix = "/* é */ "
    text = prefix + (
        "CREATE FUNCTION app.refresh() RETURNS void LANGUAGE plpgsql AS $$ BEGIN NULL; END; $$;"
    )
    source.write_text(text)
    graph = parse_file(str(source), "sql")

    location = graph.nodes["unicode:app.refresh"].location
    assert (location.start_line, location.end_line) == (1, 1)
    assert (location.start_col, location.end_col) == (len(prefix.encode()), len(text.encode()))


@pytest.mark.mutation_fast
def test_atomic_body_case_does_not_end_routine_before_later_statements(tmp_path: Path) -> None:
    source = tmp_path / "atomic.sql"
    source.write_text(
        "CREATE FUNCTION app.first(x INT) RETURNS INT LANGUAGE SQL\n"
        "BEGIN ATOMIC\n"
        "  SELECT CASE WHEN x = 0 THEN 1 ELSE x END;\n"
        "  SELECT id FROM app.items;\n"
        "END;\n"
        "CREATE FUNCTION app.second() RETURNS INT LANGUAGE SQL AS $$ SELECT 2 $$;\n",
    )
    graph = parse_file(str(source), "sql")

    first = graph.nodes["atomic:app.first"]
    assert (first.location.start_line, first.location.end_line) == (1, 5)
    assert graph.nodes["atomic:app.second"].location.start_line == 6
    assert any(
        edge.source_id == first.id and edge.target_id == "atomic:app.items" for edge in graph.edges
    )
