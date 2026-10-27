"""C call binding, source recovery, and bounded header lookup regressions."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from tree_sitter import Parser
from tree_sitter_language_pack import get_language

from tests.test_c_source_recovery import BLOCKS
from trailmark.models import CodeEdge, CodeGraph, EdgeConfidence, EdgeKind, NodeKind
from trailmark.parse import parse_directory, parse_file
from trailmark.parsers.c import _recovery
from trailmark.parsers.c.parser import CParser


def _source(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "sample.c"
    path.write_text(text)
    return path


def _calls(graph: CodeGraph, name: str) -> list[CodeEdge]:
    return [
        edge
        for edge in graph.edges
        if edge.kind == EdgeKind.CALLS and graph.nodes[edge.source_id].name == name
    ]


def test_guarded_cpp_namespace_preserves_c_functions(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        "#ifdef OPTIONAL_NAMESPACE\nnamespace OPTIONAL_NAMESPACE {\n#endif\n"
        "#define FOR(n) for (int i = 0; i < (n); ++i)\n"
        "void helper(void) {}\n"
        "void action(void) { FOR(3) { helper(); } }\n"
        "#ifdef OPTIONAL_NAMESPACE\n}\n#endif\n",
    )
    graph = parse_file(str(path), "c")
    functions = {n.name for n in graph.nodes.values() if n.kind == NodeKind.FUNCTION}
    assert functions == {"helper", "action"}
    assert graph.nodes["sample:helper"].location.start_line == 5
    assert graph.nodes["sample:action"].location.end_line == 6
    assert "sample:helper" in {e.target_id for e in _calls(graph, "action")}
    assert graph.parse_diagnostics  # Keep original syntax evidence.


@pytest.mark.parametrize("closing", ["OTHER_NAMESPACE", "!OPTIONAL_NAMESPACE"])
def test_cpp_namespace_recovery_requires_matching_positive_guards(
    tmp_path: Path, closing: str
) -> None:
    closing_guard = "#ifndef OPTIONAL_NAMESPACE" if closing.startswith("!") else f"#ifdef {closing}"
    path = _source(
        tmp_path,
        "#ifdef OPTIONAL_NAMESPACE\nnamespace OPTIONAL_NAMESPACE {\n#endif\n"
        f"void helper(void) {{}}\n{closing_guard}\n}}\n#endif\n",
    )
    source = path.read_bytes()
    parser = Parser(get_language("c"))
    original = parser.parse(source)
    masked, tree = _recovery._unwrap_cpp_namespace(source, original, parser)
    assert masked == source
    assert tree is original


def test_namespace_type_name_is_still_a_c_function(tmp_path: Path) -> None:
    path = _source(
        tmp_path, "typedef int namespace;\nnamespace action(void) { return 1; }\nint broken = ;"
    )
    graph = parse_file(str(path), "c")
    assert graph.nodes["sample:action"].kind == NodeKind.FUNCTION


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_cpp_namespace_masks_exact_tokens_and_preserves_positions(newline: str) -> None:
    source = newline.join(
        [
            "#ifdef MONOCYPHER_CPP_NAMESPACE",
            "namespace MONOCYPHER_CPP_NAMESPACE {",
            "#endif",
            "int action(void) { return 7; }",
            "#ifdef MONOCYPHER_CPP_NAMESPACE",
            "}",
            "#endif",
            "",
        ]
    ).encode()
    parser = Parser(get_language("c"))
    original = parser.parse(source)
    masked, tree = _recovery._unwrap_cpp_namespace(source, original, parser)
    opening = source.index(b"namespace")
    brace = source.index(b"{", opening)
    closing = source.rindex(b"}")
    expected = bytearray(source)
    expected[opening : brace + 1] = b" " * (brace + 1 - opening)
    expected[closing] = 32
    assert masked == bytes(expected)
    assert len(masked) == len(source)
    assert not tree.root_node.has_error
    assert b"int action(void) { return 7; }" in masked


def test_multiple_namespace_wrappers_preserve_function_text(tmp_path: Path) -> None:
    def wrapped(body: str) -> str:
        return (
            "#ifdef WRAPPER\nnamespace WRAPPER {\n#endif\n" + body + "\n#ifdef WRAPPER\n}\n#endif\n"
        )

    path = _source(
        tmp_path, wrapped("void helper(void) {}") + wrapped("void action(void) { helper(); }")
    )
    graph = parse_file(str(path), "c")
    assert {n.name for n in graph.nodes.values() if n.kind == NodeKind.FUNCTION} == {
        "helper",
        "action",
    }
    assert "sample:helper" in {e.target_id for e in _calls(graph, "action")}


def test_namespace_header_does_not_invent_a_function(tmp_path: Path) -> None:
    path = tmp_path / "sample.h"
    path.write_text(
        "#ifdef WRAPPER\nnamespace WRAPPER {\n#endif\nint action(void);\n"
        "#ifdef WRAPPER\n}\n#endif\n"
    )
    graph = parse_file(str(path), "c")
    assert not any(n.kind == NodeKind.FUNCTION for n in graph.nodes.values())


def test_alternative_cpp_linkage_arms_are_not_guessed() -> None:
    source = (
        b'#ifdef WRAPPER\nnamespace WRAPPER {\n#elif defined(__cplusplus)\nextern "C" {\n#endif\n'
        b"int action(void);\n#ifdef WRAPPER\n}\n#elif defined(__cplusplus)\n}\n#endif\n"
    )
    parser = Parser(get_language("c"))
    original = parser.parse(source)
    masked, tree = _recovery._unwrap_cpp_namespace(source, original, parser)
    assert masked == source and tree is original


def test_empty_guarded_cpp_namespace_is_not_a_function(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        "#ifdef OPTIONAL_NAMESPACE\nnamespace OPTIONAL_NAMESPACE {\n#endif\n"
        "#ifdef OPTIONAL_NAMESPACE\n}\n#endif\n",
    )
    graph = parse_file(str(path), "c")
    assert not any(n.kind == NodeKind.FUNCTION for n in graph.nodes.values())


def test_guarded_c_function_keeps_its_body(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        "void helper(void) {}\n"
        "#ifdef OPTIONAL_NAMESPACE\nvoid action(void) {\n#endif\n"
        "helper();\n#ifdef OPTIONAL_NAMESPACE\n}\n#endif\n",
    )
    graph = parse_file(str(path), "c")
    assert graph.nodes["sample:action"].location.start_line == 3
    assert "sample:helper" in {e.target_id for e in _calls(graph, "action")}


def test_unresolved_wrapper_keeps_same_file_targets(tmp_path: Path) -> None:
    path = _source(
        tmp_path, "#define DECL(n) DECL(n)\nvoid helper(void) {}\nDECL(left) { helper(); }"
    )
    graph = parse_file(str(path), "c")
    (edge,) = _calls(graph, "DECL(left)")
    assert edge.target_id == "sample:helper"
    assert edge.confidence == EdgeConfidence.CERTAIN
    assert dict(edge.attributes)["macro_signature_unresolved"]
    assert not dict(edge.attributes).get("binding_blocked")


def test_unresolved_wrapper_links_across_files(tmp_path: Path) -> None:
    _source(tmp_path, "UNAVAILABLE(left) { helper(); }")
    (tmp_path / "peer.c").write_text("void helper(void) {}")
    graph = parse_directory(str(tmp_path), "c")
    (edge,) = _calls(graph, "UNAVAILABLE(left)")
    assert edge.target_id == "peer:helper"
    assert edge.confidence == EdgeConfidence.INFERRED
    assert dict(edge.attributes)["macro_signature_unresolved"]


def test_syntax_error_preserves_unaffected_call_confidence(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        "void helper(void) {} void other(void) {}\n"
        "void action(void) { helper(); int x = 1 +; other(); }",
    )
    graph = parse_file(str(path), "c")
    assert dict(graph.nodes["sample:action"].attributes)["parse_incomplete"]
    assert {edge.target_id for edge in _calls(graph, "action")} == {"sample:helper", "sample:other"}
    assert all(edge.confidence == EdgeConfidence.CERTAIN for edge in _calls(graph, "action"))
    assert [d.kind for d in graph.parse_diagnostics] == ["syntax_error"]


@pytest.mark.parametrize("broken", ["return helper(,);", "other() helper();"])
def test_only_calls_inside_broken_syntax_get_context_marker(tmp_path: Path, broken: str) -> None:
    path = _source(
        tmp_path, f"void helper(void) {{}} void other(void) {{}}\nvoid action(void) {{ {broken} }}"
    )
    graph = parse_file(str(path), "c")
    edges = _calls(graph, "action")
    broken_edges = [e for e in edges if dict(e.attributes).get("syntax_error_context")]
    assert len(broken_edges) == 1
    assert broken_edges[0].confidence == EdgeConfidence.UNCERTAIN
    assert all(e.confidence == EdgeConfidence.CERTAIN for e in edges if e not in broken_edges)


@pytest.mark.parametrize("kind", ["relative", "absolute", "symlink"])
def test_headers_outside_analysis_root_are_not_read(tmp_path: Path, kind: str) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.h"
    outside.write_text("#define DECL(n) void outside_only_ ## n(void)\n")
    includes = {"relative": "../outside.h", "absolute": str(outside), "symlink": "link.h"}
    (root / "link.h").symlink_to(outside)
    path = _source(root, f'#include "{includes[kind]}"\nDECL(first) {{ helper(); }}')
    read_bytes = Path.read_bytes
    reads = []

    def read(path: Path) -> bytes:
        reads.append(path)
        return read_bytes(path)

    with patch.object(Path, "read_bytes", read):
        graph = parse_directory(str(root), "c")
    assert "sample:DECL(first)" in graph.nodes
    assert not any("outside_only" in node_id for node_id in graph.nodes)
    assert outside not in reads
    assert "sample:DECL(first)" in parse_file(str(path), "c").nodes


def test_in_root_parent_header_is_cached_for_one_directory_parse(tmp_path: Path) -> None:
    include = tmp_path / "include"
    source = tmp_path / "source"
    include.mkdir()
    source.mkdir()
    header = include / "declarations.h"
    header.write_text("#define DECL(n) void wired_ ## n(void)\n")
    for name in ("first", "second"):
        (source / f"{name}.c").write_text(
            f'#include "../include/declarations.h"\nDECL({name}) {{}}'
        )
    reads = []
    read_bytes = Path.read_bytes

    def read(path: Path) -> bytes:
        if path == header:
            reads.append(path)
        return read_bytes(path)

    parser = CParser()
    with patch.object(Path, "read_bytes", read):
        graph = parser.parse_directory(str(tmp_path))
    assert {"source.first:wired_first", "source.second:wired_second"} <= graph.nodes.keys()
    assert reads == [header]
    header.write_text("#define DECL(n) void changed_ ## n(void)\n")
    assert "source.first:changed_first" in parser.parse_directory(str(tmp_path)).nodes
    assert "first:DECL(first)" in parse_file(str(source / "first.c"), "c").nodes


def test_object_macro_aliases_expand_to_verified_compiled_name(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        "#define DECL ALIAS\n#define ALIAS FINAL\n"
        "#define FINAL(n) void wired_ ## n(void)\nDECL(first) {}",
    )
    graph = parse_file(str(path), "c")
    assert "sample:wired_first" in graph.nodes
    assert dict(graph.nodes["sample:wired_first"].attributes)["macro_wrapper"] == "DECL"


def test_unavailable_wrapper_argument_does_not_create_a_false_name_match(tmp_path: Path) -> None:
    _source(tmp_path, "UNAVAILABLE(printf) { helper(); }")
    (tmp_path / "caller.c").write_text('void main(void) { printf("hello"); }')
    graph = parse_directory(str(tmp_path), "c")
    assert "sample:UNAVAILABLE(printf)" in graph.nodes
    (edge,) = _calls(graph, "main")
    assert graph.nodes[edge.target_id].kind == NodeKind.PROXY


def test_callback_getter_return_value_keeps_its_own_indirect_call(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        "struct Ops { int (*(*get)(void))(void); };\n"
        "static int target(void) { return 0; }\n"
        "static int (*getter(void))(void) { return target; }\n"
        "static const struct Ops table = { .get = getter };\n"
        "void action(void) { table.get()(); }",
    )
    graph = parse_file(str(path), "c")
    edges = _calls(graph, "action")
    assert len(edges) == 2
    bound = [e for e in edges if e.target_id == "sample:getter"]
    assert len(bound) == 1
    assert bound[0].confidence == EdgeConfidence.INFERRED
    (outer,) = [e for e in edges if e not in bound]
    assert graph.nodes[outer.target_id].kind == NodeKind.PROXY
    assert outer.confidence == EdgeConfidence.UNCERTAIN
    assert outer.location is not None and bound[0].location is not None
    assert outer.location.end_col is not None and bound[0].location.end_col is not None
    assert outer.location.end_col > bound[0].location.end_col


def test_callback_binding_survives_an_unrelated_parse_error(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        "struct Ops { void (*run)(void); };\n"
        "static void helper(void) {}\n"
        "static const struct Ops table = { .run = helper };\n"
        "void action(void) { table.run(); int x = 1 +; } ",
    )
    graph = parse_file(str(path), "c")
    assert dict(graph.nodes["sample:action"].attributes)["parse_incomplete"]
    (edge,) = _calls(graph, "action")
    assert edge.target_id == "sample:helper"
    assert edge.confidence == EdgeConfidence.INFERRED


def test_brace_changing_conditionals_are_not_recovered(tmp_path: Path) -> None:

    source = BLOCKS.replace(
        " BLOCK_BEGIN { first();",
        "#if A\n if (a) {\n#else\n if (b) {\n#endif\n BLOCK_BEGIN { first();",
    )
    graph = parse_file(str(_source(tmp_path, source)), "c")
    assert not dict(graph.nodes["sample:action"].attributes).get("syntax_recovered")


def test_recovered_span_can_keep_an_unrelated_error(tmp_path: Path) -> None:

    source = BLOCKS.replace(" final_step();", " int broken = 1 +;\n final_step();")
    graph = parse_file(str(_source(tmp_path, source)), "c")
    unit = graph.nodes["sample:action"]
    assert unit.location.end_line == source.splitlines().index("} /* action ends */") + 1
    assert dict(unit.attributes)["syntax_recovered"]
    edges = _calls(graph, "action")
    assert {"sample:first", "sample:second", "sample:final_step"} <= {e.target_id for e in edges}
    assert all(not dict(e.attributes).get("syntax_error_context") for e in edges)
    assert any(d.kind == "syntax_error" for d in graph.parse_diagnostics)


def test_failed_span_candidate_does_not_cancel_an_independent_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    source = (BLOCKS + BLOCKS.replace("action", "other_action")).encode()
    parser = Parser(get_language("c"))
    original = parser.parse(source)
    tokens = [
        t for t in _recovery._TOKENS.finditer(source) if not t.group().startswith((b"//", b"/*"))
    ]
    functions = tuple(
        n
        for n in _recovery.walk_owned(
            original.root_node, boundaries=_recovery._DECLARATION_BOUNDARIES
        )
        if n.type == "function_definition"
    )
    candidates = _recovery._candidates(functions, tokens)
    assert len(candidates) == 2
    first, second = sorted(candidates)
    # Fault-inject one incorrect boundary to exercise rollback of only its masks.
    candidates[second] = replace(candidates[second], end=candidates[second].end - 1)
    monkeypatch.setattr(_recovery, "_candidates", lambda *_: candidates)
    recovered, starts = _recovery.recover_bodies(source, original, parser)
    assert starts == {first}
    assert recovered.root_node.text is not None
    assert recovered.root_node.text.count(b"BLOCK_BEGIN { first();") == 1


def test_inactive_prose_does_not_hide_physical_end(tmp_path: Path) -> None:
    source = BLOCKS.replace(
        " BLOCK_BEGIN { first();",
        "#if 0\nThis isn't C {\n#if INNER\n} } }\n#endif\n#endif\n BLOCK_BEGIN { first();",
    )
    graph = parse_file(str(_source(tmp_path, source)), "c")
    unit = graph.nodes["sample:action"]
    assert dict(unit.attributes).get("syntax_recovered")
    assert any(e.target_id == "sample:final_step" for e in _calls(graph, "action"))


def test_unrecoverable_function_does_not_prevent_neighbor_recovery(tmp_path: Path) -> None:
    bad = BLOCKS.replace("action", "bad").replace(
        " BLOCK_BEGIN { first();",
        "#if A\n if (a) {\n#else\n if (b) {\n#endif\n BLOCK_BEGIN { first();",
    )
    bad = bad[bad.index("void bad(void)") : bad.index("void after(void)")]
    graph = parse_file(str(_source(tmp_path, BLOCKS + bad)), "c")
    assert not dict(graph.nodes["sample:bad"].attributes).get("syntax_recovered")
    assert dict(graph.nodes["sample:action"].attributes).get("syntax_recovered")
    assert any(e.target_id == "sample:final_step" for e in _calls(graph, "action"))
