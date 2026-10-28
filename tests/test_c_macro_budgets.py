"""Expansion budgets must accept their boundary and reject larger declarations."""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from trailmark.models import EdgeConfidence, EdgeKind, NodeKind
from trailmark.parse import parse_file
from trailmark.parsers.c._macros import _paste_tokens

_LIMIT = 16_384


@given(text=st.text(alphabet="ab# \t\n\r\v\f\u001c\u2003", max_size=100))
@example(text="  pre ## n ## _suffix(void)  ")
@example(text="### ##   ######")
@settings(max_examples=200)
def test_token_paste_whitespace_matches_reference(text: str) -> None:
    assert _paste_tokens(text) == re.sub(r"\s*##\s*", "", text)


@pytest.mark.parametrize("extra", [-1, 0, 1], ids=["below", "at-limit", "above"])
@pytest.mark.parametrize("case", ["replacement-growth", "replacement-shrink", "invocation"])
def test_declaration_macro_text_budget_at_each_expansion_stage(
    tmp_path: Path, case: str, extra: int
) -> None:
    wrapper, name, parameter = "DECL", "x", "n"
    body = "void n(void)"
    parameters = []
    if case == "invocation":
        wrapper = "LONG_DECLARATION_WRAPPER"
        name = "x" * (_LIMIT + extra - len(wrapper) - 2)
    else:
        if case == "replacement-growth":
            name, body, parameters = "xx", "void n(int n)", [("xx", "int")]
        elif case == "replacement-shrink":
            parameter, body = "argument", "void argument(void)"
        expanded = f"void {name}(int {name})" if parameters else f"void {name}(void)"
        # Shrinking a long parameter name keeps the result below the cap, so
        # result-size checks cannot hide a missing raw-definition budget.
        measured = body if case == "replacement-shrink" else expanded
        body = body.replace("void ", "void " + " " * (_LIMIT + extra - len(measured)), 1)
    source = (
        f"#define {wrapper}({parameter}) {body}\nvoid helper(void) {{}}\n"
        f"{wrapper}({name}) {{ helper(); helper(); }}\n"
        "void after(void) { helper(); }\n"
    )
    path = tmp_path / "sample.c"
    path.write_text(source)
    graph = parse_file(str(path), "c")
    known = extra <= 0
    owner = f"sample:{name}" if known else f"sample:{wrapper}({name})"
    assert {node.id for node in graph.nodes.values() if node.kind == NodeKind.FUNCTION} == {
        "sample:helper",
        owner,
        "sample:after",
    }
    unit = graph.nodes[owner]
    attrs = dict(unit.attributes)
    assert attrs["macro_wrapper"] == wrapper
    assert attrs["source_alias"] == name
    if known:
        assert attrs["expansion_alias"] == name
        assert [
            (param.name, param.type_ref.name if param.type_ref else None)
            for param in unit.parameters
        ] == parameters
        assert graph.parse_diagnostics == []
    else:
        assert attrs["macro_signature_unresolved"] is True
        assert unit.parameters == ()
        assert [item.kind for item in graph.parse_diagnostics] == ["unsupported_syntax"]
    assert (unit.location.start_line, unit.location.end_line) == (3, 3)
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    assert [(edge.source_id, edge.target_id, edge.confidence) for edge in calls] == [
        (owner, "sample:helper", EdgeConfidence.CERTAIN),
        (owner, "sample:helper", EdgeConfidence.CERTAIN),
        ("sample:after", "sample:helper", EdgeConfidence.CERTAIN),
    ]


def test_whitespace_runs_in_many_declaration_macros_are_bounded(tmp_path: Path) -> None:
    path = tmp_path / "sample.c"
    path.write_text(
        "#define DECL(n) void "
        + " " * 16_000
        + "n(void)\nvoid helper(void) {}\n"
        + "\n".join(f"DECL(action{i}) {{ helper(); }}" for i in range(60))
        + "\n"
    )
    script = """
import json, sys
from trailmark.parse import parse_file
from trailmark.models import EdgeKind, NodeKind
graph = parse_file(sys.argv[1], 'c')
print(json.dumps({
    'functions': sorted(node.id for node in graph.nodes.values() if node.kind == NodeKind.FUNCTION),
    'calls': [(edge.source_id, edge.target_id, edge.confidence.value)
              for edge in graph.edges if edge.kind == EdgeKind.CALLS],
    'diagnostics': len(graph.parse_diagnostics),
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
        "functions": sorted(["sample:helper", *(f"sample:action{i}" for i in range(60))]),
        "calls": [[f"sample:action{i}", "sample:helper", "certain"] for i in range(60)],
        "diagnostics": 0,
    }
