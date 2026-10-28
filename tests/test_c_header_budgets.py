"""Declaration lookup stops at documented traversal bounds, preserving local macros."""

from pathlib import Path

import pytest

from trailmark.models import EdgeConfidence, EdgeKind, NodeKind
from trailmark.parse import parse_file


@pytest.mark.parametrize("dimension", ["depth", "files"])
@pytest.mark.parametrize("extra", [-1, 0, 1], ids=["below", "at-limit", "above"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_header_lookup_boundary_preserves_independent_source_definitions(
    tmp_path: Path, dimension: str, extra: int, newline: str
) -> None:
    count = 16 + extra if dimension == "depth" else 127 + extra
    for index in range(count):
        if index == count - 1:
            text = "#define DECL(n) void n(void)\n"
        elif dimension == "depth":
            text = f'#include "header{index + 1}.h"\n'
        else:
            text = "/* independent header */\n"
        (tmp_path / f"header{index}.h").write_bytes(text.replace("\n", newline).encode())
    includes = "".join(
        f'#include "header{index}.h"\n'
        for index in (range(count) if dimension == "files" else range(1))
    )
    path = tmp_path / "sample.c"
    path.write_bytes(
        (
            includes + "#define LOCAL(n) void source_ ## n(void)\nvoid helper(void) {}\n"
            "DECL(action) { helper(); helper(); }\n"
            "LOCAL(stable) { helper(); }\nvoid after(void) { helper(); }\n"
        )
        .replace("\n", newline)
        .encode()
    )
    graph = parse_file(str(path), "c")
    owner = "sample:action" if extra <= 0 else "sample:DECL(action)"
    assert {node.id for node in graph.nodes.values() if node.kind == NodeKind.FUNCTION} == {
        owner,
        "sample:helper",
        "sample:source_stable",
        "sample:after",
    }
    assert (
        dict(graph.nodes["sample:source_stable"].attributes)["expansion_alias"] == "source_stable"
    )
    assert bool(dict(graph.nodes[owner].attributes).get("macro_signature_unresolved")) == (
        extra > 0
    )
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    assert [(edge.source_id, edge.target_id, edge.confidence) for edge in calls] == [
        (owner, "sample:helper", EdgeConfidence.CERTAIN),
        (owner, "sample:helper", EdgeConfidence.CERTAIN),
        ("sample:source_stable", "sample:helper", EdgeConfidence.CERTAIN),
        ("sample:after", "sample:helper", EdgeConfidence.CERTAIN),
    ]
    assert [item.kind for item in graph.parse_diagnostics] == (
        ["unsupported_syntax"] if extra > 0 else []
    )
