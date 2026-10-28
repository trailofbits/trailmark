"""Function-return declarators must not hide the defining function's parameters."""

from __future__ import annotations

from pathlib import Path

import pytest

from trailmark.models import EdgeConfidence, EdgeKind, NodeKind, NodeOrigin
from trailmark.parse import parse_directory, parse_file


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize(
    "declaration,returned,bridge",
    [
        ("void *factory(PARAMS)", "0", ""),
        ("void (*factory(PARAMS))(int returned)", "target", ""),
        ("void (*(factory)(PARAMS))(int returned)", "target", ""),
        (
            "void (*(*factory(PARAMS))(int middle))(int returned)",
            "bridge",
            "void (*bridge(int middle))(int returned) { return target; }\n",
        ),
    ],
    ids=["pointer", "function-pointer", "parenthesized-name", "nested-function-pointer"],
)
def test_factory_parameters_own_callback_binding_in_nested_declarators(
    tmp_path: Path, newline: str, declaration: str, returned: str, bridge: str
) -> None:
    declaration = declaration.replace("PARAMS", "void (*callback)(void), int count")
    prefix = "void callback(void) {}\nvoid target(int returned) {}\n" + bridge
    source = (
        prefix
        + declaration
        + " {\n callback();\n callback();\n return "
        + returned
        + ";\n}\nvoid after(void) { callback(); }\n"
    )
    path = tmp_path / "sample.c"
    path.write_bytes(source.replace("\n", newline).encode())
    start = prefix.count("\n") + 1
    for graph in (parse_file(str(path), "c"), parse_directory(str(tmp_path), "c")):
        expected = {"sample:callback", "sample:target", "sample:factory", "sample:after"}
        if bridge:
            expected.add("sample:bridge")
        assert {
            node.id for node in graph.nodes.values() if node.kind == NodeKind.FUNCTION
        } == expected
        unit = graph.nodes["sample:factory"]
        assert (unit.location.start_line, unit.location.end_line) == (start, start + 4)
        calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
        assert [edge.source_id for edge in calls] == [unit.id, unit.id, "sample:after"]
        assert [edge.confidence for edge in calls] == [
            EdgeConfidence.UNCERTAIN,
            EdgeConfidence.UNCERTAIN,
            EdgeConfidence.CERTAIN,
        ]
        assert calls[0].target_id == calls[1].target_id
        target = graph.nodes[calls[0].target_id]
        assert (target.kind, target.origin, target.name) == (
            NodeKind.PROXY,
            NodeOrigin.PROXY,
            "sample:factory.$binding.callback",
        )
        assert [dict(edge.attributes) for edge in calls[:2]] == [
            {
                "binding_blocked": True,
                "binding_reason": "lexical_callable",
                "raw_target": "sample:factory.$binding.callback",
            }
        ] * 2
        assert calls[2].target_id == "sample:callback"
        assert [
            (edge.location.start_line, edge.location.start_col, edge.location.end_col)
            for edge in calls
            if edge.location
        ] == [(start + 1, 1, 11), (start + 2, 1, 11), (start + 5, 19, 29)]
        assert [(p.name, p.type_ref.name if p.type_ref else None) for p in unit.parameters] == [
            ("callback", "void"),
            ("count", "int"),
        ]
        assert graph.parse_diagnostics == []
        assert not dict(unit.attributes).get("parse_incomplete")
