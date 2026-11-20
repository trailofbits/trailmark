"""Only literal application heads survive removal of grouping parentheses."""

from pathlib import Path

import pytest

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.nodes import NodeOrigin
from trailmark.parse import parse_directory, parse_file


@pytest.mark.parametrize("depth", [1, 3, 200])
def test_parenthesized_known_head_and_nested_argument_have_calls(
    tmp_path: Path, depth: int
) -> None:
    path = tmp_path / "Grouped.fst"
    wrap = lambda s: "(" * depth + s + ")" * depth  # noqa: E731
    path.write_text(
        "module Grouped\nlet first (x:int) : Tot int = x\n"
        "let second (x:int) : Tot int = x\n"
        f"let run (x:int) : Tot int = {wrap('first')} ({wrap('second')} x)\n"
    )
    graph = parse_file(str(path), strict=True)
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert {graph.nodes[e.target_id].name for e in calls} == {"first", "second"}
    assert len(calls) == 2
    for edge in calls:
        assert edge.confidence == EdgeConfidence.INFERRED
        assert edge.location is not None and edge.location.start_line == 4
        assert edge.location.end_col is not None and edge.location.start_col is not None
        line = path.read_text().splitlines()[3]
        assert (
            line[edge.location.start_col : edge.location.end_col]
            == graph.nodes[edge.target_id].name
        )


@pytest.mark.parametrize("head", ["(f)", "(((f)))"])
def test_parenthesized_callback_cannot_bind_namesake(tmp_path: Path, head: str) -> None:
    path = tmp_path / "Callback.fst"
    path.write_text(
        "module Callback\nlet f (x:int) : Tot int = x\n"
        f"let run (f:int -> Tot int) (x:int) : Tot int = {head} x\n"
    )
    graph = parse_file(str(path), strict=True)
    refs = [e for e in graph.edges if e.kind == EdgeKind.REFERENCES]
    assert len(refs) == 1
    edge = refs[0]
    assert dict(edge.attributes)["binding_blocked"] is True
    assert graph.nodes[edge.target_id].origin == NodeOrigin.PROXY
    assert edge.confidence == EdgeConfidence.UNCERTAIN
    assert not any(e.kind == EdgeKind.CALLS for e in graph.edges)


def test_parenthesized_qualified_head_uses_visible_module(tmp_path: Path) -> None:
    (tmp_path / "Base.fst").write_text("module Base\nlet f (x:int) : Tot int = x\n")
    (tmp_path / "Use.fst").write_text("module Use\nlet run (x:int) : Tot int = (((Base.f))) x\n")
    graph = parse_directory(str(tmp_path), language="fstar", strict=True)
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 1
    assert dict(graph.nodes[calls[0].target_id].attributes)["qualified_name"] == "Base.f"


def test_unknown_parenthesized_head_never_establishes_argument_execution(tmp_path: Path) -> None:
    path = tmp_path / "Unknown.fst"
    path.write_text(
        "module Unknown\nlet known (x:int) : Tot int = x\n"
        "let run (x:int) : Tot int = (((missing))) (((known)) x)\n"
    )
    graph = parse_file(str(path), strict=True)
    refs = [e for e in graph.edges if e.kind == EdgeKind.REFERENCES]
    assert {graph.nodes[e.target_id].name for e in refs} == {"missing", "known"}
    unknown = next(e for e in refs if graph.nodes[e.target_id].name == "missing")
    assert unknown.confidence == EdgeConfidence.UNCERTAIN
    assert not any(e.kind == EdgeKind.CALLS for e in graph.edges)


@pytest.mark.parametrize(
    "body",
    [
        "let run (x:int) : Lemma True = (((proof))) (((known)) x)",
        "let run (x:int) : Tot int = assert ((((known))) x == x); x",
        "let run (x:int) : Tot int = (((if true then known else known))) x",
        "let run (r:record) (x:int) : Tot int = (((r.field))) x",
    ],
)
def test_grouping_preserves_proof_and_computed_head_boundaries(tmp_path: Path, body: str) -> None:
    path = tmp_path / "Contexts.fst"
    path.write_text(
        "module Contexts\nlet known (x:int) : Tot int = x\n"
        "let proof (x:int) : Lemma True = ()\ntype record = { field: int -> Tot int }\n"
        + body
        + "\n"
    )
    graph = parse_file(str(path), strict=True)
    assert not any(e.kind == EdgeKind.CALLS for e in graph.edges)
    assert {n.name for n in graph.nodes.values() if n.origin == NodeOrigin.PROXY} <= {
        "op_Equals_Equals"
    }


def test_grouped_operator_and_implicit_tot_heads_keep_calls(tmp_path: Path) -> None:
    path = tmp_path / "Operators.fst"
    path.write_text(
        "module Operators\nlet ( +^ ) x y = x + y\nlet identity x = x\n"
        "let run x = (( +^ )) (((identity)) x) x\n"
    )
    graph = parse_file(str(path), strict=True)
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert {(graph.nodes[e.source_id].name, graph.nodes[e.target_id].name) for e in calls} == {
        ("run", "op_Plus_Hat"),
        ("run", "identity"),
    }


@pytest.mark.parametrize(
    "head", ["(factory x)", "(((factory x)))", "(factory x <: int -> Lemma True)"]
)
def test_returned_callee_cannot_borrow_factory_execution_metadata(
    tmp_path: Path, head: str
) -> None:
    path = tmp_path / "Returned.fst"
    path.write_text(
        "module Returned\nlet proof (x:int) : Lemma True = ()\n"
        "let factory (x:int) : Tot (int -> Lemma True) = proof\n"
        "let known (x:int) : Tot int = x\n"
        f"let run (x:int) = {head} (known x)\n"
    )
    graph = parse_file(str(path), strict=True)
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert [(graph.nodes[e.source_id].name, graph.nodes[e.target_id].name) for e in calls] == [
        ("run", "factory")
    ]
    refs = [
        e
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES
        and graph.nodes[e.source_id].name == "run"
        and graph.nodes[e.target_id].name == "known"
    ]
    assert len(refs) == 1
    assert dict(refs[0].attributes)["reference_context"] == "argument"
