"""A callback application remains visible without guessing its implementation."""

from pathlib import Path

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.nodes import NodeOrigin
from trailmark.parse import parse_file
from trailmark.query.api import QueryEngine


def test_parameter_applications_keep_each_site_and_public_navigation(tmp_path: Path) -> None:
    path = tmp_path / "Callbacks.dfy"
    path.write_text(
        "function less(x: int, y: int): bool { x < y }\n"
        "method Search(less: (int, int) -> bool) returns (r: bool) {\n"
        "  if less(1, 2) { r := less(2, 3); } else { r := false; }\n"
        "}\n"
    )
    graph = parse_file(str(path), "dafny", strict=True)
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 2
    assert {e.source_id for e in calls} == {"Callbacks:Search"}
    assert len({e.location.start_col for e in calls if e.location}) == 2
    assert len({e.target_id for e in calls}) == 1
    assert all(graph.nodes[e.target_id].origin == NodeOrigin.PROXY for e in calls)
    assert all(e.confidence == EdgeConfidence.UNCERTAIN for e in calls)
    assert not any(e.target_id == "Callbacks:less" for e in calls)
    engine = QueryEngine.from_graph(graph)
    assert {n["id"] for n in engine.callers_of(calls[0].target_id)} == {"Callbacks:Search"}


def test_callback_and_argument_calls_keep_evaluation_boundaries(tmp_path: Path) -> None:
    path = tmp_path / "Evaluation.dfy"
    path.write_text(
        "function value(): int { 1 }\n"
        "function consume(ghost x: int): int { 0 }\n"
        "function Run(f: int -> int): int { f(value()) + consume(f(value())) }\n"
    )
    graph = parse_file(str(path), "dafny", strict=True)
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 3
    assert sum(e.target_id == "Evaluation:value" for e in calls) == 1
    assert sum(graph.nodes[e.target_id].origin == NodeOrigin.PROXY for e in calls) == 1
    assert sum(e.target_id == "Evaluation:consume" for e in calls) == 1


def test_contracts_and_ghost_parameters_do_not_become_runtime_callbacks(tmp_path: Path) -> None:
    path = tmp_path / "Ghost.dfy"
    path.write_text(
        "function f(x: int): bool { true }\n"
        "method Check(f: int -> bool, ghost g: int -> bool)\n"
        "  requires f(0)\n"
        "{ assert f(1); assert g(1); }\n"
        "lemma Proof(f: int -> bool) { assert f(2); }\n"
    )
    graph = parse_file(str(path), "dafny", strict=True)
    assert not any(e.kind == EdgeKind.CALLS for e in graph.edges)
    references = [e for e in graph.edges if e.kind == EdgeKind.REFERENCES]
    assert sum(graph.nodes[e.target_id].origin == NodeOrigin.PROXY for e in references) == 3
    assert not any(e.target_id == "Ghost:f" for e in references)


def test_inner_binders_do_not_capture_the_parameter_proxy(tmp_path: Path) -> None:
    path = tmp_path / "Shadow.dfy"
    path.write_text(
        "function Run(f: int -> int): int {\n  (var f := (x: int) => x; f(0)) + f(1)\n}\n"
    )
    graph = parse_file(str(path), "dafny", strict=True)
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 1
    assert calls[0].location
    assert calls[0].location.start_col == path.read_text().splitlines()[1].rindex("f(1)")
