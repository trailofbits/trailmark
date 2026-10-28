"""A leading comment must not make a nonconditional directive visible as code."""

from pathlib import Path

import pytest

from trailmark.models import EdgeConfidence, EdgeKind, NodeKind
from trailmark.parse import parse_file
from trailmark.parsers.c import _recovery


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
@pytest.mark.parametrize("prefix", [b"/* leading */ ", b"/* multi\nline */ "])
def test_prefixed_macro_definition_remains_opaque_inside_recovered_body(
    tmp_path: Path, newline: bytes, prefix: bytes
) -> None:
    source = (
        b"#define BLOCK if (1)\nvoid helper(void) {}\nvoid action(void) {\n"
        b" BLOCK { helper(); }\n" + prefix + b'#define UNUSED "/*"\n'
        b" helper();\n} /* action ends */\nvoid after(void) { helper(); }\n"
    ).replace(b"\n", newline)
    path = tmp_path / "sample.c"
    path.write_bytes(source)
    graph = parse_file(str(path), "c")
    assert {node.id for node in graph.nodes.values() if node.kind == NodeKind.FUNCTION} == {
        "sample:helper",
        "sample:action",
        "sample:after",
    }
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    assert [(edge.source_id, edge.target_id) for edge in calls] == [
        ("sample:action", "sample:helper"),
        ("sample:action", "sample:helper"),
        ("sample:after", "sample:helper"),
    ]
    assert [edge.confidence for edge in calls] == [
        EdgeConfidence.UNCERTAIN,
        EdgeConfidence.UNCERTAIN,
        EdgeConfidence.CERTAIN,
    ]
    unit = graph.nodes["sample:action"]
    assert unit.location.end_line == source.split(newline).index(b"} /* action ends */") + 1
    assert dict(unit.attributes)["syntax_recovered"]


def test_prefixed_directive_is_retained_as_one_opaque_token() -> None:
    source = b"/* ordinary # comment */\n/* leading */ #define VALUE { fake(); }\n{ helper(); }"
    ignored: list[tuple[int, int]] = []
    tokens = _recovery._source_tokens(source, ignored)
    assert [token.group() for token in tokens] == [
        b"/* leading */ #define VALUE { fake(); }",
        b"{",
        b"helper",
        b"(",
        b")",
        b";",
        b"}",
    ]
    masked = _recovery._masked_source(source, ignored)
    assert masked == source.replace(
        b"/* leading */ #define VALUE { fake(); }",
        b" " * len(b"/* leading */ #define VALUE { fake(); }"),
    )
