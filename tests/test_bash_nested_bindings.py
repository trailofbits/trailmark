"""Nested shell helpers bind only after an unconditional local definition."""

from pathlib import Path

import pytest

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeOrigin
from trailmark.parse import parse_file


def _helpers(tmp_path: Path, source: str) -> tuple[CodeGraph, list[CodeEdge]]:
    path = tmp_path / "nested.sh"
    path.write_text(source)
    graph = parse_file(str(path), "bash")
    calls = [
        edge
        for edge in graph.edges
        if edge.kind == EdgeKind.CALLS
        and edge.location
        and "helper" in path.read_text().splitlines()[edge.location.start_line - 1]
    ]
    return graph, calls


@pytest.mark.mutation_fast
def test_nested_helper_definition_dominates_its_call(tmp_path: Path) -> None:
    graph, calls = _helpers(tmp_path, "outer() {\n helper() { :; }\n helper\n}\n")
    call = next(edge for edge in calls if edge.source_id == "nested:outer")
    assert call.target_id == "nested:outer.helper"
    assert call.location is not None and call.location.start_line == 3
    assert call.confidence == EdgeConfidence.INFERRED


@pytest.mark.mutation_fast
@pytest.mark.parametrize("alias", ["alias helper=other", "alias ${CMD:-x}=other"])
def test_later_alias_cannot_rewrite_an_already_parsed_function_body(
    tmp_path: Path, alias: str
) -> None:
    graph, calls = _helpers(tmp_path, "outer() {\n helper() { :; }\n helper\n}\n" + alias + "\n")
    call = next(edge for edge in calls if edge.source_id == "nested:outer")
    assert call.target_id == "nested:outer.helper"


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    "body",
    [
        "helper; helper() { :; }",
        "if true; then helper() { :; }; fi; helper",
        "helper() { :; }; helper() { :; }; helper",
        "helper() { :; }; unset -f helper; helper",
        'helper() { :; }; eval "$CODE"; helper',
        "helper() { :; }; source ./other.sh; helper",
    ],
)
def test_conditional_forward_duplicate_or_mutated_nested_helpers_stay_proxies(
    tmp_path: Path, body: str
) -> None:
    graph, calls = _helpers(tmp_path, "outer() { " + body + "; }\n")
    calls = [
        edge
        for edge in calls
        if edge.source_id == "nested:outer" and not dict(edge.attributes).get("deferred")
    ]
    assert calls
    assert all(graph.nodes[edge.target_id].origin == NodeOrigin.PROXY for edge in calls)


def test_earlier_unknown_alias_keeps_function_body_calls_uncertain(tmp_path: Path) -> None:
    graph, calls = _helpers(
        tmp_path, "alias ${CMD:-x}=other\nouter() {\n helper() { :; }\n helper\n}\n"
    )
    call = next(edge for edge in calls if edge.source_id == "nested:outer")
    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY


def test_nested_helper_does_not_become_an_unconditional_script_binding(tmp_path: Path) -> None:
    graph, calls = _helpers(tmp_path, "outer() { helper() { :; }; }\nhelper\n")
    call = next(edge for edge in calls if edge.source_id == "nested:<script>")
    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY
