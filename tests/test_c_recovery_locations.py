"""Recovery retains byte columns and complete multiline call spans."""

from pathlib import Path

import pytest

from trailmark.models import EdgeConfidence, EdgeKind, NodeKind
from trailmark.parse import parse_file


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_utf8_and_spliced_call_arguments_preserve_original_call_locations(
    tmp_path: Path, newline: bytes
) -> None:
    source = (
        (
            "#define BLOCK if (1)\nvoid consume(const char *text) {}\n"
            "void helper(void) {}\nvoid ghost(void) {}\nvoid action(void) {\n"
            ' consume("é \\\n ghost(); }");\n BLOCK { helper(); }\n'
            ' consume("λ"); helper();\n} /* action ends */\n'
            "void after(void) { helper(); }\n"
        )
        .encode()
        .replace(b"\n", newline)
    )
    path = tmp_path / "sample.c"
    path.write_bytes(source)
    graph = parse_file(str(path), "c")
    assert {node.id for node in graph.nodes.values() if node.kind == NodeKind.FUNCTION} == {
        "sample:consume",
        "sample:helper",
        "sample:ghost",
        "sample:action",
        "sample:after",
    }
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    assert [(edge.source_id, edge.target_id) for edge in calls] == [
        ("sample:action", "sample:consume"),
        ("sample:action", "sample:helper"),
        ("sample:action", "sample:consume"),
        ("sample:action", "sample:helper"),
        ("sample:after", "sample:helper"),
    ]
    assert [edge.confidence for edge in calls] == [EdgeConfidence.UNCERTAIN] * 4 + [
        EdgeConfidence.CERTAIN
    ]
    assert [
        (
            edge.location.file_path,
            edge.location.start_line,
            edge.location.start_col,
            edge.location.end_line,
            edge.location.end_col,
        )
        for edge in calls
        if edge.location
    ] == [
        (str(path), 6, 1, 7, 13),
        (str(path), 8, 9, 8, 17),
        (str(path), 9, 1, 9, 14),
        (str(path), 9, 16, 9, 24),
        (str(path), 11, 19, 11, 27),
    ]
    unit = graph.nodes["sample:action"]
    assert (unit.location.start_line, unit.location.end_line) == (5, 10)
    assert all(dict(edge.attributes).get("syntax_recovered") for edge in calls[:4])
    assert any(item.kind == "syntax_recovery" for item in graph.parse_diagnostics)
