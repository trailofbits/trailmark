"""Block macros do not split a physical member body into fake functions."""

import json
from pathlib import Path

import pytest
from tree_sitter import Parser
from tree_sitter_language_pack import get_language

from trailmark.models.edges import EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.parsers._common import add_module_node
from trailmark.parsers.cpp._block_macros import recover_block_bodies
from trailmark.parsers.cpp.parser import CppParser
from trailmark.query.api import QueryEngine

_HEADER = "#define TRY try\n#define CATCH(...) catch(__VA_ARGS__)\n"
_BODY = "void run() {\n TRY { first(); }\n CATCH(...) { second(); }\n last();\n}\n"


@pytest.mark.mutation_fast
def test_catch_and_following_calls_stay_in_the_physical_function(tmp_path: Path) -> None:
    path = tmp_path / "example.cpp"
    source = (_HEADER + "void first(){} void second(){} void last(){}\n" + _BODY).encode()
    path.write_bytes(source)
    graph = CppParser().parse_file(str(path))
    run = graph.nodes["example:run"]
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert {e.source_id for e in calls} == {run.id}
    assert {graph.nodes[e.target_id].name for e in calls} == {"first", "second", "last"}
    assert run.location.end_line == 8
    assert not any(u.name in {"TRY", "CATCH"} for u in graph.nodes.values())
    attrs = dict(run.attributes)
    assert attrs["cpp_body_recovered"] is True
    spans = json.loads(str(attrs["cpp_block_macro_spans"]))
    assert [m["name"] for m in spans] == ["TRY", "CATCH"]
    assert [source[m["start_byte"] : m["end_byte"]] for m in spans] == [b"TRY", b"CATCH(...)"]
    assert path.read_bytes() == source
    assert {n["id"] for n in QueryEngine.from_graph(graph).callers_of("example:second")} == {run.id}


@pytest.mark.mutation_fast
def test_all_original_diagnostics_remain_available(tmp_path: Path) -> None:
    path = tmp_path / "example.cpp"
    source = ("int bad = ;\n" + _HEADER + _BODY).encode()
    path.write_bytes(source)
    original = Parser(get_language("cpp")).parse(source)
    before = CodeGraph(language="cpp", root_path=str(path))
    add_module_node(original.root_node, str(path), "example", before)
    graph = CppParser().parse_file(str(path))
    assert before.parse_diagnostics and set(before.parse_diagnostics) <= set(
        graph.parse_diagnostics
    )
    assert dict(graph.nodes["example:run"].attributes)["cpp_body_recovered"] is True


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    "trivia",
    [
        'const char* text = "} CATCH(...) { lost(); }";',
        'const char* text = R"tag(}\n#if BAD\nCATCH(...) { lost(); }\n#endif\n)tag";',
        "char brace = '}'; // } CATCH(...) { lost(); }",
        "/* } CATCH(...) { lost(); } */",
    ],
)
def test_literals_comments_and_raw_directives_do_not_change_brace_ownership(
    tmp_path: Path, trivia: str
) -> None:
    path = tmp_path / "example.cpp"
    path.write_text(
        _HEADER + "void run() {\n" + trivia + "\n TRY { first(); }\n"
        " CATCH(...) { second(); }\n last();\n}\n"
    )
    graph = CppParser().parse_file(str(path))
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert {e.source_id for e in calls} == {"example:run"}
    assert (
        len(calls) == 3
        and dict(graph.nodes["example:run"].attributes)["cpp_body_recovered"] is True
    )


@pytest.mark.mutation_fast
def test_balanced_preprocessor_arms_are_retained_as_source_sites(tmp_path: Path) -> None:
    path = tmp_path / "example.cpp"
    path.write_text(
        _HEADER + "void run() {\n#if MODE\n TRY { first(); }\n#else\n"
        " TRY { other(); }\n#endif\n CATCH(...) { second(); }\n}\n"
    )
    graph = CppParser().parse_file(str(path))
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert {e.source_id for e in calls} == {"example:run"}
    assert len(calls) == 3


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    "source",
    [
        "struct TYPE { TYPE(int); }; void run() { TYPE{first()}; }",
        "void run() { TRY { first(); } CATCH(make()) { second(); } }",
        "void run() { TRY { first(); } catch_macro(...) { second(); } }",
        "void run() { TRY { first(); } CATCH(...) { second(); }\n#if MODE\n}\n#else\n}\n#endif",
        'void run() { TRY { first(); } CATCH(...) { second(); } const char* s = "unterminated; }',
    ],
)
def test_unproved_or_expression_bearing_wrappers_do_not_recover(source: str) -> None:
    parser = Parser(get_language("cpp"))
    data = source.encode()
    original = parser.parse(data)
    recovered, bodies = recover_block_bodies(parser, data, original)
    assert recovered is original and not bodies


@pytest.mark.mutation_fast
def test_empty_catch_body_has_no_lost_argument_expression(tmp_path: Path) -> None:
    path = tmp_path / "example.cpp"
    path.write_text(_HEADER + "void run() { TRY { first(); } CATCH(...) {} last(); }")
    graph = CppParser().parse_file(str(path))
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 2 and all(e.source_id == "example:run" for e in calls)
    assert dict(graph.nodes["example:run"].attributes)["cpp_body_recovered"] is True


@pytest.mark.mutation_fast
def test_class_export_and_body_ranges_compose_without_changing_next_file(tmp_path: Path) -> None:
    path = tmp_path / "example.hpp"
    path.write_text(
        "#define API\n" + _HEADER + "class API Box { public: void plain() {} };\n"
        "void run() { TRY { first(); } CATCH(...) { second(); } last(); }"
    )
    parser = CppParser()
    graph = parser.parse_file(str(path))
    assert dict(graph.nodes["example:Box"].attributes)["cpp_export_marker"] == "API"
    run = next(u for u in graph.nodes.values() if u.name == "run")
    assert dict(run.attributes)["cpp_body_recovered"] is True
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 3 and all(e.source_id == run.id for e in calls)
    other = tmp_path / "other.cpp"
    other.write_text("void plain() {}")
    assert not parser.parse_file(str(other)).parse_diagnostics


def test_comment_prefixed_directive_fails_closed_for_unmodeled_arms() -> None:
    source = (
        b"void run() { TRY { first(); } CATCH(...) { second(); }\n"
        b"/*prefix*/#if MODE\n}\n#else\n}\n#endif"
    )
    parser = Parser(get_language("cpp"))
    original = parser.parse(source)
    recovered, bodies = recover_block_bodies(parser, source, original)
    assert recovered is original and not bodies


@pytest.mark.mutation_fast
def test_nested_lambda_and_local_class_keep_their_own_call_sites(tmp_path: Path) -> None:
    path = tmp_path / "example.cpp"
    path.write_text(
        _HEADER + "void run() { TRY { auto callback = [] { inside(); }; "
        "struct Local { void work() { local(); } }; outside(); } "
        "CATCH(...) { failed(); } }"
    )
    graph = CppParser().parse_file(str(path))
    calls = {
        dict(e.attributes).get("bare_name"): e.source_id
        for e in graph.edges
        if e.kind == EdgeKind.CALLS and not dict(e.attributes).get("deferred")
    }
    assert "$lambda@" in calls["inside"]
    assert ".Local.work" in calls["local"]
    assert calls["outside"] == calls["failed"] == "example:run"
