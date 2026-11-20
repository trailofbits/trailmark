"""Proof expressions leave their computational continuation executable."""

from pathlib import Path

import pytest

from trailmark.models.edges import EdgeKind
from trailmark.parse import parse_file


@pytest.mark.parametrize(
    "proof",
    [
        "assert Target(x) == x;",
        "assert Target(x) == x by { assert true; }",
        "assume Target(x) == x;",
        "calc { Target(x); == x; }",
    ],
)
@pytest.mark.parametrize("nested", [False, True])
def test_proof_expression_does_not_erase_calls_in_continuation(
    tmp_path: Path, proof: str, nested: bool
) -> None:
    source = tmp_path / "Continuation.dfy"
    prefix = "  if x == 0 then x else\n" if nested else ""
    source.write_text(
        "function Target(x: int): int { x }\n"
        "function Run(x: int): int {\n" + prefix + f"  {proof}\n"
        "  var y := Target(x);\n"
        "  Target(y)\n"
        "}\n",
    )
    graph = parse_file(str(source), "dafny", strict=True)

    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    assert [edge.location.start_line for edge in calls if edge.location] == [
        4 + int(nested),
        5 + int(nested),
    ]
    assert all(graph.nodes[edge.source_id].name == "Run" for edge in calls)
    assert all(graph.nodes[edge.target_id].name == "Target" for edge in calls)
    proof_references = [
        edge
        for edge in graph.edges
        if edge.kind == EdgeKind.REFERENCES
        and graph.nodes[edge.target_id].name == "Target"
        and dict(edge.attributes).get("reference_context") == "proof"
    ]
    assert [edge.location.start_line for edge in proof_references if edge.location] == [
        3 + int(nested)
    ]
