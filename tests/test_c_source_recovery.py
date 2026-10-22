"""Synthetic C declaration, physical-body, and callback-binding controls."""

from __future__ import annotations

from pathlib import Path

import pytest

from trailmark.models import CodeGraph, EdgeConfidence, EdgeKind, NodeKind
from trailmark.parse import parse_directory, parse_file


def _parse(tmp_path: Path, source: str) -> CodeGraph:
    path = tmp_path / "sample.c"
    path.write_text(source)
    return parse_file(str(path), "c")


def _calls(graph: CodeGraph, source: str) -> set[str]:
    return {
        edge.target_id
        for edge in graph.edges
        if edge.source_id == f"sample:{source}" and edge.kind == EdgeKind.CALLS
    }


BLOCKS = """\
#define BLOCK_BEGIN do
#define BLOCK_RESCUE while (0); if (0)
#define BLOCK_END()
void first(void) {} void second(void) {} void third(void) {}
void fourth(void) {} void final_step(void) {} void independent(void) {}
void action(void) {
 BLOCK_BEGIN { first(); } BLOCK_RESCUE { second(); } BLOCK_END();
 BLOCK_BEGIN { third(); } BLOCK_RESCUE { fourth(); } BLOCK_END();
 final_step();
} /* action ends */
void after(void) { independent(); }
"""


@pytest.mark.parametrize("line_break", ["\n", "\r\n"])
@pytest.mark.parametrize(
    "extra", ["", "const char *text = \"}\"; char c = '{'; /* } { */", "#define UNUSED \\\n { }"]
)
def test_balanced_body_recovery_preserves_span_calls_and_following_owner(
    tmp_path: Path, extra: str, line_break: str
) -> None:
    source = BLOCKS.replace(" final_step();", f" {extra}\n final_step();")
    source = source.replace("\n", line_break)
    graph = _parse(tmp_path, source)
    unit = graph.nodes["sample:action"]
    assert unit.location.end_line == source.splitlines().index("} /* action ends */") + 1
    assert dict(unit.attributes)["parse_incomplete"]
    assert dict(unit.attributes)["syntax_recovered"]
    assert {
        f"sample:{name}" for name in ("first", "second", "third", "fourth", "final_step")
    } <= _calls(graph, "action")
    assert _calls(graph, "after") == {"sample:independent"}
    assert all(
        edge.confidence == EdgeConfidence.UNCERTAIN
        for edge in graph.edges
        if edge.source_id == unit.id and edge.kind == EdgeKind.CALLS
    )
    assert any(item.kind == "syntax_recovery" for item in graph.parse_diagnostics)
    with pytest.raises(ValueError, match="diagnostic"):
        parse_file(str(tmp_path / "sample.c"), "c", strict=True)


def test_brace_neutral_conditional_groups_allow_recovery(tmp_path: Path) -> None:
    source = BLOCKS.replace(" BLOCK_BEGIN { first();", "#if OPTION\n BLOCK_BEGIN { first();")
    source = source.replace(" final_step();", "#endif\n final_step();")
    graph = _parse(tmp_path, source)
    assert graph.parse_diagnostics
    assert dict(graph.nodes["sample:action"].attributes)["syntax_recovered"]
    assert "sample:final_step" in _calls(graph, "action")


def test_ordinary_nested_blocks_and_typedefs_keep_normal_calls(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        "void helper(void) {}\nvoid action(void) { struct Local { int field; }; "
        "do { helper(); } while (0); if (1) { helper(); } else { helper(); } }",
    )
    assert not graph.parse_diagnostics
    assert _calls(graph, "action") == {"sample:helper"}
    assert not dict(graph.nodes["sample:action"].attributes).get("parse_incomplete")


def test_macro_signature_aliases_from_quoted_header_keep_body_owners(tmp_path: Path) -> None:
    (tmp_path / "declarations.h").write_text(
        "#ifndef DECLARATIONS_H\n#define DECLARATIONS_H\n"
        "#define API\n#define DECL(n) static void wired_ ## n(int code)\n#endif\n"
    )
    graph = _parse(
        tmp_path,
        '#include "declarations.h"\nvoid audit(int code) {} void release(int code) {}\n'
        "API DECL(publish) { audit(code); }\nDECL(retire) { release(code); }\n"
        "void action(void) { wired_publish(1); wired_retire(2); }",
    )
    for alias, callee in (("publish", "audit"), ("retire", "release")):
        unit = graph.nodes[f"sample:wired_{alias}"]
        attrs = dict(unit.attributes)
        assert attrs["macro_wrapper"] == "DECL"
        assert attrs["source_alias"] == alias
        assert attrs["expansion_alias"] == f"wired_{alias}"
        assert attrs["internal_linkage"]
        assert unit.parameters[0].name == "code"
        assert unit.return_type is not None and unit.return_type.name == "void"
        assert _calls(graph, unit.name) == {f"sample:{callee}"}
    assert _calls(graph, "action") == {"sample:wired_publish", "sample:wired_retire"}
    assert not graph.parse_diagnostics


@pytest.mark.parametrize(
    "definition",
    [
        "#if OPTION\n#define DECL(n) void one_ ## n(void)\n"
        "#else\n#define DECL(n) void two_ ## n(void)\n#endif\n",
        "#define DECL(n) DECL(n)\n",
        "#define DECL(n) void n(void)\n#undef DECL\n",
        "#define DECL(n) _Static_assert(1, #n)\n",
    ],
)
def test_unsupported_macro_signatures_keep_distinct_owners_and_targets(
    tmp_path: Path, definition: str
) -> None:
    graph = _parse(
        tmp_path,
        definition + "#define API\nvoid helper(void) {}\n"
        "API DECL(left) { helper(); }\nAPI DECL(right) { helper(); }\n",
    )
    for name in ("DECL(left)", "DECL(right)"):
        unit = graph.nodes[f"sample:{name}"]
        assert dict(unit.attributes)["macro_signature_unresolved"]
        assert _calls(graph, name) == {"sample:helper"}
        edges = [
            edge
            for edge in graph.edges
            if edge.source_id == unit.id and edge.kind == EdgeKind.CALLS
        ]
        assert all(edge.confidence == EdgeConfidence.CERTAIN for edge in edges)
        assert all(dict(edge.attributes)["macro_signature_unresolved"] for edge in edges)
        assert not any(dict(edge.attributes).get("binding_blocked") for edge in edges)
        assert unit.parameters == ()
    assert any(item.kind == "unsupported_syntax" for item in graph.parse_diagnostics)


def test_unavailable_macros_keep_colliding_declarations_and_their_own_calls(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        "void first(void) {} void second(void) {}\n"
        "API UNAVAILABLE(left) { first(); }\nAPI UNAVAILABLE(right) { second(); }",
    )
    units = [unit for unit in graph.nodes.values() if unit.name == "UNAVAILABLE"]
    assert len(units) == 2
    assert {frozenset(_calls(graph, unit.id.removeprefix("sample:"))) for unit in units} == {
        frozenset({"sample:first"}),
        frozenset({"sample:second"}),
    }


def test_real_function_with_unnamed_typedef_parameter_is_not_a_macro(tmp_path: Path) -> None:
    graph = _parse(tmp_path, "typedef int Value;\nvoid action(Value) {}")
    assert "sample:action" in graph.nodes
    assert not graph.parse_diagnostics
    assert "macro_wrapper" not in dict(graph.nodes["sample:action"].attributes)


def test_later_macro_does_not_rename_an_earlier_function(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        "typedef int Value; void action(Value) {}\n#define action(n) void later_ ## n(void)\n",
    )
    assert "sample:action" in graph.nodes
    assert not graph.parse_diagnostics


def test_nested_wrapper_and_include_cycle_remain_bounded(tmp_path: Path) -> None:
    (tmp_path / "first.h").write_text('#include "second.h"\n#define DECL(n) INNER(n)\n')
    (tmp_path / "second.h").write_text(
        '#include "first.h"\n#define INNER(n) void wired_ ## n(void)\n'
    )
    graph = _parse(tmp_path, '#include "first.h"\nDECL(action) {}')
    assert "sample:wired_action" in graph.nodes
    assert not graph.parse_diagnostics


def test_expanded_callback_parameter_blocks_same_name_global(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        "#define DECL(n) void n(void (*worker)(void))\n"
        "void worker(void) {}\nDECL(action) { worker(); }",
    )
    assert graph.nodes["sample:action"].parameters[0].name == "worker"
    assert all(target.startswith("proxy.") for target in _calls(graph, "action"))


CALLBACKS = """\
struct Hooks { void (*start)(void); };
static void setup(void) {} static void alternate(void) {}
static const struct Hooks hooks = { .start = &setup };
static const struct Hooks other = { .start = alternate };
void action(void) { hooks.start(); other.start(); }
"""


def test_incomplete_caller_does_not_infer_callback_table_targets(tmp_path: Path) -> None:
    declarations = CALLBACKS.split("void action")[0]
    source = declarations + BLOCKS.replace("first();", "hooks.start();").replace(
        "second();", "other.start();"
    )
    graph = _parse(tmp_path, source)
    assert dict(graph.nodes["sample:action"].attributes)["syntax_recovered"]
    assert dict(graph.nodes["sample:action"].attributes)["parse_incomplete"]
    assert not ({"sample:setup", "sample:alternate"} & _calls(graph, "action"))
    assert not any(dict(edge.attributes).get("callback_table") for edge in graph.edges)


def test_constant_designated_tables_resolve_by_object_and_field(tmp_path: Path) -> None:
    graph = _parse(tmp_path, CALLBACKS)
    assert _calls(graph, "action") == {"sample:setup", "sample:alternate"}
    edges = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    assert all(edge.confidence == EdgeConfidence.INFERRED for edge in edges)
    assert {dict(edge.attributes)["callback_table"] for edge in edges} == {"hooks", "other"}
    graph = parse_directory(str(tmp_path), "c")
    assert _calls(graph, "action") == {"sample:setup", "sample:alternate"}


@pytest.mark.parametrize(
    "source",
    [
        CALLBACKS.replace("static const struct Hooks hooks", "static struct Hooks hooks"),
        CALLBACKS.replace("void action(void)", "void action(struct Hooks hooks)"),
        CALLBACKS.replace(
            "hooks.start();", "struct Hooks hooks = { .start = alternate }; hooks.start();"
        ),
        CALLBACKS.replace(".start = &setup", ".start = &setup, .start = alternate"),
        CALLBACKS.replace(".start = &setup", ".start = missing"),
        CALLBACKS.replace("hooks.start();", "unknown.start();"),
        CALLBACKS.replace(
            "static const struct Hooks hooks = { .start = &setup };",
            "#if OPTION\nstatic const struct Hooks hooks = { .start = setup };\n"
            "#else\nstatic const struct Hooks hooks = { .start = alternate };\n#endif",
        ),
    ],
)
def test_unproven_callback_targets_stay_uncertain(tmp_path: Path, source: str) -> None:
    graph = _parse(tmp_path, source)
    unresolved = [
        edge
        for edge in graph.edges
        if edge.source_id == "sample:action"
        and edge.kind == EdgeKind.CALLS
        and dict(edge.attributes).get("callback_table") != "other"
    ]
    assert len(unresolved) == 1
    assert graph.nodes[unresolved[0].target_id].kind == NodeKind.PROXY
    assert unresolved[0].confidence == EdgeConfidence.UNCERTAIN
