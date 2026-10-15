"""Local Rust items retain their bodies, owners, and lexical call targets."""

from __future__ import annotations

from pathlib import Path

import pytest

from trailmark.models.edges import EdgeKind
from trailmark.models.nodes import NodeKind
from trailmark.parsers.rust.parser import RustParser
from trailmark.query.api import QueryEngine


@pytest.mark.parametrize("wrapper", [False, True])
@pytest.mark.parametrize(
    ("declaration", "call", "owner", "kind"),
    [
        (
            "struct S; impl S { fn m(&self) { if true { dangerous(); } } }",
            "S.m();",
            "S",
            NodeKind.METHOD,
        ),
        (
            "trait T { fn m(&self) { if true { dangerous(); } } }",
            "",
            "T",
            NodeKind.METHOD,
        ),
        (
            "mod local { use super::dangerous; pub fn m() { if true { dangerous(); } } }",
            "local::m();",
            "local",
            NodeKind.FUNCTION,
        ),
    ],
)
def test_local_item_bodies_have_distinct_owners(
    tmp_path: Path, wrapper: bool, declaration: str, call: str, owner: str, *, kind: NodeKind
) -> None:
    path = tmp_path / "items.rs"
    source = f"fn dangerous() {{}} fn outer() {{ {declaration} {call} }}"
    source += f" fn other() {{ {declaration} {call} }}"
    path.write_text(f"verus! {{ {source} }}" if wrapper else source)
    graph = RustParser().parse_file(str(path))
    calls = {(e.source_id, e.target_id) for e in graph.edges if e.kind == EdgeKind.CALLS}
    for function in ("outer", "other"):
        method = f"items:{function}.{owner}.m"
        assert graph.nodes[method].kind == kind
        assert graph.nodes[method].cyclomatic_complexity == 2
        assert graph.nodes[f"items:{function}"].cyclomatic_complexity == 1
        assert (method, "items:dangerous") in calls
        engine = QueryEngine.from_graph(graph)
        assert {n["id"] for n in engine.reachable_from(method)} == {"items:dangerous"}
        if call:
            assert {n["id"] for n in engine.reachable_from(f"items:{function}")} == {
                method,
                "items:dangerous",
            }
    assert len(calls) == (4 if call else 2)


@pytest.mark.parametrize("wrapper", [False, True])
def test_local_types_and_modules_shadow_only_within_their_block(
    tmp_path: Path, wrapper: bool
) -> None:
    path = tmp_path / "blocks.rs"
    source = """\
fn first() {}
fn second() {}
fn outer() {
    { struct S; impl S { fn m() { first(); } } S::m(); }
    { impl S { fn m() { second(); } } struct S; S::m(); }
    { mod local { pub fn m() {} } local::m(); }
    { mod local { pub fn m() {} } local::m(); }
    S::m(); local::m();
}
"""
    path.write_text(f"verus! {{ {source} }}" if wrapper else source)
    graph = RustParser().parse_file(str(path))
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    outer = [e for e in calls if e.source_id == "blocks:outer"]
    assert [graph.nodes[e.target_id].location.start_line for e in outer[:4]] == [4, 5, 6, 7]
    assert [e.target_id for e in outer[4:]] == ["S::m", "local::m"]
    assert len({e.target_id for e in outer[:4]}) == 4
    assert [e.target_id for e in calls if e.source_id != "blocks:outer"] == [
        "blocks:first",
        "blocks:second",
    ]
    assert "@" in outer[1].target_id
    assert "@" in outer[3].target_id


def test_local_module_paths_and_imports_respect_rust_module_boundaries(tmp_path: Path) -> None:
    path = tmp_path / "modules.rs"
    path.write_text("""\
fn dangerous() {}
fn outer() {
    fn helper() {}
    mod local {
        use super::dangerous as alias;
        pub fn entry() { alias(); self::nested::f(); helper(); }
        mod nested { pub fn f() { super::super::dangerous(); } }
    }
    use local::entry as run;
    run(); local::nested::f();
}
""")
    graph = RustParser().parse_file(str(path))
    assert [(e.source_id, e.target_id) for e in graph.edges if e.kind == EdgeKind.CALLS] == [
        ("modules:outer", "modules:outer.local.entry"),
        ("modules:outer", "modules:outer.local.nested.f"),
        ("modules:outer.local.entry", "modules:dangerous"),
        ("modules:outer.local.entry", "modules:outer.local.nested.f"),
        ("modules:outer.local.entry", "modules:helper"),
        ("modules:outer.local.nested.f", "modules:dangerous"),
    ]


def test_local_enum_impls_and_trait_implementations_keep_lexical_owners(tmp_path: Path) -> None:
    path = tmp_path / "types.rs"
    path.write_text("""\
fn outer() {
    trait T { fn m(&self) {} }
    enum E { Value }
    impl T for E { fn m(&self) { helper(); } }
    fn helper() {}
    E::Value.m();
}
""")
    graph = RustParser().parse_file(str(path))
    assert graph.nodes["types:outer.E"].kind == NodeKind.ENUM
    assert graph.nodes["types:outer.E.m"].kind == NodeKind.METHOD
    assert ("types:outer.E", "types:outer.T") in {
        (e.source_id, e.target_id) for e in graph.edges if e.kind == EdgeKind.IMPLEMENTS
    }
    assert ("types:outer.E.m", "types:outer.helper") in {
        (e.source_id, e.target_id) for e in graph.edges if e.kind == EdgeKind.CALLS
    }


@pytest.mark.parametrize("local", [False, True])
def test_qualified_calls_resolve_known_types_without_guessing_variable_types(
    tmp_path: Path, local: bool
) -> None:
    path = tmp_path / "qualified.rs"
    source = "struct S; impl S { fn new() {} } fn caller() { S::new(); s.new(); unknown::new(); }"
    path.write_text(f"fn outer() {{ {source} }}" if local else source)
    graph = RustParser().parse_file(str(path))
    prefix = "qualified:outer." if local else "qualified:"
    assert [(e.source_id, e.target_id) for e in graph.edges if e.kind == EdgeKind.CALLS] == [
        (f"{prefix}caller", f"{prefix}S.new"),
        (f"{prefix}caller", "s.new"),
        (f"{prefix}caller", "unknown::new"),
    ]


@pytest.mark.parametrize("item", ["impl S", "trait T", "mod local"])
def test_verus_macros_inside_function_local_items_still_remain_opaque(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, item: str
) -> None:
    path = tmp_path / "opaque.rs"
    path.write_text(
        f"fn outer() {{ struct S; {item} {{ verus! {{ fn hidden() {{}} }} fn visible() {{}} }} }}"
    )
    graph = RustParser().parse_file(str(path))
    assert not any(node.name == "hidden" for node in graph.nodes.values())
    assert sum(node.name == "visible" for node in graph.nodes.values()) == 1
    assert caplog.text.count("inside a function body") == 1


@pytest.mark.parametrize("wrapper", [False, True])
@pytest.mark.parametrize("in_module", [False, True])
def test_bodyless_local_modules_do_not_replace_their_enclosing_binding(
    tmp_path: Path, wrapper: bool, in_module: bool
) -> None:
    path = tmp_path / "external.rs"
    body = '#[path="other.rs"] mod external;'
    if in_module:
        source = f"fn outer() {{ mod local {{ {body} pub fn entry() {{}} }} local::entry(); }}"
        expected = ("external:outer", "external:outer.local.entry")
    else:
        source = f"use crate::outer as alias; fn caller() {{ alias(); }} fn outer() {{ {body} }}"
        expected = ("external:caller", "external:outer")
    path.write_text(f"verus! {{ {source} }}" if wrapper else source)
    graph = RustParser().parse_file(str(path))
    assert [(e.source_id, e.target_id) for e in graph.edges if e.kind == EdgeKind.CALLS] == [
        expected
    ]


def test_local_impl_selects_the_named_type_among_other_local_types(tmp_path: Path) -> None:
    path = tmp_path / "types.rs"
    path.write_text("fn outer() { struct A; struct B; impl B { fn m() {} } B::m(); }")
    graph = RustParser().parse_file(str(path))
    assert "types:outer.B.m" in graph.nodes
    assert "types:outer.A.m" not in graph.nodes
    assert [(e.source_id, e.target_id) for e in graph.edges if e.kind == EdgeKind.CALLS] == [
        ("types:outer", "types:outer.B.m")
    ]


def test_impl_in_function_for_a_nonlocal_type_retains_its_method_body(tmp_path: Path) -> None:
    path = tmp_path / "nonlocal.rs"
    path.write_text(
        "struct S; fn dangerous() {} fn outer() { impl S { fn m() { dangerous(); } } S::m(); }"
    )
    graph = RustParser().parse_file(str(path))
    assert graph.nodes["nonlocal:outer.S.m"].kind == NodeKind.METHOD
    assert [(e.source_id, e.target_id) for e in graph.edges if e.kind == EdgeKind.CALLS] == [
        ("nonlocal:outer", "nonlocal:outer.S.m"),
        ("nonlocal:outer.S.m", "nonlocal:dangerous"),
    ]
    assert {n["id"] for n in QueryEngine.from_graph(graph).reachable_from("outer")} == {
        "nonlocal:outer.S.m",
        "nonlocal:dangerous",
    }


def test_local_impl_can_refer_to_an_imported_type_and_local_trait(tmp_path: Path) -> None:
    path = tmp_path / "imported.rs"
    path.write_text("""\
mod types { pub struct S; }
use crate::types::S as Alias;
fn dangerous() {}
fn outer() {
    trait T { fn m(); }
    impl T for Alias { fn m() { dangerous(); } }
    Alias::m();
}
""")
    graph = RustParser().parse_file(str(path))
    assert ("imported.types:S", "imported:outer.T") in {
        (e.source_id, e.target_id) for e in graph.edges if e.kind == EdgeKind.IMPLEMENTS
    }
    assert ("imported.types:S", "imported:outer.Alias.m") in {
        (e.source_id, e.target_id) for e in graph.edges if e.kind == EdgeKind.CONTAINS
    }
    assert {n["id"] for n in QueryEngine.from_graph(graph).reachable_from("outer")} == {
        "imported:outer.Alias.m",
        "imported:dangerous",
    }


def test_imported_namespace_alias_can_qualify_a_known_method(tmp_path: Path) -> None:
    path = tmp_path / "aliases.rs"
    path.write_text("""\
mod items { pub struct S; impl S { pub fn m() {} } }
use crate::items as alias;
fn caller() { alias::S::m(); }
""")
    graph = RustParser().parse_file(str(path))
    assert [(e.source_id, e.target_id) for e in graph.edges if e.kind == EdgeKind.CALLS] == [
        ("aliases:caller", "aliases.items:S.m")
    ]


def test_local_impl_for_an_unavailable_type_preserves_its_calls(tmp_path: Path) -> None:
    path = tmp_path / "external_type.rs"
    path.write_text("""\
mod external;
use external::S;
fn dangerous() {}
fn outer() { impl S { fn m() { dangerous(); } } }
""")
    graph = RustParser().parse_file(str(path))
    assert graph.nodes["external_type:outer.S.m"].kind == NodeKind.METHOD
    assert [(e.source_id, e.target_id) for e in graph.edges if e.kind == EdgeKind.CALLS] == [
        ("external_type:outer.S.m", "external_type:dangerous")
    ]


def test_impl_in_an_inner_block_does_not_narrow_its_type_visibility(tmp_path: Path) -> None:
    path = tmp_path / "visibility.rs"
    path.write_text("fn outer() { struct S(); { impl S { fn m() {} } } S(); }")
    graph = RustParser().parse_file(str(path))
    assert [(e.source_id, e.target_id) for e in graph.edges if e.kind == EdgeKind.CALLS] == [
        ("visibility:outer", "visibility:outer.S")
    ]


def test_drop_impls_for_same_named_guards_in_different_functions_do_not_collide(
    tmp_path: Path,
) -> None:
    path = tmp_path / "guards.rs"
    path.write_text("""\
fn first_effect() {}
fn second_effect() {}
fn first() { struct Guard; impl Drop for Guard { fn drop(&mut self) { first_effect(); } } }
fn second() { struct Guard; impl Drop for Guard { fn drop(&mut self) { second_effect(); } } }
""")
    graph = RustParser().parse_file(str(path))
    assert graph.nodes["guards:first.Guard.drop"].kind == NodeKind.METHOD
    assert graph.nodes["guards:second.Guard.drop"].kind == NodeKind.METHOD
    assert [(e.source_id, e.target_id) for e in graph.edges if e.kind == EdgeKind.CALLS] == [
        ("guards:first.Guard.drop", "guards:first_effect"),
        ("guards:second.Guard.drop", "guards:second_effect"),
    ]


@pytest.mark.parametrize("wrapper", [False, True])
@pytest.mark.parametrize(
    ("source", "caller", "target"),
    [
        (
            "struct HostStream { fd: usize } impl HostStream {"
            "fn write_all(&mut self, buffer: &[u8]) { write_all(self.fd, buffer); } }"
            "fn write_all(fd: usize, buffer: &[u8]) {}",
            "HostStream.write_all",
            "resolution:write_all",
        ),
        (
            "struct Guard; impl Drop for Guard { fn drop(&mut self) {} }"
            "impl Guard { fn release(g: Self) { drop(g); } }",
            "Guard.release",
            "resolution:drop",
        ),
        (
            "struct M; impl M { fn try_lock(&self) -> bool { true }"
            "fn lock_internal(&self, try_lock: impl FnOnce() -> bool) -> bool { try_lock() } }",
            "M.lock_internal",
            "resolution:try_lock",
        ),
        (
            "fn dangerous() {} fn helper() { dangerous(); } struct S;"
            "impl S { fn helper(&self) {} fn run(&self) { self.helper(); } }",
            "S.run",
            "self.helper",
        ),
        (
            "fn dangerous() {} mod config { pub fn load() { super::dangerous(); } }"
            "struct Cfg; impl Cfg { fn load(&self) {} }"
            "fn run(config: Cfg) { config.load(); }",
            "run",
            "config.load",
        ),
        (
            "struct LitStr; impl LitStr { fn value(&self) -> u8 { 0 }"
            "fn parse_with(&self) { self.value(); } } mod value { pub fn unrelated() {} }",
            "LitStr.parse_with",
            "self.value",
        ),
        (
            "impl u32 { fn helper() {} fn run() { helper(); } } fn helper() {}",
            "u32.run",
            "resolution:helper",
        ),
    ],
)
def test_bare_calls_and_receivers_do_not_capture_associated_items(
    tmp_path: Path, wrapper: bool, source: str, caller: str, target: str
) -> None:
    path = tmp_path / "resolution.rs"
    path.write_text(f"verus! {{ {source} }}" if wrapper else source)
    graph = RustParser().parse_file(str(path))
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    assert [edge.target_id for edge in calls if edge.source_id == f"resolution:{caller}"] == [
        target
    ]
    assert all(
        graph.nodes[edge.target_id].kind != NodeKind.MODULE
        for edge in calls
        if edge.target_id in graph.nodes
    )


def test_associated_paths_and_unit_struct_receivers_still_resolve(tmp_path: Path) -> None:
    path = tmp_path / "associated.rs"
    path.write_text("""\
struct S;
enum E { Value }
impl S { fn helper() {} }
impl E { fn helper() {} }
mod helper { pub fn unrelated() {} }
fn run() { S::helper(); E::helper(); S.helper(); helper(); }
""")
    graph = RustParser().parse_file(str(path))
    assert [edge.target_id for edge in graph.edges if edge.kind == EdgeKind.CALLS] == [
        "associated:S.helper",
        "associated:E.helper",
        "associated:S.helper",
        "associated:helper",
    ]


@pytest.mark.timeout(5, func_only=True)
def test_large_rust_file_resolves_calls_without_scanning_every_declaration(tmp_path: Path) -> None:
    path = tmp_path / "large.rs"
    count = 8000
    path.write_text("\n".join(f"fn f{i}() {{ f{(i + 1) % count}(); }}" for i in range(count)))
    graph = RustParser().parse_file(str(path))
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    assert len(calls) == count
    assert {(edge.source_id, edge.target_id) for edge in calls} == {
        (f"large:f{i}", f"large:f{(i + 1) % count}") for i in range(count)
    }
