"""A single conditional definition is insufficient evidence for a compiled name."""

from pathlib import Path

import pytest

from trailmark.models import EdgeConfidence, EdgeKind, NodeKind
from trailmark.parse import parse_file


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize(
    "before,after,known",
    [
        ("#ifndef DECLS_H\n#define DECLS_H\n", "#endif\n", True),
        ("#if OPTION\n", "#endif\n", False),
        ("#if 0\n", "#endif\n", False),
        ("#ifdef DECLS_H\n#define DECLS_H\n", "#endif\n", False),
        ("#ifndef DECLS_H\n#define OTHER_H\n", "#endif\n", False),
        ("int outside;\n#ifndef DECLS_H\n#define DECLS_H\n", "#endif\n", False),
        ("#ifndef DECLS_H\n#define DECLS_H\n", "#else\n#endif\n", False),
        ("#ifndef DECLS_H\n#define DECLS_H\n", "#elifdef OTHER\n#endif\n", False),
        ("#ifndef DECLS_H\n#define DECLS_H\n", "#elifndef OTHER\n#endif\n", False),
        (
            "static void initialize(void) {\n#ifndef DECLS_H\n#define DECLS_H\n",
            "#endif\n}\n",
            False,
        ),
    ],
    ids=[
        "include-guard",
        "unknown-if",
        "literal-zero",
        "positive-guard",
        "mismatched-guard",
        "declaration-outside-guard",
        "alternative-arm",
        "c23-positive-alternative",
        "c23-negative-alternative",
        "nested-guard",
    ],
)
def test_only_a_complete_matching_include_guard_allows_a_conditional_macro(
    tmp_path: Path, newline: str, before: str, after: str, known: bool
) -> None:
    (tmp_path / "declarations.h").write_bytes(
        (before + "#define DECL(n) void wired_ ## n(void)\n" + after)
        .replace("\n", newline)
        .encode()
    )
    path = tmp_path / "sample.c"
    path.write_bytes(
        (
            '#include "declarations.h"\nvoid helper(void) {}\n'
            "DECL(action) { helper(); helper(); }\nvoid after(void) { helper(); }\n"
        )
        .replace("\n", newline)
        .encode()
    )
    graph = parse_file(str(path), "c")
    owner = "sample:wired_action" if known else "sample:DECL(action)"
    assert {node.id for node in graph.nodes.values() if node.kind == NodeKind.FUNCTION} == {
        "sample:helper",
        owner,
        "sample:after",
    }
    unit = graph.nodes[owner]
    assert (unit.location.start_line, unit.location.end_line) == (3, 3)
    attrs = dict(unit.attributes)
    assert attrs["macro_wrapper"] == "DECL"
    assert attrs["source_alias"] == "action"
    if known:
        assert attrs["expansion_alias"] == "wired_action"
        assert not attrs.get("macro_signature_unresolved")
        assert graph.parse_diagnostics == []
    else:
        assert attrs["macro_signature_unresolved"] is True
        assert attrs["parse_incomplete"] is True
        assert [(item.kind, item.location.start_line) for item in graph.parse_diagnostics] == [
            ("unsupported_syntax", 3)
        ]
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    assert [(edge.source_id, edge.target_id, edge.confidence) for edge in calls] == [
        (owner, "sample:helper", EdgeConfidence.CERTAIN),
        (owner, "sample:helper", EdgeConfidence.CERTAIN),
        ("sample:after", "sample:helper", EdgeConfidence.CERTAIN),
    ]
    assert [dict(edge.attributes) for edge in calls] == [
        *([{}] * 2 if known else [{"macro_signature_unresolved": True}] * 2),
        {},
    ]
