"""Standard enum typedefs must not swallow following Objective-C declarations."""

from pathlib import Path

import pytest

from trailmark.models.edges import EdgeKind
from trailmark.models.nodes import NodeKind
from trailmark.parse import parse_file
from trailmark.parsers.objc.parser import ObjCParser

_BODY = (
    "@interface Box\n- (void)run;\n- (void)helper;\n@end\n"
    "@implementation Box\n- (void)run { [self helper]; }\n- (void)helper {}\n@end\n"
)


@pytest.mark.parametrize("macro", ["NS_ENUM", "NS_OPTIONS"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_enum_macro_keeps_following_class_and_original_sites(
    tmp_path: Path, macro: str, newline: str
) -> None:
    path = tmp_path / "sample.m"
    source = (
        (
            f"// λ source\ntypedef {macro}(unsigned long, Mode) {{\n"
            "  First = 1 << 0, /* } ; */ Second = 1 << 1\n};\n" + _BODY
        )
        .replace("\n", newline)
        .encode()
    )
    path.write_bytes(source)
    graph = parse_file(str(path), "objc")
    enum = graph.nodes["sample:Mode"]
    assert enum.kind == NodeKind.ENUM
    assert (enum.location.start_line, enum.location.end_line) == (2, 4)
    assert dict(enum.attributes)["objc_enum_macro"] == macro
    assert graph.nodes["sample:Box"].location.start_line == 5
    (call,) = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert graph.nodes[call.source_id].name == "run"
    assert graph.nodes[call.target_id].name == "helper"
    assert call.location and (call.location.start_line, call.location.start_col) == (10, 14)
    assert graph.nodes["sample"].location.end_line == 13
    assert graph.parse_diagnostics  # The original unsupported syntax remains visible.
    assert path.read_bytes() == source


def test_multiple_enums_compose_with_nullability_ranges(tmp_path: Path) -> None:
    path = tmp_path / "sample.m"
    path.write_text(
        "NS_ASSUME_NONNULL_BEGIN\n"
        "typedef NS_ENUM(int, First) { A = 0 };\n"
        "typedef NS_OPTIONS(int, Second) { B = 1 };\n" + _BODY + "NS_ASSUME_NONNULL_END\n"
    )
    graph = parse_file(str(path), "objc")
    assert {n.name for n in graph.nodes.values() if n.kind == NodeKind.ENUM} == {"First", "Second"}
    assert graph.nodes["sample:Box"].location.start_line == 4
    assert dict(graph.nodes["sample"].attributes)["objc_nullability_regions"] == 1
    assert any(e.kind == EdgeKind.CALLS for e in graph.edges)


def test_enum_only_file_terminates_and_parser_ranges_do_not_leak(tmp_path: Path) -> None:
    path = tmp_path / "sample.m"
    parser = ObjCParser()
    path.write_text("typedef NS_ENUM(int, Mode) { First = 1 };")
    graph = parser.parse_file(str(path))
    assert graph.nodes["sample:Mode"].location.end_col == 41
    assert len([e for e in graph.edges if e.target_id == "sample:Mode"]) == 1
    path.write_text(_BODY)
    following = parser.parse_file(str(path))
    assert "sample:Mode" not in following.nodes
    assert any(e.kind == EdgeKind.CALLS for e in following.edges)


@pytest.mark.parametrize(
    "declaration",
    [
        "typedef NS_ENUM(int, Mode) { First = };\n",
        "typedef NS_ENUM(int, Mode) { First = 1 }\n",
        "typedef NS_ENUM(int, Mode) { First = 1;\n",
        "#define NS_ENUM(type, name) replacement\ntypedef NS_ENUM(int, Mode) { First };\n",
        "#undef NS_OPTIONS\ntypedef NS_ENUM(int, Mode) { First };\n",
        "void outer(void) { typedef NS_ENUM(int, Mode) { First }; }\n",
        "/* typedef NS_ENUM(int, Mode) { First }; */\n",
        'const char *text = "typedef NS_ENUM(int, Mode) { First };";\n',
    ],
)
def test_unproven_macro_declarations_do_not_create_recovered_enums(
    tmp_path: Path, declaration: str
) -> None:
    path = tmp_path / "sample.m"
    path.write_text(declaration + _BODY)
    graph = parse_file(str(path), "objc")
    assert not any(dict(n.attributes).get("objc_enum_macro") for n in graph.nodes.values())


def test_closed_conditional_enum_does_not_promote_nested_function_declarations(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sample.m"
    path.write_text(
        "#if FLAG\ntypedef NS_ENUM(int, Mode) { First };\n#endif\n"
        "void outer(void) { void nested(void) {} }\n" + _BODY
    )
    graph = parse_file(str(path), "objc")
    assert graph.nodes["sample:Mode"].kind == NodeKind.ENUM
    assert not any(n.name == "nested" for n in graph.nodes.values())
    assert graph.nodes["sample:Box"].location.start_line == 5


def test_recovery_limit_keeps_original_syntax_diagnostics(tmp_path: Path) -> None:
    path = tmp_path / "sample.m"
    path.write_text("".join(f"typedef NS_ENUM(int, Mode{i}) {{ Value{i} }};\n" for i in range(65)))
    graph = parse_file(str(path), "objc")
    recovered = [n for n in graph.nodes.values() if dict(n.attributes).get("objc_enum_macro")]
    assert len(recovered) == 64
    assert "sample:Mode64" not in {n.id for n in recovered}
    assert graph.parse_diagnostics
