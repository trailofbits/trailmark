"""C++ directory scopes include conventional .h headers exactly once."""

from pathlib import Path
from typing import cast

import pytest

from trailmark.models.nodes import NodeKind
from trailmark.parse import detect_languages, parse_directory, parse_roots
from trailmark.parsers.cpp import CppParser
from trailmark.query.api import QueryEngine
from trailmark.source_policy import SourcePolicy, SourceRoot

HEADER = "class Widget { public: void helper() {} void run() { helper(); } };\n"


def test_explicit_cpp_directory_and_parser_include_h_headers(tmp_path: Path) -> None:
    (tmp_path / "widget.h").write_text(HEADER)
    for graph in (
        parse_directory(str(tmp_path), "cpp"),
        CppParser().parse_directory(str(tmp_path)),
    ):
        assert graph.source_file_counts == {"cpp": 1}
        assert graph.nodes["widget:Widget"].kind == NodeKind.CLASS
        engine = QueryEngine.from_graph(graph)
        assert {n["id"] for n in engine.callees_of("widget:Widget.run")} == {"widget:Widget.helper"}


@pytest.mark.parametrize("language", ["c,cpp", "cpp,c", "auto"])
def test_cpp_owns_h_in_mixed_c_cpp_scopes(tmp_path: Path, language: str) -> None:
    (tmp_path / "widget.h").write_text(HEADER)
    (tmp_path / "main.cpp").write_text("int main() { return 0; }\n")
    (tmp_path / "plain.c").write_text("int plain(void) { return 0; }\n")
    graph = parse_directory(str(tmp_path), language)
    assert graph.source_file_counts == {"c": 1, "cpp": 2}
    assert graph.nodes["widget:Widget"].kind == NodeKind.CLASS
    assert not graph.parse_diagnostics


def test_objc_header_precedence_remains_deterministic(tmp_path: Path) -> None:
    (tmp_path / "shared.h").write_text("@interface Base\n- (void)work;\n@end\n")
    (tmp_path / "plain.c").write_text("int plain(void) { return 0; }\n")
    (tmp_path / "main.cpp").write_text("int main() { return 0; }\n")
    (tmp_path / "app.m").write_text("@implementation Base\n- (void)work {}\n@end\n")
    for language in ("auto", "cpp,c,objc", "objc,cpp,c"):
        graph = parse_directory(str(tmp_path), language)
        assert graph.source_file_counts == {"c": 1, "cpp": 1, "objc": 2}
        assert "shared:Base" in graph.nodes


def test_h_alone_keeps_automatic_detection_ambiguous(tmp_path: Path) -> None:
    (tmp_path / "plain.h").write_text("int helper(void) { return 0; }\n")
    assert detect_languages(str(tmp_path)) == []
    with pytest.raises(ValueError, match="No supported languages"):
        parse_directory(str(tmp_path), "auto")


def test_cpp_source_policy_marks_h_header_inspected(tmp_path: Path) -> None:
    (tmp_path / "widget.h").write_text(HEADER)
    graph = parse_roots(SourcePolicy((SourceRoot(str(tmp_path), "app"),)), "cpp")
    assert graph.source_file_counts == {"cpp": 1}
    assert graph.source_policy is not None
    files = cast(list[dict[str, str]], graph.source_policy["files"])
    assert len(files) == 1
    assert files[0]["status"] == "inspected"


def test_macro_inline_pointer_return_has_a_real_identifier_in_auto_mode(tmp_path: Path) -> None:
    (tmp_path / "config.h").write_text("#define ngx_inline inline\n")
    (tmp_path / "node.h").write_text(
        '#include "config.h"\n'
        "typedef struct ngx_rbtree_node_t ngx_rbtree_node_t;\n"
        "struct ngx_rbtree_node_t { ngx_rbtree_node_t *left; };\n"
        "static ngx_inline ngx_rbtree_node_t *\n"
        "ngx_rbtree_min(ngx_rbtree_node_t *node, ngx_rbtree_node_t *sentinel) {\n"
        "    while (node->left != sentinel) node = node->left;\n    return node;\n}\n"
    )
    (tmp_path / "main.c").write_text(
        '#include "node.h"\n'
        "ngx_rbtree_node_t *caller(ngx_rbtree_node_t *node, ngx_rbtree_node_t *end) {\n"
        "    return ngx_rbtree_min(node, end);\n}\n"
    )
    (tmp_path / "unrelated.cpp").write_text("/* unrelated */\n")
    graph = parse_directory(str(tmp_path), "auto")
    assert graph.nodes["node:ngx_rbtree_min"].name == "ngx_rbtree_min"
    assert dict(graph.nodes["node:ngx_rbtree_min"].attributes)["cpp_declarator_recovered"]
    assert [n["id"] for n in QueryEngine.from_graph(graph).callees_of("main:caller")] == [
        "node:ngx_rbtree_min"
    ]
    assert graph.parse_diagnostics
