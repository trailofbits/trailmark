"""Public controls for callback confidence and C linkage boundaries."""

from pathlib import Path

import pytest

from trailmark.models import EdgeConfidence, EdgeKind, NodeKind, NodeOrigin
from trailmark.parse import parse_directory, parse_file


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_recovered_callback_occurrences_stay_unbound_next_to_clean_caller(
    tmp_path: Path, newline: str
) -> None:
    source = (
        "#define BLOCK if (1)\n"
        "struct Hooks { void (*go)(void); };\n"
        "static void helper(void) {}\n"
        "static const struct Hooks table = { .go = helper };\n"
        "void action(void) {\n BLOCK { table.go(); }\n table.go();\n}\n"
        "void after(void) { table.go(); }\n"
    ).replace("\n", newline)
    path = tmp_path / "sample.c"
    path.write_bytes(source.encode())
    graph = parse_file(str(path), "c")
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    assert [edge.source_id for edge in calls] == ["sample:action", "sample:action", "sample:after"]
    assert [edge.confidence for edge in calls] == [
        EdgeConfidence.UNCERTAIN,
        EdgeConfidence.UNCERTAIN,
        EdgeConfidence.INFERRED,
    ]
    assert calls[0].target_id == calls[1].target_id
    assert graph.nodes[calls[0].target_id].name == "table.go"
    assert calls[2].target_id == "sample:helper"
    assert [dict(edge.attributes) for edge in calls[:2]] == [
        {"syntax_recovered": True, "indirect_call": True, "raw_target": "table.go"}
    ] * 2
    assert dict(calls[2].attributes)["callback_table"] == "table"
    assert dict(calls[2].attributes)["callback_field"] == "go"
    assert [
        (edge.location.start_line, edge.location.start_col, edge.location.end_col)
        for edge in calls
        if edge.location
    ] == [(6, 9, 19), (7, 1, 11), (9, 19, 29)]
    assert dict(graph.nodes["sample:action"].attributes)["syntax_recovered"]
    assert not dict(graph.nodes["sample:after"].attributes).get("parse_incomplete")


@pytest.mark.parametrize("member", ["table.go", "pointer->go"])
def test_mutable_member_calls_keep_exact_unresolved_target(tmp_path: Path, member: str) -> None:
    path = tmp_path / "sample.c"
    path.write_text(
        "struct Hooks { void (*go)(void); };\n"
        "void helper(void) {}\nstruct Hooks table = { .go = helper };\n"
        "void action(struct Hooks *pointer) { " + member + "(); " + member + "(); }\n"
    )
    graph = parse_file(str(path), "c")
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    assert len(calls) == 2
    assert [(edge.source_id, edge.confidence) for edge in calls] == [
        ("sample:action", EdgeConfidence.UNCERTAIN)
    ] * 2
    assert [graph.nodes[edge.target_id].name for edge in calls] == [member, member]
    assert all(
        (graph.nodes[edge.target_id].kind, graph.nodes[edge.target_id].origin)
        == (NodeKind.PROXY, NodeOrigin.PROXY)
        for edge in calls
    )
    assert [dict(edge.attributes) for edge in calls] == [
        {"indirect_call": True, "raw_target": member}
    ] * 2
    assert graph.parse_diagnostics == []


def test_extern_definition_remains_visible_to_other_translation_units(tmp_path: Path) -> None:
    (tmp_path / "implementation.c").write_text(
        "extern void exported(void) {}\nstatic void local(void) {}\n"
    )
    (tmp_path / "caller.c").write_text(
        "void exported(void); void local(void);\n"
        "void action(void) { exported(); exported(); local(); }\n"
    )
    graph = parse_directory(str(tmp_path), "c")
    assert dict(graph.nodes["implementation:exported"].attributes)["internal_linkage"] is False
    assert dict(graph.nodes["implementation:local"].attributes)["internal_linkage"] is True
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    assert [edge.source_id for edge in calls] == ["caller:action"] * 3
    assert [edge.target_id for edge in calls[:2]] == ["implementation:exported"] * 2
    assert graph.nodes[calls[2].target_id].kind == NodeKind.PROXY
    assert graph.nodes[calls[2].target_id].name == "caller:local"
    assert [edge.confidence for edge in calls] == [
        EdgeConfidence.INFERRED,
        EdgeConfidence.INFERRED,
        EdgeConfidence.CERTAIN,
    ]
    assert graph.parse_diagnostics == []


def test_single_conditional_callback_definition_does_not_prove_a_target(tmp_path: Path) -> None:
    path = tmp_path / "sample.c"
    path.write_text(
        "struct Hooks { void (*go)(void); };\nvoid helper(void) {}\n"
        "#if OPTION\nstatic const struct Hooks table = { .go = helper };\n#endif\n"
        "void action(void) {\n#if OPTION\n table.go(); table.go();\n#endif\n}\n"
    )
    graph = parse_file(str(path), "c")
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    assert len(calls) == 2
    assert [edge.source_id for edge in calls] == ["sample:action"] * 2
    assert [edge.confidence for edge in calls] == [EdgeConfidence.UNCERTAIN] * 2
    assert [graph.nodes[edge.target_id].name for edge in calls] == ["table.go"] * 2
    assert [dict(edge.attributes) for edge in calls] == [
        {"indirect_call": True, "raw_target": "table.go"}
    ] * 2
    assert graph.parse_diagnostics == []


def test_returned_callable_keeps_inner_and_outer_occurrences_distinct(tmp_path: Path) -> None:
    path = tmp_path / "sample.c"
    path.write_text(
        "void target(void) {}\nvoid (*factory(void))(void) { return target; }\n"
        "void action(void) { factory()(); factory()(); }\n"
    )
    graph = parse_file(str(path), "c")
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    assert [edge.source_id for edge in calls] == ["sample:action"] * 4
    assert [edge.confidence for edge in calls] == [
        EdgeConfidence.UNCERTAIN,
        EdgeConfidence.CERTAIN,
        EdgeConfidence.UNCERTAIN,
        EdgeConfidence.CERTAIN,
    ]
    assert [graph.nodes[edge.target_id].name for edge in calls] == [
        "sample:action.$indirect.factory()",
        "factory",
        "sample:action.$indirect.factory()",
        "factory",
    ]
    assert [dict(edge.attributes) for edge in calls] == [
        {
            "binding_blocked": True,
            "indirect_call": True,
            "raw_target": "sample:action.$indirect.factory()",
        },
        {},
        {
            "binding_blocked": True,
            "indirect_call": True,
            "raw_target": "sample:action.$indirect.factory()",
        },
        {},
    ]
    assert [
        (edge.location.start_line, edge.location.start_col, edge.location.end_col)
        for edge in calls
        if edge.location
    ] == [(3, 20, 31), (3, 20, 29), (3, 33, 44), (3, 33, 42)]
    assert graph.parse_diagnostics == []
