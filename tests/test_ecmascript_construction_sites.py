"""Construction is an execution site with its own owner and complete source span."""

from pathlib import Path

import pytest

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.nodes import NodeOrigin
from trailmark.parse import parse_directory, parse_file


@pytest.mark.parametrize("language,extension", [("javascript", ".js"), ("typescript", ".ts")])
def test_construction_calls_a_local_constructor_and_keeps_argument_sites(
    tmp_path: Path, language: str, extension: str
) -> None:
    source = tmp_path / ("build" + extension)
    source.write_text(
        "class Widget { constructor(value) { sink(value); } }\n"
        "function make() { return new Widget(input()); }\n"
    )
    graph = parse_file(str(source), language)
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    construction = next(edge for edge in calls if dict(edge.attributes).get("constructor_call"))
    assert construction.source_id == "build:make"
    assert construction.target_id == "build:Widget.constructor"
    assert construction.location is not None
    text = source.read_text().splitlines()[1]
    assert (
        text[construction.location.start_col : construction.location.end_col]
        == "new Widget(input())"
    )
    assert any(
        edge.source_id == "build:make" and graph.nodes[edge.target_id].name.endswith(":input")
        for edge in calls
    )
    assert any(
        edge.source_id == "build:Widget.constructor"
        and graph.nodes[edge.target_id].name.endswith(":sink")
        for edge in calls
    )


@pytest.mark.parametrize("language,extension", [("javascript", ".js"), ("typescript", ".ts")])
@pytest.mark.parametrize("expression", ["Widget", "(Widget)"])
def test_constructor_parameter_shadows_a_same_name_class(
    tmp_path: Path, language: str, extension: str, expression: str
) -> None:
    source = tmp_path / ("shadow" + extension)
    source.write_text(
        "class Widget { constructor() {} }\n"
        f"function make(Widget) {{ return new {expression}(); }}\n"
    )
    graph = parse_file(str(source), language)
    construction = next(edge for edge in graph.edges if edge.kind == EdgeKind.CALLS)
    assert graph.nodes[construction.target_id].origin == NodeOrigin.PROXY
    assert construction.confidence == EdgeConfidence.UNCERTAIN
    assert dict(construction.attributes)["constructor_call"]
    assert dict(construction.attributes)["binding_blocked"]


@pytest.mark.parametrize("language,extension", [("javascript", ".js"), ("typescript", ".ts")])
def test_dynamic_constructor_keeps_inner_calls_without_guessing_the_target(
    tmp_path: Path, language: str, extension: str
) -> None:
    source = tmp_path / ("dynamic" + extension)
    source.write_text("function make() { return new (choose())(input()); }\n")
    graph = parse_file(str(source), language)
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    assert len(calls) == 3
    construction = next(edge for edge in calls if dict(edge.attributes).get("constructor_call"))
    assert graph.nodes[construction.target_id].origin == NodeOrigin.PROXY
    assert construction.confidence == EdgeConfidence.UNCERTAIN
    assert {
        graph.nodes[edge.target_id].name.rsplit(":", 1)[-1]
        for edge in calls
        if edge is not construction
    } == {"choose", "input"}


@pytest.mark.parametrize("language,extension", [("javascript", ".js"), ("typescript", ".ts")])
def test_default_parameter_construction_belongs_to_the_callable(
    tmp_path: Path, language: str, extension: str
) -> None:
    source = tmp_path / ("defaults" + extension)
    source.write_text("function make(value = new External()) {}\n")
    graph = parse_file(str(source), language)
    construction = next(edge for edge in graph.edges if edge.kind == EdgeKind.CALLS)
    assert construction.source_id == "defaults:make"
    assert dict(construction.attributes)["constructor_call"]


@pytest.mark.parametrize("language,extension", [("javascript", ".js"), ("typescript", ".ts")])
def test_unknown_constructor_does_not_use_an_unrelated_global_name(
    tmp_path: Path, language: str, extension: str
) -> None:
    (tmp_path / ("other" + extension)).write_text("export function External() {}\n")
    (tmp_path / ("entry" + extension)).write_text("function make() { return new External(); }\n")
    graph = parse_directory(str(tmp_path), language)
    construction = next(edge for edge in graph.edges if edge.kind == EdgeKind.CALLS)
    assert construction.target_id != "other:External"
    assert graph.nodes[construction.target_id].origin == NodeOrigin.PROXY


@pytest.mark.parametrize("language,extension", [("javascript", ".js"), ("typescript", ".ts")])
@pytest.mark.parametrize("name", ["$Widget", "Ω"])
def test_constructor_shadowing_handles_javascript_identifiers(
    tmp_path: Path, language: str, extension: str, name: str
) -> None:
    source = tmp_path / ("identifiers" + extension)
    source.write_text(
        f"class {name} {{ constructor() {{}} }}\n"
        f"function make({name}) {{ return new ({name})(); }}\n"
    )
    graph = parse_file(str(source), language)
    construction = next(edge for edge in graph.edges if edge.kind == EdgeKind.CALLS)
    assert graph.nodes[construction.target_id].origin == NodeOrigin.PROXY
    assert dict(construction.attributes)["binding_blocked"]


@pytest.mark.parametrize("language,extension", [("javascript", ".js"), ("typescript", ".ts")])
def test_nested_class_shadows_an_outer_same_name_constructor(
    tmp_path: Path, language: str, extension: str
) -> None:
    source = tmp_path / ("classes" + extension)
    source.write_text(
        "class Widget { constructor() {} }\n"
        "function make() { class Widget { constructor() {} } return new Widget(); }\n"
    )
    graph = parse_file(str(source), language)
    construction = next(edge for edge in graph.edges if edge.kind == EdgeKind.CALLS)
    assert construction.target_id == "classes:make.Widget.constructor"


@pytest.mark.parametrize("language,extension", [("javascript", ".js"), ("typescript", ".ts")])
def test_default_constructor_targets_its_known_class(
    tmp_path: Path, language: str, extension: str
) -> None:
    source = tmp_path / ("implicit" + extension)
    source.write_text("class Widget {}\nfunction make() { return new Widget(); }\n")
    graph = parse_file(str(source), language)
    construction = next(edge for edge in graph.edges if edge.kind == EdgeKind.CALLS)
    assert construction.target_id == "implicit:Widget"
    assert construction.confidence == EdgeConfidence.INFERRED


@pytest.mark.parametrize("language,extension", [("javascript", ".js"), ("typescript", ".ts")])
def test_ordinary_function_used_as_a_constructor_keeps_its_body_target(
    tmp_path: Path, language: str, extension: str
) -> None:
    source = tmp_path / ("functions" + extension)
    source.write_text("function Widget() {}\nfunction make() { return new Widget(); }\n")
    graph = parse_file(str(source), language)
    construction = next(edge for edge in graph.edges if edge.kind == EdgeKind.CALLS)
    assert construction.target_id == "functions:Widget"
    assert construction.confidence == EdgeConfidence.CERTAIN
    assert dict(construction.attributes)["constructor_call"]
