"""Compare recovery of generated brace-neutral conditionals with a source oracle."""

from __future__ import annotations

import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from trailmark.models import EdgeConfidence, EdgeKind
from trailmark.parse import parse_file

_LEAF = st.integers(0, 3).map(lambda index: ([f" helper{index}();"], [index]))


def _branch(
    parts: tuple[tuple[list[str], list[int]], tuple[list[str], list[int]], bool],
) -> tuple[list[str], list[int]]:
    (left, left_calls), (right, right_calls), disabled = parts
    if disabled:
        return ["#if 0", " } } prose {{{ (", "#else", *right, "#endif"], right_calls
    return ["#if OPTION", *left, "#else", *right, "#endif"], left_calls + right_calls


_BODY = st.recursive(
    _LEAF,
    lambda child: st.tuples(child, child, st.booleans()).map(_branch),
    max_leaves=10,
)


@given(body=_BODY, newline=st.sampled_from(["\n", "\r\n"]))
@settings(max_examples=200, deadline=None)
def test_generated_conditionals_preserve_potential_calls(
    body: tuple[list[str], list[int]], newline: str
) -> None:
    rows, expected = body
    lines = [
        "#define BLOCK if (1)",
        *(f"void helper{index}(void) {{}}" for index in range(4)),
        "void action(void) {",
        " BLOCK { helper0(); }",
        *rows,
        " helper3();",
        "}",
        "void after(void) { helper2(); }",
    ]
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "sample.c"
        path.write_bytes((newline.join(lines) + newline).encode())
        graph = parse_file(str(path), "c")
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    assert [(edge.source_id, edge.target_id) for edge in calls] == [
        *(("sample:action", f"sample:helper{index}") for index in [0, *expected, 3]),
        ("sample:after", "sample:helper2"),
    ]
    assert [edge.confidence for edge in calls] == [
        *([EdgeConfidence.UNCERTAIN] * (len(expected) + 2)),
        EdgeConfidence.CERTAIN,
    ]
    assert graph.nodes["sample:action"].location.end_line == len(lines) - 1
