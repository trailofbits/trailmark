"""Unverified expansion forms cannot establish a concrete body owner."""

from pathlib import Path

import pytest

from trailmark.models import EdgeConfidence, EdgeKind, NodeKind
from trailmark.parse import parse_file


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize(
    "definition",
    [
        '#define DECL(n) __attribute__((deprecated("message"))) void wired_ ## n(void)\n',
        "#define DECL(n) static void thunk_ ## n(void) {} void n(void)\n",
        "#define DECL(...) void __VA_ARGS__(void)\n",
    ],
    ids=["quoted-attribute", "multiple-functions", "variadic"],
)
def test_unsupported_expansion_keeps_distinct_symbolic_owners(
    tmp_path: Path, newline: str, definition: str
) -> None:
    path = tmp_path / "sample.c"
    path.write_bytes(
        (
            definition + "void helper(void) {}\n"
            "DECL(left) { helper(); helper(); }\nDECL(right) { helper(); }\n"
            "void after(void) { helper(); }\n"
        )
        .replace("\n", newline)
        .encode()
    )
    graph = parse_file(str(path), "c")
    assert {node.id for node in graph.nodes.values() if node.kind == NodeKind.FUNCTION} == {
        "sample:helper",
        "sample:DECL(left)",
        "sample:DECL(right)",
        "sample:after",
    }
    for alias, line in (("left", 3), ("right", 4)):
        unit = graph.nodes[f"sample:DECL({alias})"]
        assert dict(unit.attributes)["source_alias"] == alias
        assert dict(unit.attributes)["macro_signature_unresolved"] is True
        assert unit.parameters == ()
        assert unit.return_type is None
        assert (unit.location.start_line, unit.location.end_line) == (line, line)
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    assert [(edge.source_id, edge.target_id, edge.confidence) for edge in calls] == [
        ("sample:DECL(left)", "sample:helper", EdgeConfidence.CERTAIN),
        ("sample:DECL(left)", "sample:helper", EdgeConfidence.CERTAIN),
        ("sample:DECL(right)", "sample:helper", EdgeConfidence.CERTAIN),
        ("sample:after", "sample:helper", EdgeConfidence.CERTAIN),
    ]
    assert [dict(edge.attributes) for edge in calls] == [
        {"macro_signature_unresolved": True},
        {"macro_signature_unresolved": True},
        {"macro_signature_unresolved": True},
        {},
    ]
    assert [(item.kind, item.location.start_line) for item in graph.parse_diagnostics] == [
        ("unsupported_syntax", 3),
        ("unsupported_syntax", 4),
    ]
