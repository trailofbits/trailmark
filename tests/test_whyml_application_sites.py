"""Missing callee definitions do not erase explicit program applications."""

from pathlib import Path

import pytest

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.nodes import NodeOrigin
from trailmark.parse import parse_directory, parse_file
from trailmark.query.api import QueryEngine


def test_unavailable_callee_and_nested_calls_retain_uncertainty(tmp_path: Path) -> None:
    path = tmp_path / "Program.mlw"
    path.write_text(
        "module Program\nuse external.External\n"
        "let value (x:int):int = x\n"
        "let run () = external (value 1) (value 2)\nend\n"
    )
    graph = parse_file(str(path), "whyml", strict=True)
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 3
    assert all(e.source_id == "Program:Program.run" for e in calls)
    assert all(e.location and e.location.start_line == 4 for e in calls)
    assert all(e.confidence == EdgeConfidence.UNCERTAIN for e in calls)
    assert sum(graph.nodes[e.target_id].origin == NodeOrigin.PROXY for e in calls) == 1
    engine = QueryEngine.from_graph(graph)
    assert {n["id"] for n in engine.callers_of("Program:Program.value")} == {"Program:Program.run"}


def test_known_runtime_arguments_keep_inferred_confidence(tmp_path: Path) -> None:
    path = tmp_path / "Known.mlw"
    path.write_text("module Known\nlet value (x:int):int = x\nlet run () = value (value 1)\nend\n")
    graph = parse_file(str(path), "whyml", strict=True)
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 2
    assert all(e.confidence == EdgeConfidence.INFERRED for e in calls)


def test_known_ghost_arguments_block_unknown_and_known_nested_calls(tmp_path: Path) -> None:
    path = tmp_path / "Ghost.mlw"
    path.write_text(
        "module Ghost\nlet value (x:int):int = x\n"
        "let consume (ghost x:int):unit = ()\n"
        "let run () = consume (external (value 0))\nend\n"
    )
    graph = parse_file(str(path), "whyml", strict=True)
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert [(e.source_id, e.target_id) for e in calls] == [
        ("Ghost:Ghost.run", "Ghost:Ghost.consume")
    ]


@pytest.mark.mutation_fast
def test_unknown_contract_and_proof_applications_do_not_become_calls(tmp_path: Path) -> None:
    path = tmp_path / "Proof.mlw"
    path.write_text(
        "module Proof\nlet value (x:int):int = x\n"
        "let run () requires { external (value 0) = 0 } =\n"
        "  assert { external (value 1) = 1 }; ()\nend\n"
    )
    graph = parse_file(str(path), "whyml", strict=True)
    assert not any(e.kind == EdgeKind.CALLS for e in graph.edges)


def test_unavailable_import_does_not_bind_an_unrelated_file(tmp_path: Path) -> None:
    (tmp_path / "Decoy.mlw").write_text("module Decoy\nlet external (x:int):int = x\nend\n")
    (tmp_path / "Use.mlw").write_text(
        "module Use\nuse absent.External\nlet run () = external 0\nend\n"
    )
    graph = parse_directory(str(tmp_path), "whyml", strict=True)
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 1
    assert graph.nodes[calls[0].target_id].origin == NodeOrigin.PROXY
