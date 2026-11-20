"""Literal namespace/class ownership survives bounded declaration annotations."""

import json
from pathlib import Path

import pytest

from trailmark.models.edges import EdgeKind
from trailmark.models.nodes import NodeKind
from trailmark.parse import parse_file
from trailmark.query.api import QueryEngine


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_namespace_and_constexpr_constructor_owners_keep_physical_sites(
    tmp_path: Path, newline: str
) -> None:
    path = tmp_path / "sample.cpp"
    source = newline.join(
        [
            "// λ decoy API namespace fake { }",
            "#define API",
            "#define CTOR constexpr",
            "API",
            "namespace detail {",
            "void helper() {}",
            "class Buffer { public:",
            " CTOR explicit Buffer() : n_{0} { helper(); }",
            " CTOR ~Buffer() {}",
            " int n_;",
            "};",
            "}",
            "void outside() {}",
            "",
        ]
    )
    path.write_bytes(source.encode())
    graph = parse_file(str(path), "cpp")
    assert graph.nodes["sample:detail"].kind == NodeKind.NAMESPACE
    assert graph.nodes["sample:detail.Buffer"].kind == NodeKind.CLASS
    assert graph.nodes["sample:outside"].kind == NodeKind.FUNCTION
    owners = {e.target_id: e.source_id for e in graph.edges if e.kind == EdgeKind.CONTAINS}
    assert owners["sample:detail.Buffer"] == "sample:detail"
    (call,) = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert graph.nodes[call.source_id].name == "Buffer"
    assert owners[call.source_id] == "sample:detail.Buffer"
    assert call.target_id == "sample:detail.helper"
    assert call.location is not None
    assert (call.location.start_line, call.location.start_col) == (8, 34)
    assert QueryEngine.from_graph(graph).paths_between(call.source_id, call.target_id)
    markers = json.loads(str(dict(graph.nodes["sample"].attributes)["cpp_declaration_markers"]))
    assert {m["role"] for m in markers} == {"namespace_prefix", "constructor_annotation"}
    assert len(markers) == 3
    assert all(
        source.encode()[m["start_byte"] : m["end_byte"]].decode() == m["name"] for m in markers
    )
    assert graph.parse_diagnostics  # Original recovery evidence remains visible.


@pytest.mark.parametrize(
    "definitions",
    [
        "",
        "typedef int CTOR;",
        "#define CTOR static\n",
        "#define CTOR constexpr\n#undef CTOR\n",
        "#define CTOR constexpr\n#undef /* stop */ CTOR\n",
        "#define CTOR constexpr\n#undef/**/CTOR\n",
        "#define CTOR constexpr\n#un\\\ndef CTOR\n",
        "#define CTOR constexpr\n#un\\\ndef/**/CTOR\n",
        "#define CTOR constexpr\n#undef CT\\\nOR\n",
        "#define CTOR constexpr\n#define CTOR int\n",
    ],
)
def test_unknown_or_conflicting_constructor_markers_are_not_removed(
    tmp_path: Path, definitions: str
) -> None:
    path = tmp_path / "sample.cpp"
    path.write_text(definitions + "\nclass Buffer { CTOR explicit Buffer() {} };\n")
    graph = parse_file(str(path), "cpp")
    assert "cpp_declaration_markers" not in dict(graph.nodes["sample"].attributes)


def test_regular_types_free_functions_and_namespace_aliases_are_not_annotations(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sample.cpp"
    path.write_text(
        "#define CTOR constexpr\nstruct Result {};\n"
        "CTOR Result Buffer() { return {}; }\n"
        "namespace detail {}\nnamespace alias = detail;\n"
    )
    graph = parse_file(str(path), "cpp")
    assert "cpp_declaration_markers" not in dict(graph.nodes["sample"].attributes)
    assert graph.nodes["sample:Buffer"].kind == NodeKind.FUNCTION


def test_multiple_reopened_namespaces_do_not_capture_following_declarations(tmp_path: Path) -> None:
    path = tmp_path / "sample.cpp"
    path.write_text(
        "#define API\nAPI\nnamespace detail { struct A {}; }\n"
        "API\nnamespace detail { struct B {}; }\nstruct Outside {};\n"
    )
    graph = parse_file(str(path), "cpp")
    assert {"sample:detail.A", "sample:detail.B", "sample:Outside"} <= graph.nodes.keys()
    assert "sample:detail.Outside" not in graph.nodes


def test_comments_and_strings_cannot_create_declaration_markers(tmp_path: Path) -> None:
    path = tmp_path / "sample.cpp"
    path.write_text(
        '#define API\nconst char* s = "API\\nnamespace fake {";\n'
        "// API\n// namespace fake {\nnamespace actual { struct A {}; }\n"
    )
    graph = parse_file(str(path), "cpp", strict=True)
    assert "sample:actual.A" in graph.nodes
    assert "cpp_declaration_markers" not in dict(graph.nodes["sample"].attributes)
