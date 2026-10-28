"""Only supported constant-table initializer forms establish a concrete target."""

from pathlib import Path

import pytest

from trailmark.models import EdgeConfidence, EdgeKind
from trailmark.parse import parse_file


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("initializer", ["helper", "&helper", "*helper"])
def test_function_designator_forms_keep_unsupported_initializers_symbolic(
    tmp_path: Path, newline: str, initializer: str
) -> None:
    path = tmp_path / "sample.c"
    path.write_bytes(
        (
            "struct Ops { void (*run)(void); };\nvoid helper(void) {}\n"
            f"static const struct Ops ops = {{ .run = {initializer} }};\n"
            "void action(void) { ops.run(); ops.run(); }\n"
            "void after(void) { helper(); }\n"
        )
        .replace("\n", newline)
        .encode()
    )
    graph = parse_file(str(path), "c")
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    known = initializer != "*helper"
    assert [(edge.source_id, edge.target_id, edge.confidence) for edge in calls] == [
        (
            "sample:action",
            "sample:helper" if known else "proxy.unresolved:ops.run",
            EdgeConfidence.INFERRED if known else EdgeConfidence.UNCERTAIN,
        ),
        (
            "sample:action",
            "sample:helper" if known else "proxy.unresolved:ops.run",
            EdgeConfidence.INFERRED if known else EdgeConfidence.UNCERTAIN,
        ),
        ("sample:after", "sample:helper", EdgeConfidence.CERTAIN),
    ]
    assert [dict(edge.attributes) for edge in calls[:2]] == [
        {"indirect_call": True, "callback_table": "ops", "callback_field": "run"}
        if known
        else {"indirect_call": True, "raw_target": "ops.run"}
    ] * 2
    assert [
        (edge.location.start_line, edge.location.start_col) for edge in calls if edge.location
    ] == [(4, 20), (4, 31), (5, 19)]
    assert graph.parse_diagnostics == []
