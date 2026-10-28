"""C line-splicing must not expose braces or calls from comments and literals."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from trailmark.models import EdgeConfidence, EdgeKind, NodeKind
from trailmark.parse import parse_file
from trailmark.parsers.c import _recovery


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize(
    "comment",
    [
        "// comment continues \\\n ghost(); } } }",
        "/\\\n/ ghost(); } } }",
        "/\\\n* ghost(); } } } */",
        "/* ghost(); } } } *\\\n/",
        "/\\\n* ghost(); } } } *\\\n/",
        'const char *text = "ignored \\\\\nn } } } ghost();";',
        '#define TEXT "ignored \\\\\nn } } } ghost();"',
        'const char *text = "ignored \\\\\\\nn } } } ghost();";',
        '#define TEXT "ignored \\\\\\\nn } } } ghost();"',
        'const char *text = "ignored \\\\\\\\\nn } } } ghost();";',
        '#define TEXT "ignored \\\\\\\\\nn } } } ghost();"',
        'const char *text = "ignored \\\n } } } ghost();";',
        "int character = '\\\n}';",
        "#define UNUSED /* comment\n } } } ghost();\n */",
        '#define UNUSED "/* not a comment"',
        "#define UNUSED // /* not a block comment",
        "#define UNUSED /\\\n* comment\n } } } ghost();\n *\\\n/",
    ],
)
def test_spliced_trivia_preserves_calls_and_function_boundaries(
    tmp_path: Path, comment: str, newline: str
) -> None:
    source = (
        "#define BLOCK if (1)\n"
        "void helper(void) {}\n"
        "void ghost(void) {}\n"
        "void tail(void) {}\n"
        "void action(void) {\n"
        " BLOCK { helper(); }\n" + comment + "\n"
        " helper();\n"
        " tail();\n"
        "} /* action ends */\n"
        "void after(void) { tail(); }\n"
    ).replace("\n", newline)
    path = tmp_path / "sample.c"
    path.write_bytes(source.encode())
    graph = parse_file(str(path), "c")
    assert {unit.id for unit in graph.nodes.values() if unit.kind == NodeKind.FUNCTION} == {
        "sample:helper",
        "sample:ghost",
        "sample:tail",
        "sample:action",
        "sample:after",
    }
    unit = graph.nodes["sample:action"]
    end = source.splitlines().index("} /* action ends */") + 1
    assert (unit.location.start_line, unit.location.end_line) == (5, end)
    assert dict(unit.attributes)["syntax_recovered"]
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    assert [(edge.source_id, edge.target_id) for edge in calls] == [
        ("sample:action", "sample:helper"),
        ("sample:action", "sample:helper"),
        ("sample:action", "sample:tail"),
        ("sample:after", "sample:tail"),
    ]
    assert [
        (edge.location.start_line, edge.location.start_col)
        for edge in calls
        if edge.location is not None
    ] == [(6, 9), (end - 2, 1), (end - 1, 1), (end + 1, 19)]
    assert [edge.confidence for edge in calls] == [
        EdgeConfidence.UNCERTAIN,
        EdgeConfidence.UNCERTAIN,
        EdgeConfidence.UNCERTAIN,
        EdgeConfidence.CERTAIN,
    ]
    assert graph.parse_diagnostics


@given(
    noise=st.text(alphabet="abc {}();'\"", max_size=80),
    opening=st.sampled_from([b"//", b"/\\\n/", b"/*", b"/\\\n*"]),
    newline=st.sampled_from([b"\n", b"\r\n"]),
)
@settings(max_examples=200, deadline=None)
def test_comment_braces_do_not_affect_physical_body_spans(
    noise: str, opening: bytes, newline: bytes
) -> None:
    closing = b"" if opening.endswith(b"/") else b"*\\\n/"
    comment = opening + b" ignored \\\n" + noise.encode() + closing
    body = (b"{\n" + comment + b"\nhelper();\n}").replace(b"\n", newline)
    tokens = _recovery._source_tokens(body)
    assert [token.group() for token in tokens] == [
        b"{",
        b"helper",
        b"(",
        b")",
        b";",
        b"}",
    ]
    end = _recovery._physical_end(tokens, 0)
    assert end is not None and tokens[end].end() == len(body)


@given(
    noise=st.text(alphabet="abc {}();", max_size=80),
    quote=st.sampled_from([b'"', b"'"]),
    newline=st.sampled_from([b"\n", b"\r\n"]),
    slashes=st.integers(min_value=1, max_value=8),
)
@settings(max_examples=200, deadline=None)
def test_spliced_literal_is_one_token(
    noise: str, quote: bytes, newline: bytes, slashes: int
) -> None:
    literal = quote + b"\\" * slashes + newline + b"n" + noise.encode() + quote
    body = b"{" + literal + b"; helper(); }"
    tokens = _recovery._source_tokens(body)
    assert [token.group() for token in tokens] == [
        b"{",
        literal,
        b";",
        b"helper",
        b"(",
        b")",
        b";",
        b"}",
    ]
    end = _recovery._physical_end(tokens, 0)
    assert end is not None and tokens[end].end() == len(body)


@given(
    slashes=st.integers(min_value=1, max_value=8),
    newline=st.sampled_from([b"\n", b"\r\n"]),
)
@settings(max_examples=100, deadline=None)
def test_continued_directives_cannot_expose_replacement_text(slashes: int, newline: bytes) -> None:
    directive = b'#define TEXT "' + b"\\" * slashes + newline + b'n } } ghost();"'
    body = b"{" + newline + directive + newline + b"helper(); }"
    tokens = _recovery._source_tokens(body)
    assert [token.group().rstrip(b"\r\n") for token in tokens] == [
        b"{",
        directive,
        b"helper",
        b"(",
        b")",
        b";",
        b"}",
    ]
    end = _recovery._physical_end(tokens, 0)
    assert end is not None and tokens[end].end() == len(body)


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_unclosed_spliced_literal_reports_diagnostics_without_hanging(
    tmp_path: Path, newline: bytes
) -> None:
    source = (
        b'#define BLOCK if (1)\nvoid action(void) { BLOCK {}\nconst char *text = "'
        + b"\\\n" * 64
        + b"unfinished\n}\n"
    ).replace(b"\n", newline)
    path = tmp_path / "sample.c"
    path.write_bytes(source)
    script = """import sys
from trailmark.parse import parse_file
graph = parse_file(sys.argv[1], "c")
assert graph.parse_diagnostics
"""
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script, str(path)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
