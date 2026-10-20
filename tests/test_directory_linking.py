"""Semantic regressions for directory identities and conservative linking."""

from pathlib import Path

import pytest

from trailmark.analysis.entrypoints import detect_entrypoints
from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.nodes import NodeKind, NodeOrigin
from trailmark.parse import parse_directory


def test_same_language_extensions_and_init_modules_do_not_overwrite(tmp_path: Path) -> None:
    (tmp_path / "route.js").write_text("function first() {}")
    (tmp_path / "route.mjs").write_text("function second() {}")
    (tmp_path / "pkg.py").write_text("def module_function(): pass")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("def package_function(): pass")
    graph = parse_directory(str(tmp_path), "javascript,python")
    expected = {
        r"route\.js:first",
        r"route\.mjs:second",
        r"pkg\.py:module_function",
        r"pkg.__init__\.py:package_function",
    }
    assert expected <= graph.nodes.keys()
    assert all(graph.nodes[key].origin == NodeOrigin.SOURCE for key in expected)


def test_expanded_module_id_cannot_collide_with_another_stem(tmp_path: Path) -> None:
    for filename in ("a.js", "a.mjs", "a.js.js"):
        (tmp_path / filename).write_text("function keep() {}")
    graph = parse_directory(str(tmp_path), "javascript")
    functions = [unit for unit in graph.nodes.values() if unit.kind == NodeKind.FUNCTION]
    assert len(functions) == 3
    assert len({unit.id for unit in functions}) == 3
    assert {Path(unit.location.file_path).name for unit in functions} == {
        "a.js",
        "a.mjs",
        "a.js.js",
    }


@pytest.mark.parametrize("languages", ["c,objc", "objc,c", "auto"])
def test_shared_headers_have_one_owner_without_losing_other_files(
    tmp_path: Path, languages: str
) -> None:
    (tmp_path / "app.c").write_text("void run(void) {}")
    (tmp_path / "app.m").write_text("@implementation App\n- (void)run {}\n@end")
    (tmp_path / "plain.h").write_text("struct Record { int value; };\n")
    (tmp_path / "objects.h").write_text("@interface Thing\n- (void)work;\n@end")
    graph = parse_directory(str(tmp_path), languages)
    assert graph.nodes["plain:Record"].kind == NodeKind.STRUCT
    assert graph.nodes["objects:Thing"].kind == NodeKind.CLASS
    assert {r"app\.c:run", r"app\.m:App.run"} <= graph.nodes.keys()
    for module in ("plain", "objects"):
        assert (
            sum(unit.kind == NodeKind.MODULE and unit.id == module for unit in graph.nodes.values())
            == 1
        )


@pytest.mark.parametrize(
    ("language", "suffix", "caller", "other"),
    [
        (
            "solidity",
            "sol",
            "contract A { function run() public { helper(); } function helper() internal {} }",
            "contract B { function helper() internal {} }",
        ),
        (
            "cpp",
            "cpp",
            "class A { void run() { helper(); } void helper() {} };",
            "class B { void helper() {} };",
        ),
    ],
)
def test_bare_method_call_prefers_callers_container(
    tmp_path: Path, language: str, suffix: str, caller: str, other: str
) -> None:
    (tmp_path / f"caller.{suffix}").write_text(caller)
    (tmp_path / f"other.{suffix}").write_text(other)
    graph = parse_directory(str(tmp_path), language)
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    assert len(calls) == 1
    assert calls[0].source_id == "caller:A.run"
    assert calls[0].target_id == "caller:A.helper"
    assert calls[0].confidence == EdgeConfidence.CERTAIN


def test_imported_free_call_does_not_bind_an_unrelated_method(tmp_path: Path) -> None:
    (tmp_path / "caller.py").write_text("from missing import helper\ndef run(): helper()\n")
    (tmp_path / "other.py").write_text("class B:\n    def helper(self): pass\n")
    graph = parse_directory(str(tmp_path), "python")
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    assert len(calls) == 1
    assert graph.nodes[calls[0].target_id].origin == NodeOrigin.PROXY
    assert not calls[0].target_id.endswith("B.helper")


@pytest.mark.parametrize(
    ("language", "base_file", "base_source", "child_file", "child_source", "kind", "target"),
    [
        (
            "python",
            "base.py",
            "class Base: pass",
            "child.py",
            "from base import Base\nclass Child(Base): pass",
            EdgeKind.INHERITS,
            "base:Base",
        ),
        (
            "java",
            "Base.java",
            "interface Base {}",
            "Child.java",
            "class Child implements Base {}",
            EdgeKind.IMPLEMENTS,
            "Base:Base",
        ),
        (
            "solidity",
            "Base.sol",
            "contract Base {}",
            "Child.sol",
            'import "./Base.sol"; contract Child is Base {}',
            EdgeKind.INHERITS,
            "Base:Base",
        ),
    ],
)
def test_cross_file_type_relationships_resolve_before_analysis(
    tmp_path: Path,
    language: str,
    base_file: str,
    base_source: str,
    child_file: str,
    child_source: str,
    kind: EdgeKind,
    target: str,
) -> None:
    (tmp_path / base_file).write_text(base_source)
    (tmp_path / child_file).write_text(child_source)
    graph = parse_directory(str(tmp_path), language)
    edges = [edge for edge in graph.edges if edge.kind == kind]
    assert len(edges) == 1
    assert edges[0].target_id == target
    assert edges[0].confidence == EdgeConfidence.INFERRED


def test_ambiguous_base_types_stay_unresolved(tmp_path: Path) -> None:
    (tmp_path / "one.py").write_text("class Base: pass")
    (tmp_path / "two.py").write_text("class Base: pass")
    (tmp_path / "child.py").write_text("class Child(Base): pass")
    graph = parse_directory(str(tmp_path), "python")
    edges = [edge for edge in graph.edges if edge.kind == EdgeKind.INHERITS]
    assert len(edges) == 1
    assert edges[0].target_id == "child:Base"
    assert edges[0].target_id not in graph.nodes


def test_cross_file_solidity_override_annotation(tmp_path: Path) -> None:
    (tmp_path / "Base.sol").write_text("contract Base { function run() public virtual {} }")
    (tmp_path / "Child.sol").write_text(
        'import "./Base.sol"; contract Child is Base { function run() public override {} }'
    )
    graph = parse_directory(str(tmp_path), language="solidity")
    detect_entrypoints(graph, str(tmp_path))
    base = graph.nodes["Base:Base.run"]
    assert dict(base.attributes)["solidity_overridden_by"] == "Child:Child.run"


@pytest.mark.parametrize("language", ["c,objc", "objc,c", "auto"])
def test_mixed_headers_preserve_c_types_and_objc_classes(tmp_path: Path, language: str) -> None:
    (tmp_path / "app.c").write_text("void c_main(void) {}")
    (tmp_path / "app.m").write_text("void objc_main(void) {}")
    (tmp_path / "mixed.h").write_text(
        "#ifndef MIXED_H\n#define MIXED_H\n"
        "struct Record { int value; };\n"
        "@interface Thing\n- (void)work;\n@end\n#endif\n"
    )
    (tmp_path / "plain.h").write_text(
        "// @interface is mentioned in a comment\nstruct Plain { int value; };"
    )
    graph = parse_directory(str(tmp_path), language)
    assert {"mixed:Record", "mixed:Thing", "plain:Plain"} <= graph.nodes.keys()
    for name in ("mixed:Record", "plain:Plain"):
        assert graph.nodes[name].kind == NodeKind.STRUCT


@pytest.mark.parametrize("language", ["objc", "c,objc", "objc,c", "auto"])
def test_header_free_functions_link_across_c_family_frontends(
    tmp_path: Path, language: str
) -> None:
    (tmp_path / "util.h").write_text("static inline void helper(void) {}")
    (tmp_path / "main.c").write_text("void main(void) {}")
    (tmp_path / "app.m").write_text('#include "util.h"\nvoid run(void) { helper(); }')
    (tmp_path / "other.py").write_text("def helper(): pass")
    graph = parse_directory(str(tmp_path), language)
    calls = [
        edge for edge in graph.edges if edge.source_id == "app:run" and edge.kind == EdgeKind.CALLS
    ]
    assert len(calls) == 1
    assert calls[0].target_id == "util:helper"
    assert calls[0].confidence == EdgeConfidence.INFERRED


def test_rego_files_can_share_a_package_module(tmp_path: Path) -> None:
    (tmp_path / "one.rego").write_text("package p\nfirst { true }\n")
    (tmp_path / "two.rego").write_text("package p\nsecond { true }\n")
    graph = parse_directory(str(tmp_path), "rego")
    assert {"p", "p:first", "p:second"} <= graph.nodes.keys()


@pytest.mark.parametrize("language", ["c", "objc", "c,objc", "objc,c"])
def test_header_call_does_not_bind_a_same_named_module(tmp_path: Path, language: str) -> None:
    (tmp_path / "helper.h").write_text(
        "static inline void helper(void) {}\nstatic inline void run(void) { helper(); }"
    )
    graph = parse_directory(str(tmp_path), language)
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    assert len(calls) == 1
    assert calls[0].source_id == "helper:run"
    assert calls[0].target_id == "helper:helper"
    assert calls[0].confidence == EdgeConfidence.CERTAIN


def test_root_initializer_preserves_escaped_package_identity(tmp_path: Path) -> None:
    root = tmp_path / "root.pkg"
    root.mkdir()
    (root / "__init__.py").write_text("def initialize(): pass")
    graph = parse_directory(str(root), "python")
    assert {r"root\.pkg", r"root\.pkg:initialize"} <= graph.nodes.keys()
    assert "root.pkg" not in graph.nodes


def test_qualified_cpp_call_does_not_bind_unqualified_free_function(tmp_path: Path) -> None:
    (tmp_path / "caller.cpp").write_text(
        "namespace external { void helper(); }\nvoid run() { external::helper(); }"
    )
    (tmp_path / "worker.cpp").write_text("void helper() {}")
    graph = parse_directory(str(tmp_path), "cpp")
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    assert len(calls) == 1
    assert graph.nodes[calls[0].target_id].origin == NodeOrigin.PROXY
    assert dict(calls[0].attributes)["raw_target"] == "external::helper"


def test_java_method_name_does_not_shadow_type_namespace(tmp_path: Path) -> None:
    (tmp_path / "Types.java").write_text(
        "class Base {}\nclass Outer { void Base() {} class Inner extends Base {} }"
    )
    graph = parse_directory(str(tmp_path), "java")
    edges = [edge for edge in graph.edges if edge.kind == EdgeKind.INHERITS]
    assert len(edges) == 1
    assert edges[0].source_id == "Types:Outer.Inner"
    assert edges[0].target_id == "Types:Base"
    assert graph.nodes[edges[0].target_id].kind == NodeKind.CLASS
