"""Class export annotations retain original source spans and diagnostics."""

from pathlib import Path

import pytest
from tree_sitter import Parser
from tree_sitter_language_pack import get_language

from trailmark.models.edges import EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind
from trailmark.parsers._common import add_module_node
from trailmark.parsers.cpp.parser import CppParser
from trailmark.query.api import QueryEngine


@pytest.mark.parametrize("access", ["public", "private", "protected"])
def test_export_marked_class_recovers_method_navigation(tmp_path: Path, access: str) -> None:
    path = tmp_path / "example.hpp"
    source = (
        f"#define API\nclass API File {{ {access}:\n"
        " void close() {}\n void run() { close(); }\n};\n"
    ).encode()
    path.write_bytes(source)
    graph = CppParser().parse_file(str(path))
    unit = next(u for u in graph.nodes.values() if u.kind == NodeKind.CLASS and u.name == "File")
    attrs = dict(unit.attributes)
    assert attrs["cpp_export_marker"] == "API"
    assert isinstance(attrs["cpp_export_marker_start_byte"], int)
    assert isinstance(attrs["cpp_export_marker_end_byte"], int)
    assert (
        source[attrs["cpp_export_marker_start_byte"] : attrs["cpp_export_marker_end_byte"]]
        == b"API"
    )
    assert unit.location.start_line == 2 and unit.location.start_col == 0
    assert unit.location.end_line == 5 and unit.location.end_col == 1
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)
    assert graph.nodes[call.source_id].name == "run" and graph.nodes[call.target_id].name == "close"
    assert call.location is not None and call.location.start_line == 4
    assert call.location.start_col == 14
    assert any(
        n["id"] == call.source_id for n in QueryEngine.from_graph(graph).callers_of(call.target_id)
    )
    assert path.read_bytes() == source


def test_namespace_declaration_preserves_original_offsets(tmp_path: Path) -> None:
    path = tmp_path / "example.hpp"
    source = "// π\n#define API\nnamespace N {\nclass API First { public: void a() {} };\n}\n"
    path.write_text(source)
    graph = CppParser().parse_file(str(path))
    classes = {u.name: u for u in graph.nodes.values() if u.kind == NodeKind.CLASS}
    assert set(classes) == {"First"}
    unit = classes["First"]
    assert unit.id == "example:N.First" and unit.location.start_line == 4
    attrs = dict(unit.attributes)
    assert isinstance(attrs["cpp_export_marker_start_byte"], int)
    assert isinstance(attrs["cpp_export_marker_end_byte"], int)
    assert (
        source.encode()[attrs["cpp_export_marker_start_byte"] : attrs["cpp_export_marker_end_byte"]]
        == b"API"
    )


def test_original_diagnostics_survive_including_unrelated_syntax_error(tmp_path: Path) -> None:
    path = tmp_path / "example.hpp"
    source = b"int bad = ;\n#define API\nclass API File { public: void close() {} };\n"
    path.write_bytes(source)
    raw = Parser(get_language("cpp")).parse(source)
    original = CodeGraph(language="cpp", root_path=str(path))
    add_module_node(raw.root_node, str(path), "example", original)
    assert original.parse_diagnostics
    graph = CppParser().parse_file(str(path))
    assert set(original.parse_diagnostics) <= set(graph.parse_diagnostics)
    assert any(u.kind == NodeKind.CLASS and u.name == "File" for u in graph.nodes.values())


@pytest.mark.parametrize(
    "source",
    [
        "struct API { int member; }; class API object{1};",
        "// class API Fake { public: void work() {} };\nvoid run() {}",
        'const char* value = "class API Fake { public: void work() {} };";',
        "class Api File { public: void work() {} };",
        "class API File { void work() {} };",
    ],
)
def test_unproved_shapes_are_not_recovered(tmp_path: Path, source: str) -> None:
    path = tmp_path / "example.hpp"
    path.write_text(source)
    graph = CppParser().parse_file(str(path))
    assert not any(dict(u.attributes).get("cpp_export_marker") for u in graph.nodes.values())
    assert not any(
        u.kind == NodeKind.CLASS and u.name in {"object", "Fake", "File"}
        for u in graph.nodes.values()
    )


def test_recovery_does_not_change_the_parsers_next_file(tmp_path: Path) -> None:
    first, second = tmp_path / "first.hpp", tmp_path / "second.hpp"
    first.write_text("class API File { public: void work() {} };")
    second.write_text("void ordinary() {}")
    parser = CppParser()
    assert any(
        dict(u.attributes).get("cpp_export_marker")
        for u in parser.parse_file(str(first)).nodes.values()
    )
    graph = parser.parse_file(str(second))
    assert any(u.name == "ordinary" for u in graph.nodes.values())
    assert not graph.parse_diagnostics
