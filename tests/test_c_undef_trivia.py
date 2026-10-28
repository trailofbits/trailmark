"""Undefining a declaration macro must invalidate every trivia spelling."""

from pathlib import Path

import pytest

from trailmark.models import EdgeConfidence, EdgeKind, NodeKind
from trailmark.parse import parse_directory, parse_file


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("macro", ["DECL", "DÉCL"])
@pytest.mark.parametrize("header", [False, True], ids=["source", "quoted-header"])
@pytest.mark.parametrize(
    "undef",
    [
        "#undef DECL",
        "# undef DECL",
        "#\tundef DECL",
        "#undef DECL // comment",
        "#undef DECL /* comment */",
        "# undef DECL /* comment */",
        "#undef DECL extra",
    ],
)
def test_undef_trivia_does_not_keep_a_stale_declaration_macro(
    tmp_path: Path, newline: str, macro: str, header: bool, undef: str
) -> None:
    definitions = (
        f"#define {macro}(n) void wired_ ## n(void)\n" + undef.replace("DECL", macro) + "\n"
    )
    if header:
        (tmp_path / "declarations.h").write_bytes(definitions.replace("\n", newline).encode())
        definitions = '#include "declarations.h"\n'
    source = definitions + (
        f"void helper(void) {{}}\n{macro}(left) {{ helper(); helper(); }}\n"
        "void after(void) { helper(); }\n"
    )
    path = tmp_path / "sample.c"
    path.write_bytes(source.replace("\n", newline).encode())
    line = definitions.count("\n") + 2
    for graph in (parse_file(str(path), "c"), parse_directory(str(tmp_path), "c")):
        assert {node.id for node in graph.nodes.values() if node.kind == NodeKind.FUNCTION} == {
            "sample:helper",
            f"sample:{macro}(left)",
            "sample:after",
        }
        unit = graph.nodes[f"sample:{macro}(left)"]
        assert dict(unit.attributes) == {
            "internal_linkage": False,
            "macro_wrapper": macro,
            "source_alias": "left",
            "macro_signature_unresolved": True,
            "parse_incomplete": True,
        }
        assert unit.parameters == ()
        calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
        assert [(edge.source_id, edge.target_id, edge.confidence) for edge in calls] == [
            (unit.id, "sample:helper", EdgeConfidence.CERTAIN),
            (unit.id, "sample:helper", EdgeConfidence.CERTAIN),
            ("sample:after", "sample:helper", EdgeConfidence.CERTAIN),
        ]
        assert [dict(edge.attributes) for edge in calls] == [
            {"macro_signature_unresolved": True},
            {"macro_signature_unresolved": True},
            {},
        ]
        assert [
            (edge.location.start_line, edge.location.start_col, edge.location.end_col)
            for edge in calls
            if edge.location
        ] == [
            (line, len(macro.encode()) + 9, len(macro.encode()) + 17),
            (line, len(macro.encode()) + 19, len(macro.encode()) + 27),
            (line + 1, 19, 27),
        ]
        assert [
            (item.kind, item.location.start_line, item.location.end_line)
            for item in graph.parse_diagnostics
        ] == [("unsupported_syntax", line, line)]


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_unicode_macro_without_undef_retains_a_concrete_signature(
    tmp_path: Path, newline: str
) -> None:
    path = tmp_path / "sample.c"
    path.write_bytes(
        "#define DÉCL(n) void wired_ ## n(void)\nvoid helper(void) {}\n"
        "DÉCL(left) { helper(); helper(); }\n".replace("\n", newline).encode()
    )
    graph = parse_file(str(path), "c")
    assert {unit.id for unit in graph.nodes.values() if unit.kind == NodeKind.FUNCTION} == {
        "sample:helper",
        "sample:wired_left",
    }
    assert [
        (edge.source_id, edge.target_id, edge.confidence)
        for edge in graph.edges
        if edge.kind == EdgeKind.CALLS
    ] == [("sample:wired_left", "sample:helper", EdgeConfidence.CERTAIN)] * 2
    assert dict(graph.nodes["sample:wired_left"].attributes)["macro_wrapper"] == "DÉCL"
    assert graph.parse_diagnostics == []
