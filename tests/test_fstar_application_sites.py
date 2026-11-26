"""Retain source application witnesses without inventing elaborated callees."""

from pathlib import Path

import pytest

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.nodes import NodeOrigin
from trailmark.parse import parse_directory, parse_file
from trailmark.query.api import QueryEngine


@pytest.mark.mutation_fast
def test_bound_callback_never_links_namesake_and_keeps_each_site(tmp_path: Path) -> None:
    path = tmp_path / "Callbacks.fst"
    path.write_text(
        "module Callbacks\nlet callback (x:int) : Tot int = x\n"
        "let run (callback:int -> Tot int) (x:int) : Tot int =\n"
        "  let y = callback x in callback y\n"
    )
    graph = parse_file(str(path), strict=True)
    owner = next(n for n in graph.nodes.values() if n.name == "run")
    refs = [e for e in graph.edges if e.source_id == owner.id and e.kind == EdgeKind.REFERENCES]
    assert len(refs) == 2
    assert len({e.location for e in refs}) == 2
    for edge in refs:
        assert edge.confidence == EdgeConfidence.UNCERTAIN
        assert dict(edge.attributes)["binding_blocked"] is True
        target = graph.nodes[edge.target_id]
        assert target.name == "callback"
        assert target.origin == NodeOrigin.PROXY
        assert dict(target.attributes)["executable"] is False
    assert not any(e.kind == EdgeKind.CALLS for e in graph.edges)
    engine = QueryEngine.from_graph(graph)
    assert engine.callees_of(owner.id) == []
    assert len(engine.reachable_from(owner.id, edge_kinds=("references",))) == 1


@pytest.mark.mutation_fast
def test_unavailable_nested_application_stays_uncertain_reference(tmp_path: Path) -> None:
    path = tmp_path / "Unavailable.fst"
    path.write_text(
        "module Unavailable\nlet known (x:int) : Tot int = x\n"
        "let run (x:int) : Tot int = Missing.apply (known x)\n"
    )
    graph = parse_file(str(path), strict=True)
    refs = [e for e in graph.edges if e.kind == EdgeKind.REFERENCES]
    assert {graph.nodes[e.target_id].name for e in refs} == {"Missing.apply", "known"}
    unknown = next(e for e in refs if graph.nodes[e.target_id].origin == NodeOrigin.PROXY)
    assert unknown.confidence == EdgeConfidence.UNCERTAIN
    assert dict(unknown.attributes)["reference_context"] == "body"
    assert unknown.location is not None and unknown.location.start_line == 3
    # An unavailable callee supplies no evidence that its arguments execute.
    assert not any(e.kind == EdgeKind.CALLS for e in graph.edges)


@pytest.mark.parametrize(
    ("body", "expected_context"),
    [
        ("let run (f:int -> Tot int) (x:int) : Tot int = f x", "body"),
        ("let run (f:int -> Tot int) (x:int) : Lemma True = assert (f x == x)", "proof"),
        ("let run (x:int) : Tot int = let f = (fun y -> y) in f x", "body"),
        ("let run (x:int) : Tot int = (fun f -> f x) (fun y -> y)", "body"),
        ("let run (x:int) : Tot int = match Some x with | Some f -> f x | None -> x", "body"),
    ],
)
def test_lexical_forms_preserve_bound_application_only(
    tmp_path: Path, body: str, expected_context: str
) -> None:
    path = tmp_path / "Lexical.fst"
    path.write_text("module Lexical\nlet f (x:int) : Tot int = x\n" + body + "\n")
    graph = parse_file(str(path), strict=True)
    refs = [
        e
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES and dict(e.attributes).get("spelling") == "f"
    ]
    assert len(refs) == 1
    edge = refs[0]
    assert dict(edge.attributes)["reference_context"] == expected_context
    assert dict(edge.attributes)["binding_blocked"] is True
    assert edge.confidence == EdgeConfidence.UNCERTAIN
    assert graph.nodes[edge.target_id].origin == NodeOrigin.PROXY
    assert not any(e.kind == EdgeKind.CALLS for e in graph.edges)


def test_directory_resolution_keeps_available_target_and_unavailable_decoy_separate(
    tmp_path: Path,
) -> None:
    (tmp_path / "Base.fst").write_text("module Base\nlet helper (x:int) : Tot int = x\n")
    (tmp_path / "Decoy.fst").write_text("module Decoy\nlet absent (x:int) : Tot int = x\n")
    (tmp_path / "Use.fst").write_text(
        "module Use\nopen Base\nlet run (x:int) : Tot int = helper x\n"
        "let other (x:int) : Tot int = absent x\n"
    )
    graph = parse_directory(str(tmp_path), language="fstar", strict=True)
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert [(graph.nodes[e.source_id].name, graph.nodes[e.target_id].name) for e in calls] == [
        ("run", "helper")
    ]
    unknown = next(
        e
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES and graph.nodes[e.source_id].name == "other"
    )
    assert unknown.confidence == EdgeConfidence.UNCERTAIN
    assert graph.nodes[unknown.target_id].origin == NodeOrigin.PROXY


def test_bound_value_and_type_names_are_not_unresolved_applications(tmp_path: Path) -> None:
    path = tmp_path / "Values.fst"
    path.write_text("module Values\nlet identity (f:int -> Tot int) : (int -> Tot int) = f\n")
    graph = parse_file(str(path), strict=True)
    assert not any(e.kind in {EdgeKind.REFERENCES, EdgeKind.CALLS} for e in graph.edges)


def test_unavailable_and_bound_type_applications_stay_out_of_body_evidence(tmp_path: Path) -> None:
    path = tmp_path / "Types.fst"
    path.write_text(
        "module Types\ntype refined = x:int{unavailable x > 0}\n"
        "let identity (f:int -> Tot int) (x:int{f x > 0}) : Tot int = x\n"
    )
    graph = parse_file(str(path), strict=True)
    assert not any(e.kind in {EdgeKind.REFERENCES, EdgeKind.CALLS} for e in graph.edges)


def test_unresolved_proxy_is_scoped_to_owner(tmp_path: Path) -> None:
    path = tmp_path / "Owners.fst"
    path.write_text("module Owners\nlet first x = missing x\nlet second x = missing x\n")
    graph = parse_file(str(path), strict=True)
    refs = [e for e in graph.edges if e.kind == EdgeKind.REFERENCES]
    assert len(refs) == 2
    assert len({e.target_id for e in refs}) == 2
    assert all(graph.nodes[e.target_id].origin == NodeOrigin.PROXY for e in refs)


@pytest.mark.parametrize("receiver", ["r", "(r)"])
def test_projection_receiver_is_not_mistaken_for_application_head(
    tmp_path: Path, receiver: str
) -> None:
    path = tmp_path / "Projection.fst"
    path.write_text(
        "module Projection\ntype record = { field: int -> Tot int }\n"
        f"let run (r:record) (x:int) : Tot int = {receiver}.field x\n"
    )
    graph = parse_file(str(path), strict=True)
    assert not any(n.origin == NodeOrigin.PROXY for n in graph.nodes.values())
    assert not any(e.kind == EdgeKind.CALLS for e in graph.edges)


@pytest.mark.mutation_fast
def test_global_record_value_retains_reference_without_receiver_call(tmp_path: Path) -> None:
    path = tmp_path / "Record.fst"
    path.write_text(
        "module Record\ntype record = { field: int -> Tot int }\n"
        "let value : record = { field = (fun x -> x) }\n"
        "let known (x:int) : Tot int = x\n"
        "let run (x:int) : Tot int = value.field (known x)\n"
    )
    graph = parse_file(str(path), strict=True)
    refs = [e for e in graph.edges if e.kind == EdgeKind.REFERENCES]
    assert any(
        graph.nodes[e.source_id].name == "run" and graph.nodes[e.target_id].name == "value"
        for e in refs
    )
    assert any(
        graph.nodes[e.source_id].name == "run" and graph.nodes[e.target_id].name == "known"
        for e in refs
    )
    assert not any(e.kind == EdgeKind.CALLS for e in graph.edges)
