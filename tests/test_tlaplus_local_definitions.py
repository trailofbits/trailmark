"""Local TLA+ definitions retain their own specification graph identities."""

from pathlib import Path

import pytest

from trailmark.models.edges import EdgeKind
from trailmark.parse import parse_directory, parse_file
from trailmark.query.api import QueryEngine


@pytest.mark.mutation_fast
def test_recursive_let_function_has_own_body_and_navigation(tmp_path: Path) -> None:
    source = tmp_path / "Fold.tla"
    source.write_text(
        "---- MODULE Fold ----\nCONSTANT S\n"
        "Outer == LET iter[s \\in SUBSET S] ==\n"
        "               IF s = {} THEN S ELSE iter[s \\ {CHOOSE x \\in s: TRUE}]\n"
        "         IN iter[S]\n====\n",
    )
    graph = parse_file(str(source), "tlaplus", strict=True)
    outer = next(unit for unit in graph.nodes.values() if unit.name == "Outer")
    local = next(unit for unit in graph.nodes.values() if unit.name == "iter")
    assert [parameter.name for parameter in local.parameters] == ["s"]
    assert (local.location.start_line, local.location.end_line) == (3, 4)
    assert any(
        edge.kind == EdgeKind.CONTAINS and edge.source_id == outer.id and edge.target_id == local.id
        for edge in graph.edges
    )
    refs = [edge for edge in graph.edges if edge.kind == EdgeKind.REFERENCES]
    assert any(edge.source_id == outer.id and edge.target_id == local.id for edge in refs)
    assert any(edge.source_id == local.id and edge.target_id == local.id for edge in refs)
    assert any(
        edge.source_id == local.id and graph.nodes[edge.target_id].name == "S" for edge in refs
    )
    engine = QueryEngine.from_graph(graph)
    assert engine.paths_between(outer.id, local.id, edge_kinds=(EdgeKind.REFERENCES,))
    assert not any(edge.kind == EdgeKind.CALLS for edge in graph.edges)


def test_sibling_let_definitions_keep_distinct_targets_and_do_not_leak(tmp_path: Path) -> None:
    source = tmp_path / "Scopes.tla"
    source.write_text(
        "---- MODULE Scopes ----\n"
        "Outer == (LET Helper == 1 IN Helper) + (LET Helper == 2 IN Helper)\n"
        "Outside == Helper\n====\n",
    )
    graph = parse_file(str(source), "tlaplus", strict=True)
    locals_ = [unit for unit in graph.nodes.values() if unit.name == "Helper"]
    assert len(locals_) == 2
    assert len({unit.id for unit in locals_}) == 2
    refs = [edge for edge in graph.edges if edge.kind == EdgeKind.REFERENCES]
    assert {edge.target_id for edge in refs} == {unit.id for unit in locals_}
    assert all(graph.nodes[edge.source_id].name == "Outer" for edge in refs)
    assert len(refs) == 2


@pytest.mark.mutation_fast
def test_nested_let_ownership_and_parameter_shadowing(tmp_path: Path) -> None:
    source = tmp_path / "Nested.tla"
    source.write_text(
        "---- MODULE Nested ----\nCONSTANT Global\n"
        "Outer == LET Value == Global\n"
        "             Apply(Value) == LET Inner == Value IN Inner\n"
        "         IN Apply(Value)\n====\n",
    )
    graph = parse_file(str(source), "tlaplus", strict=True)
    units = {unit.name: unit for unit in graph.nodes.values()}
    assert [parameter.name for parameter in units["Apply"].parameters] == ["Value"]
    pairs = {
        (graph.nodes[edge.source_id].name, graph.nodes[edge.target_id].name)
        for edge in graph.edges
        if edge.kind == EdgeKind.REFERENCES
    }
    assert pairs == {
        ("Value", "Global"),
        ("Apply", "Inner"),
        ("Outer", "Apply"),
        ("Outer", "Value"),
    }
    assert any(
        edge.kind == EdgeKind.CONTAINS
        and edge.source_id == units["Apply"].id
        and edge.target_id == units["Inner"].id
        for edge in graph.edges
    )


def test_quantifiers_shadow_let_names_and_locals_are_not_exported(tmp_path: Path) -> None:
    (tmp_path / "Base.tla").write_text(
        "---- MODULE Base ----\nCONSTANT Global\n"
        "Outer == LET Helper == Global IN \\A Helper \\in {1}: Helper=Helper\n====\n",
    )
    (tmp_path / "Use.tla").write_text(
        "---- MODULE Use ----\nEXTENDS Base\nOther == Helper\n====\n",
    )
    graph = parse_directory(str(tmp_path), "tlaplus", strict=True)
    assert [unit.name for unit in graph.nodes.values()].count("Helper") == 1
    assert {
        (graph.nodes[edge.source_id].name, graph.nodes[edge.target_id].name)
        for edge in graph.edges
        if edge.kind == EdgeKind.REFERENCES
    } == {("Helper", "Global")}


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    "expression, count",
    [("M!op(Local)", 1), ("M(Local)!op", 1), ("M(Local)!N(Local)!op(Local)", 3)],
)
def test_prefixed_operator_arguments_keep_local_references(
    tmp_path: Path, expression: str, count: int
) -> None:
    source = tmp_path / "Prefixed.tla"
    source.write_text(
        f"---- MODULE Prefixed ----\nOuter == LET Local == 1 IN {expression}\n====\n",
    )
    graph = parse_file(str(source), "tlaplus", strict=True)
    refs = [edge for edge in graph.edges if edge.kind == EdgeKind.REFERENCES]
    assert [
        (graph.nodes[edge.source_id].name, graph.nodes[edge.target_id].name) for edge in refs
    ] == [("Outer", "Local")] * count


@pytest.mark.mutation_fast
def test_local_recursive_forward_declarations_link_mutual_operators(tmp_path: Path) -> None:
    source = tmp_path / "Recursive.tla"
    source.write_text(
        "---- MODULE Recursive ----\n"
        "Outer == LET RECURSIVE F(_), G(_)\n"
        "             F(x) == IF x=0 THEN 0 ELSE G(x-1)\n"
        "             G(x) == F(x)\n"
        "         IN F(2)\n====\n",
    )
    graph = parse_file(str(source), "tlaplus", strict=True)
    refs = [edge for edge in graph.edges if edge.kind == EdgeKind.REFERENCES]
    assert {
        (graph.nodes[edge.source_id].name, graph.nodes[edge.target_id].name) for edge in refs
    } == {("Outer", "F"), ("F", "G"), ("G", "F")}
    assert all(not dict(graph.nodes[edge.target_id].attributes).get("signature") for edge in refs)
    assert len([edge for edge in graph.edges if edge.kind == EdgeKind.CORRESPONDS_TO]) == 2


@pytest.mark.mutation_fast
def test_later_let_binding_does_not_capture_its_initializer(tmp_path: Path) -> None:
    source = tmp_path / "Sequential.tla"
    source.write_text(
        "---- MODULE Sequential ----\n"
        "Outer == LET Value == 1\n"
        "         IN LET Value == Value + 1 IN Value\n====\n",
    )
    graph = parse_file(str(source), "tlaplus", strict=True)
    values = sorted(
        [unit for unit in graph.nodes.values() if unit.name == "Value"],
        key=lambda unit: unit.location.start_line,
    )
    refs = [edge for edge in graph.edges if edge.kind == EdgeKind.REFERENCES]
    assert {(edge.source_id, edge.target_id) for edge in refs} == {
        (values[1].id, values[0].id),
        ("Sequential:Sequential.Outer", values[1].id),
    }
