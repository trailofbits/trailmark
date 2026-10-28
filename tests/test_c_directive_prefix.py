"""Preprocessor directives may follow whitespace and comments on a line."""

from pathlib import Path

import pytest

from trailmark.models import EdgeConfidence, EdgeKind, NodeKind
from trailmark.parse import parse_file
from trailmark.parsers.c import _recovery


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
@pytest.mark.parametrize(
    "prefix",
    [
        b"",
        b"/* leading */ ",
        b"/* multi\n line */ ",
        b"\v",
        b"\f",
        b" \t/* one */ /* two */ ",
        b"/\\\n* leading *\\\n/ ",
    ],
)
def test_comment_prefixed_directives_preserve_active_calls_and_spans(
    tmp_path: Path, newline: bytes, prefix: bytes
) -> None:
    source = (
        b"#define BLOCK if (1)\nvoid helper(void) {}\nvoid ghost(void) {}\n"
        b"void action(void) {\n BLOCK { helper(); }\n"
        + prefix
        + b"#if 0\n disabled prose ??? ghost(); } }\n"
        + prefix
        + b"#endif\n helper();\n} /* action ends */\n"
        b"void after(void) { helper(); }\n"
    ).replace(b"\n", newline)
    path = tmp_path / "sample.c"
    path.write_bytes(source)
    graph = parse_file(str(path), "c")
    assert {node.id for node in graph.nodes.values() if node.kind == NodeKind.FUNCTION} == {
        "sample:helper",
        "sample:ghost",
        "sample:action",
        "sample:after",
    }
    unit = graph.nodes["sample:action"]
    end = source.split(newline).index(b"} /* action ends */") + 1
    assert (unit.location.start_line, unit.location.end_line) == (4, end)
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
    assert [
        (edge.location.start_line, edge.location.start_col) for edge in calls if edge.location
    ] == [(5, 9), (end - 1, 1), (end + 1, 19)]
    assert any(item.kind == "syntax_recovery" for item in graph.parse_diagnostics)


@pytest.mark.parametrize("prefix", [b"/* first */ ", b"\v", b"\f"])
def test_prefixed_unknown_condition_remains_potentially_active(prefix: bytes) -> None:
    source = prefix + b"#if 0 + 1\n{ helper(); }\n" + prefix + b"#endif\n"
    tokens = _recovery._source_tokens(source)
    assert [token.group() for token in tokens] == [
        prefix + b"#if 0 + 1",
        b"{",
        b"helper",
        b"(",
        b")",
        b";",
        b"}",
        prefix + b"#endif",
    ]
