"""Literal namespace/class ownership survives bounded declaration annotations."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.parse_work import parse_work
from trailmark.models.edges import EdgeKind
from trailmark.models.nodes import NodeKind
from trailmark.parse import parse_file
from trailmark.query.api import QueryEngine


def test_adjacent_comments_before_a_template_do_not_repeat_the_scan(tmp_path: Path) -> None:
    path = tmp_path / "sample.cpp"
    path.write_text("#define API\nAPI\n/**//**/\ntemplate<class T> class View {};\n")
    graph, _ = parse_work(path, language="cpp", max_work=100_000)
    assert graph.nodes["sample:View"].kind == NodeKind.CLASS
    markers = json.loads(str(dict(graph.nodes["sample"].attributes)["cpp_declaration_markers"]))
    assert [marker["role"] for marker in markers] == ["template_prefix"]


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
        "#define CTOR constexpr\n#define CT\\\nOR int\n",
        "#define CTOR constexpr\n#define CT\\\nOR(x) x\n",
        "#define CTOR constexpr\n#/**/define CTOR consteval\n",
        "#define CTOR constexpr\n# /* x */ define CTOR int\n",
        "#define CTOR constexpr\n#/**/undef CTOR\n",
        "#define CTOR constexpr\n#/*one\ntwo*/define CTOR consteval\n",
        "#define CTOR constexpr\n#\v/**/define CTOR consteval\n",
        "#define CTOR constexpr\n#\f/**/define CTOR consteval\n",
        "#define CTOR constexpr\n#/\\\n*one*/define CTOR consteval\n",
        "#define CTOR constexpr\n%:define CTOR consteval\n",
        "#define CTOR constexpr\n??=define CTOR consteval\n",
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


def test_literal_and_comment_directives_do_not_revoke_constructor_annotation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sample.cpp"
    path.write_text(
        '#define CTOR constexpr\nconst char* s = R"(\n#define CTOR int\n)";\n'
        "/*\n#undef CTOR\n*/\nclass Buffer { CTOR explicit Buffer() {} };\n"
    )
    graph = parse_file(str(path), "cpp")
    markers = json.loads(str(dict(graph.nodes["sample"].attributes)["cpp_declaration_markers"]))
    assert [m["role"] for m in markers] == ["constructor_annotation"]


@pytest.mark.parametrize("initializer", ["", ": args{{0}, values...} "])
def test_proven_annotation_chain_preserves_complete_namespace_and_class(
    tmp_path: Path, initializer: str
) -> None:
    path = tmp_path / "sample.cpp"
    path.write_text(
        "#define API\n#define CTOR constexpr\n"
        "#if FLAG\n#define INLINE inline __attribute__((always_inline))\n"
        "#else\n#define INLINE inline\n#endif\n"
        "API\nnamespace detail {\nvoid helper() {}\n"
        "template<class T> struct Store {\n int args[1];\n"
        " template<class... U> CTOR INLINE Store(U&... values) " + initializer + "{ helper(); }\n"
        " void after() { helper(); }\n};\n"
        "void tail() { helper(); }\n}\nvoid outside() {}\n"
    )
    graph = parse_file(str(path), "cpp")
    assert graph.nodes["sample:detail.Store"].kind == NodeKind.STRUCT
    assert graph.nodes["sample:detail.tail"].kind == NodeKind.FUNCTION
    assert "sample:outside" in graph.nodes
    assert "sample:detail.outside" not in graph.nodes
    sources = {e.source_id for e in graph.edges if e.kind == EdgeKind.CALLS}
    assert "sample:detail.Store.after" in sources
    assert "sample:detail.tail" in sources
    assert any(graph.nodes[key].name == "Store" for key in sources)
    markers = json.loads(str(dict(graph.nodes["sample"].attributes)["cpp_declaration_markers"]))
    assert {m["name"] for m in markers} == {"API", "CTOR", "INLINE"}


def test_unproven_annotation_in_chain_is_not_removed(tmp_path: Path) -> None:
    path = tmp_path / "sample.cpp"
    path.write_text(
        "#define CTOR constexpr\n#define INLINE mystery\nstruct Store { CTOR INLINE Store() {} };\n"
    )
    graph = parse_file(str(path), "cpp")
    assert "cpp_declaration_markers" not in dict(graph.nodes["sample"].attributes)


def test_standalone_template_prefix_preserves_method_owner(tmp_path: Path) -> None:
    path = tmp_path / "sample.cpp"
    path.write_text(
        "#define API\nAPI\n/** public view */\n"
        "template<class T> class View { public:\n"
        " const T* data() { return nullptr; }\n};\nvoid outside() {}\n"
    )
    graph = parse_file(str(path), "cpp")
    assert graph.nodes["sample:View"].kind == NodeKind.CLASS
    assert graph.nodes["sample:View.data"].kind == NodeKind.METHOD
    assert "sample:data" not in graph.nodes
    assert "sample:outside" in graph.nodes
    markers = json.loads(str(dict(graph.nodes["sample"].attributes)["cpp_declaration_markers"]))
    assert [m["role"] for m in markers] == ["template_prefix"]


@pytest.mark.parametrize(
    "suffix",
    [
        "int compute() { return 0; }\n",
        "/* unterminated template<class T> class View {};",
        "// template<class T> class View {};",
    ],
)
def test_repeated_comments_without_template_do_not_stall(tmp_path: Path, suffix: str) -> None:
    path = tmp_path / "sample.cpp"
    path.write_text("#define API\nAPI\n" + "/**/" * 40 + "\n" + suffix)
    # Isolate the native parser while checking the adversarial input's graph.
    subprocess.run(  # noqa: S603 -- Fixed interpreter and code; only a test path varies.
        [
            sys.executable,
            "-c",
            "import sys; from trailmark.parse import parse_file; "
            "graph = parse_file(sys.argv[1], 'cpp'); "
            "assert 'cpp_declaration_markers' not in dict(graph.nodes['sample'].attributes); "
            "assert 'sample:View' not in graph.nodes",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def test_template_prefix_skips_mixed_comments_and_whitespace(tmp_path: Path) -> None:
    path = tmp_path / "sample.cpp"
    path.write_text(
        "#define API\nAPI\n"
        "/* first */\t// template<class T> class Decoy {};\r\n"
        "/* second */\v\f template<class T> class View { void work() {} };\n"
    )
    graph = parse_file(str(path), "cpp")
    assert graph.nodes["sample:View"].kind == NodeKind.CLASS
    assert graph.nodes["sample:View.work"].kind == NodeKind.METHOD
    assert "sample:Decoy" not in graph.nodes
    markers = json.loads(str(dict(graph.nodes["sample"].attributes)["cpp_declaration_markers"]))
    assert [m["role"] for m in markers] == ["template_prefix"]
