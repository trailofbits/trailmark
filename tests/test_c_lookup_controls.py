"""Conditional and cyclic includes must not overstate declaration evidence."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from trailmark.models import EdgeConfidence, EdgeKind, NodeKind
from trailmark.parse import parse_file


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("condition", [None, "OPTION", "0"])
def test_conditional_include_cannot_establish_a_declaration_macro(
    tmp_path: Path, newline: str, condition: str | None
) -> None:
    (tmp_path / "declarations.h").write_bytes(b"#define DECL(n) void wired_ ## n(void)\n")
    include = '#include "declarations.h"\n'
    if condition is not None:
        include = f"#if {condition}\n{include}#endif\n"
    source = (
        include + "void helper(void) {}\n"
        "DECL(action) { helper(); helper(); }\nvoid after(void) { helper(); }\n"
    )
    path = tmp_path / "sample.c"
    path.write_bytes(source.replace("\n", newline).encode())
    graph = parse_file(str(path), "c")
    owner = "sample:wired_action" if condition is None else "sample:DECL(action)"
    assert {unit.id for unit in graph.nodes.values() if unit.kind == NodeKind.FUNCTION} == {
        "sample:helper",
        owner,
        "sample:after",
    }
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    assert [(edge.source_id, edge.target_id, edge.confidence) for edge in calls] == [
        (owner, "sample:helper", EdgeConfidence.CERTAIN),
        (owner, "sample:helper", EdgeConfidence.CERTAIN),
        ("sample:after", "sample:helper", EdgeConfidence.CERTAIN),
    ]
    assert bool(dict(graph.nodes[owner].attributes).get("macro_signature_unresolved")) == (
        condition is not None
    )
    assert [item.kind for item in graph.parse_diagnostics] == (
        [] if condition is None else ["unsupported_syntax"]
    )


def test_cyclic_repeated_guarded_includes_do_not_expand_the_lookup_work(tmp_path: Path) -> None:
    for name, other in (("first", "second"), ("second", "first")):
        guard = f"{name.upper()}_H"
        (tmp_path / f"{name}.h").write_text(
            f"#ifndef {guard}\n#define {guard}\n" + f'#include "{other}.h"\n' * 3 + "#endif\n"
        )
    path = tmp_path / "sample.c"
    path.write_text(
        '#include "first.h"\n#define DECL(n) void n(void)\nvoid helper(void) {}\n'
        "DECL(action) { helper(); helper(); }\n"
    )
    script = """import json, sys
from trailmark.parse import parse_file
from trailmark.models import EdgeKind, NodeKind
graph = parse_file(sys.argv[1], "c")
print(json.dumps({
    "functions": sorted(unit.id for unit in graph.nodes.values() if unit.kind == NodeKind.FUNCTION),
    "calls": [(edge.source_id, edge.target_id, edge.confidence.value)
              for edge in graph.edges if edge.kind == EdgeKind.CALLS],
    "diagnostics": len(graph.parse_diagnostics),
}))
"""
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script, str(path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert json.loads(result.stdout) == {
        "functions": ["sample:action", "sample:helper"],
        "calls": [["sample:action", "sample:helper", "certain"]] * 2,
        "diagnostics": 0,
    }
