"""TLA+ proof source navigation does not assert proof validity or execution."""

from pathlib import Path

import pytest

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.nodes import NodeKind, NodeOrigin
from trailmark.parse import parse_file
from trailmark.query.api import QueryEngine


def _query(tmp_path: Path, body: str) -> QueryEngine:
    path = tmp_path / "Proof.tla"
    path.write_text("---- MODULE Proof ----\nP == TRUE\n" + body + "\n====\n")
    return QueryEngine.from_graph(parse_file(str(path), "tlaplus"))


def _sites(query: QueryEngine, spelling: str) -> list[CodeEdge]:
    return [
        e
        for e in query._store._graph.edges
        if e.kind == EdgeKind.REFERENCES and dict(e.attributes).get("spelling") == spelling
    ]


@pytest.mark.mutation_fast
def test_nested_steps_and_citations_keep_owners_and_public_paths(tmp_path: Path) -> None:
    query = _query(
        tmp_path,
        "THEOREM T == P\n"
        "<1>1. P BY DEF P\n"
        "<1>2. P\n"
        "  <2>1. P BY <1>1\n"
        "  <2> QED BY <2>1\n"
        "<1> QED BY <1>1, <1>2",
    )
    graph = query._store._graph
    steps = [n for n in graph.nodes.values() if n.kind == NodeKind.TEMPLATE]
    assert len(steps) == 5
    assert all(dict(n.attributes).get("executable") is False for n in steps)
    assert all(dict(n.attributes).get("private") is True for n in steps)
    inner = "Proof:Proof.T.<1>2.<2>1"
    edge = next(e for e in _sites(query, "<1>1") if e.source_id == inner)
    assert edge.target_id == "Proof:Proof.T.<1>1"
    assert query.paths_between(inner, edge.target_id, edge_kinds=(EdgeKind.REFERENCES,))
    assert not any(e.kind == EdgeKind.CALLS for e in graph.edges)
    assert any(
        e.source_id == "Proof:Proof.T.<1>1" and e.target_id == "Proof:Proof.P"
        for e in _sites(query, "P")
    )
    assert graph.parse_diagnostics  # Source retention is not proof checking.


@pytest.mark.mutation_fast
def test_nested_step_names_do_not_escape_or_bind_siblings(tmp_path: Path) -> None:
    query = _query(
        tmp_path,
        "THEOREM T == P\n"
        "<1>1. P\n <2>1. P OBVIOUS\n <2> QED BY <2>1\n"
        "<1>2. P\n <2>1. P OBVIOUS\n <2> QED BY <2>1\n"
        "<1> QED BY <2>1, <1>1, <1>2",
    )
    graph = query._store._graph
    edges = _sites(query, "<2>1")
    assert len(edges) == 3
    assert sum(graph.nodes[e.target_id].origin == NodeOrigin.PROXY for e in edges) == 1
    for e in edges:
        if graph.nodes[e.target_id].origin == NodeOrigin.SOURCE:
            assert e.target_id.rsplit(".", 1)[0] == e.source_id.rsplit(".", 1)[0]


@pytest.mark.mutation_fast
@pytest.mark.parametrize("duplicate", [False, True])
def test_forward_duplicate_and_other_theorem_labels_stay_uncertain(
    tmp_path: Path, duplicate: bool
) -> None:
    body = (
        "THEOREM First == P\n<1>1. P BY <1>2\n"
        + ("<1>1. P OBVIOUS\n" if duplicate else "")
        + "<1>2. P BY <1>1\n<1> QED BY <1>2\n"
        "THEOREM Second == P\n<1> QED BY <1>1"
    )
    query = _query(tmp_path, body)
    graph = query._store._graph
    forward = next(e for e in _sites(query, "<1>2") if e.source_id.endswith("First.<1>1"))
    assert graph.nodes[forward.target_id].origin == NodeOrigin.PROXY
    second = next(e for e in _sites(query, "<1>1") if ".Second." in e.source_id)
    assert graph.nodes[second.target_id].origin == NodeOrigin.PROXY
    if duplicate:
        backward = next(e for e in _sites(query, "<1>1") if e.source_id.endswith("First.<1>2"))
        assert graph.nodes[backward.target_id].origin == NodeOrigin.PROXY


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    "step",
    [
        "<1>1. TAKE P",
        "<1>1. PICK P \\in {TRUE}: P OBVIOUS",
        "<1>1. DEFINE P == TRUE",
        "<1>1. SUFFICES ASSUME NEW P PROVE P OBVIOUS",
        "<1>1. \\A P \\in {TRUE}: P OBVIOUS",
    ],
)
def test_proof_binders_block_namesake_global_binding(tmp_path: Path, step: str) -> None:
    query = _query(tmp_path, "THEOREM T == TRUE\n" + step + "\n<1> QED BY DEF P")
    graph = query._store._graph
    edges = [e for e in _sites(query, "P") if "proof@" in e.source_id]
    assert edges
    assert all(graph.nodes[e.target_id].origin == NodeOrigin.PROXY for e in edges)
    assert all(e.confidence == EdgeConfidence.UNCERTAIN for e in edges)


def test_comment_string_decoys_and_repeated_citation_positions(tmp_path: Path) -> None:
    path = tmp_path / "Proof.tla"
    path.write_bytes(
        (
            '---- MODULE Proof ----\r\n(* λ <9>fake. *)\r\nP == "<9>fake"\r\n'
            "THEOREM T == TRUE\r\n<1>1. TRUE OBVIOUS\r\n"
            "<1> QED BY <1>1, <1>1\r\n====\r\n"
        ).encode()
    )
    query = QueryEngine.from_graph(parse_file(str(path), "tlaplus"))
    graph = query._store._graph
    assert len([n for n in graph.nodes.values() if n.kind == NodeKind.TEMPLATE]) == 2
    edges = _sites(query, "<1>1")
    assert len(edges) == 2
    assert {(e.location.start_line, e.location.start_col) for e in edges if e.location} == {
        (6, 11),
        (6, 17),
    }
    assert all(e.target_id == "Proof:Proof.T.<1>1" for e in edges)


@pytest.mark.mutation_fast
def test_unavailable_proof_citations_remain_public_uncertain_references(tmp_path: Path) -> None:
    query = _query(tmp_path, "THEOREM T == P\nBY Missing DEF Absent")
    graph = query._store._graph
    for spelling in ("Missing", "Absent"):
        (edge,) = _sites(query, spelling)
        assert graph.nodes[edge.target_id].origin == NodeOrigin.PROXY
        assert edge.confidence == EdgeConfidence.UNCERTAIN
        assert query.paths_between(
            edge.source_id, edge.target_id, edge_kinds=(EdgeKind.REFERENCES,)
        )
