"""Export prefixes preserve C declaration identity and source call linkage."""

from pathlib import Path

import pytest

from trailmark.models import CodeGraph, EdgeKind, NodeKind
from trailmark.parsers.c.parser import CParser

_DECLARATIONS = "#define PHPAPI\ntypedef int zend_result;\n"
_IMPLEMENTATION = """\
void sink(void) {}

PHPAPI zend_result php_hash_environment(void)
{
    sink();
    return 0;
}
"""
_CALLER = """\
void startup_environment(void)
{
    php_hash_environment();
}
"""


def _assert_environment_links(graph: CodeGraph) -> None:
    functions = [node for node in graph.nodes.values() if node.kind == NodeKind.FUNCTION]
    (target,) = [node for node in functions if node.name == "php_hash_environment"]
    (caller,) = [node for node in functions if node.name == "startup_environment"]
    (sink,) = [node for node in functions if node.name == "sink"]
    calls = {
        (edge.source_id, edge.target_id) for edge in graph.edges if edge.kind == EdgeKind.CALLS
    }
    assert (caller.id, target.id) in calls
    assert (target.id, sink.id) in calls
    attributes = dict(target.attributes)
    assert not attributes.get("macro_signature_unresolved")
    assert "macro_wrapper" not in attributes
    assert attributes.get("source_alias") != "void"
    assert target.return_type is None or target.return_type.name != target.name


@pytest.mark.parametrize("declarations", ["plain", "inline", "header"])
def test_visibility_prefix_preserves_function_and_body(tmp_path: Path, declarations: str) -> None:
    if declarations == "header":
        (tmp_path / "api.h").write_text(_DECLARATIONS)
        prefix = '#include "api.h"\n'
    else:
        prefix = _DECLARATIONS
    source = prefix + _IMPLEMENTATION + _CALLER
    if declarations == "plain":
        source = source.replace("PHPAPI ", "")
    path = tmp_path / "environment.c"
    path.write_text(source)
    graph = CParser().parse_file(str(path))
    _assert_environment_links(graph)
    target = graph.nodes["environment:php_hash_environment"]
    declaration_line = next(
        i
        for i, line in enumerate(source.splitlines(), 1)
        if "zend_result php_hash_environment" in line
    )
    assert target.location.start_line == declaration_line
    assert target.location.end_line == declaration_line + 4
    assert bool(graph.parse_diagnostics) == (declarations != "plain")
    assert all(d.location.file_path == str(path) for d in graph.parse_diagnostics)


def test_visibility_prefix_links_from_another_file(tmp_path: Path) -> None:
    (tmp_path / "api.h").write_text(_DECLARATIONS)
    (tmp_path / "environment.c").write_text('#include "api.h"\n' + _IMPLEMENTATION)
    (tmp_path / "startup.c").write_text(
        '#include "api.h"\nzend_result php_hash_environment(void);\n' + _CALLER
    )
    graph = CParser().parse_directory(str(tmp_path))
    _assert_environment_links(graph)
    assert graph.nodes["environment:php_hash_environment"].location.file_path == str(
        tmp_path / "environment.c"
    )


@pytest.mark.parametrize("alias", [False, True])
def test_genuine_wrapper_with_type_named_argument(tmp_path: Path, alias: bool) -> None:
    definition = "#define DECL(n) void wired_ ## n(void)\n"
    if alias:
        definition = "#define DECL REAL_DECL\n#define REAL_DECL(n) void wired_ ## n(void)\n"
    path = tmp_path / "wrapper.c"
    path.write_text(definition + "DECL(void) {}\nvoid caller(void) { wired_void(); }")
    graph = CParser().parse_file(str(path))
    target = graph.nodes["wrapper:wired_void"]
    assert dict(target.attributes)["macro_wrapper"] == "DECL"
    assert not dict(target.attributes).get("macro_signature_unresolved")
    assert any(
        edge.source_id == "wrapper:caller" and edge.target_id == target.id
        for edge in graph.edges
        if edge.kind == EdgeKind.CALLS
    )


@pytest.mark.parametrize("argument", ["action", "void"])
@pytest.mark.parametrize("conditional", [False, True])
def test_ambiguous_wrapper_keeps_uncertain_signature(
    tmp_path: Path, argument: str, conditional: bool
) -> None:
    definitions = "#define DECL(n) void first_ ## n(void)\n"
    if conditional:
        definitions = "#if CONFIG\n" + definitions + "#else\n"
    else:
        definitions += "#undef DECL\n"
    definitions += "#define DECL(n) void second_ ## n(void)\n"
    if conditional:
        definitions += "#endif\n"
    path = tmp_path / "wrapper.c"
    path.write_text(definitions + f"void sink(void) {{}}\nDECL({argument}) {{ sink(); }}")
    graph = CParser().parse_file(str(path))
    target = graph.nodes[f"wrapper:DECL({argument})"]
    assert dict(target.attributes)["macro_signature_unresolved"]
    assert not any(node.name.startswith(("first_", "second_")) for node in graph.nodes.values())
    assert any(d.kind == "unsupported_syntax" for d in graph.parse_diagnostics)
    assert any(
        edge.source_id == target.id and edge.target_id == "wrapper:sink"
        for edge in graph.edges
        if edge.kind == EdgeKind.CALLS
    )
