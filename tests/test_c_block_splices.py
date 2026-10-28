"""Physical recovery follows C line splicing at block-macro boundaries."""

from pathlib import Path

import pytest

from trailmark.models import EdgeConfidence, EdgeKind, NodeKind
from trailmark.parse import parse_file
from trailmark.parsers.c import _recovery


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
@pytest.mark.parametrize(
    "block",
    [
        b"BLOCK",
        b"BLO\\\nCK",
        b"BLOCK\\\n",
        b"BLOCK /\\\n* comment */",
        b"BLOCK /* comment *\\\n/",
    ],
)
def test_spliced_block_macro_keeps_calls_and_conservative_confidence(
    tmp_path: Path, newline: bytes, block: bytes
) -> None:
    source = (
        b"#define BLOCK if (1)\nvoid helper(void) {}\nvoid action(void) {\n "
        + block
        + b" { helper(); }\n helper();\n}\nvoid after(void) { helper(); }\n"
    ).replace(b"\n", newline)
    path = tmp_path / "sample.c"
    path.write_bytes(source)
    graph = parse_file(str(path), "c")
    assert {node.id for node in graph.nodes.values() if node.kind == NodeKind.FUNCTION} == {
        "sample:helper",
        "sample:action",
        "sample:after",
    }
    unit = graph.nodes["sample:action"]
    end = len(source.split(newline)) - 2
    assert (unit.location.start_line, unit.location.end_line) == (3, end)
    assert dict(unit.attributes)["syntax_recovered"]
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    assert [(edge.source_id, edge.target_id) for edge in calls] == [
        (unit.id, "sample:helper"),
        (unit.id, "sample:helper"),
        ("sample:after", "sample:helper"),
    ]
    assert [edge.confidence for edge in calls] == [
        EdgeConfidence.UNCERTAIN,
        EdgeConfidence.UNCERTAIN,
        EdgeConfidence.CERTAIN,
    ]
    assert all(dict(edge.attributes).get("syntax_recovered") for edge in calls[:2])
    expected_locations = []
    for line, text in enumerate(source.split(newline), 1):
        if (column := text.find(b"helper()")) >= 0:
            expected_locations.append((line, column, column + 8))
    assert [
        (edge.location.start_line, edge.location.start_col, edge.location.end_col)
        for edge in calls
        if edge.location
    ] == expected_locations
    assert any(item.kind == "syntax_recovery" for item in graph.parse_diagnostics)


@pytest.mark.parametrize("keyword", [b"else", b"do", b"struct", b"union", b"enum"])
def test_spliced_keywords_are_not_masked_as_block_macros(keyword: bytes) -> None:
    split = keyword[:1] + b"\\\n" + keyword[1:]
    tokens = _recovery._source_tokens(b"{ " + split + b" { helper(); } }")
    assert _recovery._mask_ranges(tokens, 0, len(tokens) - 1) == ()
