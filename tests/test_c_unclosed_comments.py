"""Unterminated block comments stay opaque and cannot trigger repeated rescans."""

import subprocess
import sys
from pathlib import Path

import pytest

from trailmark.parse import parse_file
from trailmark.parsers.c import _recovery


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
@pytest.mark.parametrize("opening", [b"/*", b"/\\\n*"])
def test_unclosed_comment_cannot_supply_a_function_closing_brace(
    newline: bytes, opening: bytes
) -> None:
    body = (b"{ BLOCK { live(); } " + opening + b" ghost(); }\n}").replace(b"\n", newline)
    tokens = _recovery._source_tokens(body)
    assert [token.group() for token in tokens] == [
        b"{",
        b"BLOCK",
        b"{",
        b"live",
        b"(",
        b")",
        b";",
        b"}",
    ]
    assert _recovery._physical_end(tokens, 0) is None


def test_many_unclosed_comment_openers_are_scanned_without_hanging() -> None:
    script = """from trailmark.parsers.c import _recovery
tokens = _recovery._source_tokens(b"{ BLOCK { live(); } " + b"/*x" * 32_000 + b"\\n}")
assert [token.group() for token in tokens] == [
    b"{", b"BLOCK", b"{", b"live", b"(", b")", b";", b"}"
]
"""
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_unclosed_comment_keeps_original_diagnostics(tmp_path: Path, newline: bytes) -> None:
    path = tmp_path / "sample.c"
    path.write_bytes(
        (
            b"#define BLOCK if (1)\nvoid helper(void) {}\n"
            b"void action(void) { BLOCK { helper(); } /* unclosed\n ghost(); }\n"
        ).replace(b"\n", newline)
    )
    graph = parse_file(str(path), "c")
    assert "sample:helper" in graph.nodes
    assert graph.parse_diagnostics
    assert not dict(graph.nodes["sample:action"].attributes).get("syntax_recovered")
