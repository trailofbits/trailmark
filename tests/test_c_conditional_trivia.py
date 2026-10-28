"""Only literal-zero directives may discard potentially active source calls."""

from __future__ import annotations

from pathlib import Path

import pytest

from trailmark.models import EdgeConfidence, EdgeKind, NodeKind
from trailmark.parse import parse_file


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize(
    "directive,alternative,active",
    [
        ("#if 0 /* first */ + 1 /* second */", "", True),
        ("#if 0 /* first */ || 1 /* second */", "", True),
        ("#if 0 /* first */ == 0 /* second */", "", True),
        ("#if 0 /* first */ && 1 /* second */", "", True),
        ("#if 0 /* first */ /* second */", "", False),
        ("#if 0 /* multi\nline */", "", False),
        ("# /* keyword */ if 0", "", False),
        ("#i\\\nf 0", "", False),
        ("#if 0", "#el\\\nse\n", True),
    ],
)
def test_comments_and_splices_do_not_change_literal_zero_classification(
    tmp_path: Path, directive: str, alternative: str, active: bool, newline: str
) -> None:
    source = (
        "#define BLOCK if (1)\nvoid helper(void) {}\nvoid action(void) {\n"
        " BLOCK { helper(); }\n"
        + directive
        + "\n"
        + alternative
        + " helper(); /* conditional */\n#endif\n helper(); /* tail */\n"
        "} /* action ends */\nvoid after(void) { helper(); }\n"
    ).replace("\n", newline)
    path = tmp_path / "sample.c"
    path.write_bytes(source.encode())
    graph = parse_file(str(path), "c")
    assert {node.id for node in graph.nodes.values() if node.kind == NodeKind.FUNCTION} == {
        "sample:helper",
        "sample:action",
        "sample:after",
    }
    lines = source.splitlines()
    end = lines.index("} /* action ends */") + 1
    unit = graph.nodes["sample:action"]
    assert (unit.location.start_line, unit.location.end_line) == (3, end)
    assert dict(unit.attributes)["syntax_recovered"] is True
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    expected_lines = [4]
    if active:
        expected_lines.append(lines.index(" helper(); /* conditional */") + 1)
    expected_lines.append(lines.index(" helper(); /* tail */") + 1)
    assert [(edge.source_id, edge.target_id) for edge in calls] == [
        *[("sample:action", "sample:helper")] * len(expected_lines),
        ("sample:after", "sample:helper"),
    ]
    assert [
        (edge.location.start_line, edge.location.start_col) for edge in calls if edge.location
    ] == [(line, 9 if index == 0 else 1) for index, line in enumerate(expected_lines)] + [
        (end + 1, 19)
    ]
    assert [edge.confidence for edge in calls] == [
        *[EdgeConfidence.UNCERTAIN] * len(expected_lines),
        EdgeConfidence.CERTAIN,
    ]
    assert any(item.kind == "syntax_recovery" for item in graph.parse_diagnostics)
