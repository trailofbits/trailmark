"""A phase-two splice separated from an identifier is still whitespace trivia."""

from pathlib import Path

import pytest

from trailmark.models import EdgeConfidence, EdgeKind, NodeKind
from trailmark.parse import parse_file
from trailmark.parsers.c import _recovery


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
@pytest.mark.parametrize("separator", [b" \\\n", b"\t\\\n ", b" \\\n/**/ ", b" \\\n \\\n "])
def test_standalone_splices_preserve_recovered_body_ownership(
    tmp_path: Path, newline: bytes, separator: bytes
) -> None:
    source = (
        b"#define BLOCK if (1)\nvoid helper(void) {}\nvoid action(void) {\n BLOCK"
        + separator
        + b"{ helper(); helper(); }\n helper();\n} /* end */\n"
        b"void after(void) { helper(); }\n"
    ).replace(b"\n", newline)
    path = tmp_path / "sample.c"
    path.write_bytes(source)
    graph = parse_file(str(path), "c")
    assert {unit.id for unit in graph.nodes.values() if unit.kind == NodeKind.FUNCTION} == {
        "sample:helper",
        "sample:action",
        "sample:after",
    }
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    assert [(edge.source_id, edge.target_id, edge.confidence) for edge in calls] == [
        ("sample:action", "sample:helper", EdgeConfidence.UNCERTAIN),
        ("sample:action", "sample:helper", EdgeConfidence.UNCERTAIN),
        ("sample:action", "sample:helper", EdgeConfidence.UNCERTAIN),
        ("sample:after", "sample:helper", EdgeConfidence.CERTAIN),
    ]
    rows = source.split(newline)
    expected = []
    for line, row in enumerate(rows, 1):
        start = 0
        while (column := row.find(b"helper();", start)) >= 0:
            expected.append((line, column))
            start = column + 9
    assert [
        (edge.location.start_line, edge.location.start_col) for edge in calls if edge.location
    ] == expected
    assert dict(graph.nodes["sample:action"].attributes)["syntax_recovered"] is True
    assert [item.kind for item in graph.parse_diagnostics].count("syntax_recovery") == 1


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_standalone_splice_is_omitted_without_changing_offsets(newline: bytes) -> None:
    source = b"{ BLOCK \\\n { helper(); } }".replace(b"\n", newline)
    ignored: list[tuple[int, int]] = []
    tokens = _recovery._source_tokens(source, ignored)
    assert [token.group() for token in tokens] == [
        b"{",
        b"BLOCK",
        b"{",
        b"helper",
        b"(",
        b")",
        b";",
        b"}",
        b"}",
    ]
    assert _recovery._masked_source(source, ignored) == source.replace(
        b"\\" + newline, b" " + newline
    )
    assert next(token.start() for token in tokens if token.group() == b"helper") == source.index(
        b"helper"
    )


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_spliced_do_statement_does_not_become_a_recovered_macro(
    tmp_path: Path, newline: bytes
) -> None:
    path = tmp_path / "sample.c"
    path.write_bytes(
        b"void helper(void) {}\nvoid action(void) { do \\\n{ helper(); } while (0); }\n".replace(
            b"\n", newline
        )
    )
    graph = parse_file(str(path), "c")
    assert not dict(graph.nodes["sample:action"].attributes).get("syntax_recovered")
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    assert [(edge.source_id, edge.target_id, edge.confidence) for edge in calls] == [
        ("sample:action", "sample:helper", EdgeConfidence.CERTAIN)
    ]
