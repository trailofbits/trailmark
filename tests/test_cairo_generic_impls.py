"""Generic impl declarations retain their names, method owners, and source sites."""

from pathlib import Path

import pytest

from trailmark.models.edges import EdgeKind
from trailmark.models.nodes import NodeKind
from trailmark.parse import parse_file
from trailmark.query.api import QueryEngine


@pytest.mark.parametrize(
    "parameters",
    ["T", "T, +Drop<T>", "T, impl TDrop: Drop<T>", "T, +core::traits::Drop<T>,"],
)
def test_generic_impl_methods_keep_concrete_owners_and_navigation(
    tmp_path: Path, parameters: str
) -> None:
    path = tmp_path / "Generic.cairo"
    path.write_text(
        "mod Component {\n"
        "    fn helper() {}\n"
        f"impl Work<{parameters}> of Operations<T> {{\n"
        "    fn run() { helper(); helper(); }\n"
        "}\n}\n"
    )
    graph = parse_file(str(path), "cairo", strict=True)
    owner, run = ("Generic:Component.Work" + suffix for suffix in ["", ".run"])
    helper = "Generic:Component.helper"

    assert graph.nodes[owner].kind == NodeKind.NAMESPACE
    assert graph.nodes[owner].name == "Work"
    assert graph.nodes[helper].location.start_line == 2
    assert graph.nodes[run].location.start_line == 4
    assert any(
        e.source_id == owner and e.target_id == run and e.kind == EdgeKind.CONTAINS
        for e in graph.edges
    )
    calls = [e for e in graph.edges if e.source_id == run and e.kind == EdgeKind.CALLS]
    assert len(calls) == 2
    assert {e.target_id for e in calls} == {helper}
    assert len({e.location.start_col for e in calls if e.location}) == 2
    assert QueryEngine.from_graph(graph).paths_between(run, helper)


def test_multiline_component_impl_generics_do_not_hide_later_sibling_declarations(
    tmp_path: Path,
) -> None:
    path = tmp_path / "Component.cairo"
    path.write_text(
        "#[starknet::component]\nmod Component {\n"
        "#[embeddable_as(FirstImpl)]\nimpl First<\n"
        " T, +HasComponent<T>, // impl constraint\n"
        "> of interface::Operations<ComponentState<T>> {\n"
        " fn transfer(ref self: ComponentState<T>) { external(); }\n}\n"
        "impl Second<T, +HasComponent<T>> of Other<ComponentState<T>> {\n"
        " fn transfer(ref self: ComponentState<T>) { other(); }\n}\n"
        "fn after() {}\n}\n"
    )
    graph = parse_file(str(path), "cairo", strict=True)

    assert "Component:Component.First.transfer" in graph.nodes
    assert "Component:Component.Second.transfer" in graph.nodes
    assert "Component:Component.after" in graph.nodes
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert {e.source_id for e in calls} == {
        "Component:Component.First.transfer",
        "Component:Component.Second.transfer",
    }
    assert {e.location.start_line for e in calls if e.location} == {7, 10}
    assert not any("HasComponent" in e.target_id for e in calls)


def test_malformed_generic_impl_constraint_still_has_parse_diagnostics(tmp_path: Path) -> None:
    path = tmp_path / "Malformed.cairo"
    path.write_text("impl Work<T, +> of Operations<T> { fn run() {} }\n")

    assert parse_file(str(path), "cairo").parse_diagnostics
    with pytest.raises(ValueError, match="Incomplete source analysis"):
        parse_file(str(path), "cairo", strict=True)
