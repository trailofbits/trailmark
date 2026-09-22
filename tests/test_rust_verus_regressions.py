"""Known answers for Verus scope, diagnostics, and parser state."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from unittest.mock import patch

import pytest

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.nodes import NodeKind, SourceLocation, TypeParameter, TypeRef
from trailmark.parsers.rust.parser import RustParser
from trailmark.tree_sitter_custom import verus


def test_inline_modules_keep_distinct_nodes_and_call_provenance(tmp_path: Path) -> None:
    path = tmp_path / "scope.rs"
    path.write_text("""\
mod a {
    fn helper() {}
    verus! { pub fn f() { helper(); } }
    mod nested { verus! { pub fn f() {} } }
}
mod b {
    fn helper() {}
    verus! { pub fn f() { helper(); helper(); } }
}
""")
    graph = RustParser().parse_file(str(path))
    assert set(graph.nodes) == {
        "scope",
        "scope.a",
        "scope.a:helper",
        "scope.a:f",
        "scope.a.nested",
        "scope.a.nested:f",
        "scope.b",
        "scope.b:helper",
        "scope.b:f",
    }
    assert graph.nodes["scope.a:f"].location.start_line == 3
    assert graph.nodes["scope.b:f"].location.start_line == 8
    assert Counter((e.source_id, e.target_id, e.kind) for e in graph.edges) == Counter(
        {
            ("scope", "scope.a", EdgeKind.CONTAINS): 1,
            ("scope.a", "scope.a:helper", EdgeKind.CONTAINS): 1,
            ("scope.a", "scope.a:f", EdgeKind.CONTAINS): 1,
            ("scope.a:f", "scope.a:helper", EdgeKind.CALLS): 1,
            ("scope.a", "scope.a.nested", EdgeKind.CONTAINS): 1,
            ("scope.a.nested", "scope.a.nested:f", EdgeKind.CONTAINS): 1,
            ("scope", "scope.b", EdgeKind.CONTAINS): 1,
            ("scope.b", "scope.b:helper", EdgeKind.CONTAINS): 1,
            ("scope.b", "scope.b:f", EdgeKind.CONTAINS): 1,
            ("scope.b:f", "scope.b:helper", EdgeKind.CALLS): 2,
        }
    )
    assert all(e.confidence == EdgeConfidence.CERTAIN for e in graph.edges)
    repeated = [e for e in graph.edges if e.source_id == "scope.b:f"]
    assert repeated[0].location != repeated[1].location


@pytest.mark.parametrize("wrapper", [False, True])
def test_impl_and_trait_macro_methods_keep_their_owners(tmp_path: Path, wrapper: bool) -> None:
    source = """\
struct A {}
struct B {}
impl A { verus! { fn f() {} } }
impl B { verus! { fn f() {} } }
trait T { verus! { fn f() {} } }
"""
    path = tmp_path / "owners.rs"
    path.write_text(f"verus! {{\n{source}}}" if wrapper else source)
    graph = RustParser().parse_file(str(path))
    methods = {node.id for node in graph.nodes.values() if node.kind == NodeKind.METHOD}
    assert methods == {"owners:A.f", "owners:B.f", "owners:T.f"}
    assert not any(node.kind == NodeKind.FUNCTION for node in graph.nodes.values())
    assert Counter((e.source_id, e.target_id) for e in graph.edges) == Counter(
        {
            ("owners", "owners:A"): 1,
            ("owners", "owners:B"): 1,
            ("owners", "owners:T"): 1,
            ("owners:A", "owners:A.f"): 1,
            ("owners:B", "owners:B.f"): 1,
            ("owners:T", "owners:T.f"): 1,
        }
    )


def test_nested_unsupported_macros_do_not_leak_functions(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "delimiters.rs"
    path.write_text("""\
verus! {
    verus!(fn paren() {});
    verus /* comment */ ![fn bracket() {}];
    verus! { fn nested() {} }
    fn real() {}
}
""")
    graph = RustParser().parse_file(str(path))
    assert set(graph.nodes) == {"delimiters", "delimiters:nested", "delimiters:real"}
    assert len(graph.edges) == 2
    assert len(caplog.records) == 2
    assert all("unsupported Verus delimiter" in r.message for r in caplog.records)


def test_invalid_block_does_not_consume_valid_neighbors(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "errors.rs"
    path.write_text("verus! { fn first() {} }\nverus! { fn }\nverus! { fn last() {} }\n")
    graph = RustParser().parse_file(str(path))
    assert set(graph.nodes) == {"errors", "errors:first", "errors:last"}
    assert len(graph.edges) == 2
    assert len(caplog.records) == 1
    assert f"{path}:2:1:" in caplog.records[0].message
    assert "Verus syntax errors" in caplog.records[0].message


@pytest.mark.parametrize("body", ["", "const LIMIT: u64 = 100;"])
def test_valid_block_without_functions_is_not_an_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, body: str
) -> None:
    path = tmp_path / "empty.rs"
    path.write_text(f"verus! {{ {body} }}")
    graph = RustParser().parse_file(str(path))
    assert set(graph.nodes) == {"empty"}
    assert graph.edges == []
    assert caplog.records == []


@pytest.mark.timeout(5)
def test_opaque_macros_are_diagnosed_without_expanding_them(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "opaque.rs"
    path.write_text("""\
wrapper! { verus! { fn hidden() {} } }
macro_rules! unused { () => { verus! { fn never_emitted() {} } }; }
not_verus! { fn also_hidden() {} }
fn ordinary() { let s = "verus! { fn text() {} }"; }
// verus! { fn comment() {} }
verus! { fn visible() {} }
""")
    graph = RustParser().parse_file(str(path))
    assert set(graph.nodes) == {"opaque", "opaque:ordinary", "opaque:visible"}
    assert len(graph.edges) == 2
    assert len(caplog.records) == 1
    assert "inside an opaque macro" in caplog.records[0].message


def test_function_local_verus_is_not_misrepresented_at_module_scope(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "local.rs"
    path.write_text("fn outer() { verus! { fn inner() {} } }")
    graph = RustParser().parse_file(str(path))
    assert set(graph.nodes) == {"local", "local:outer"}
    assert "inside a function body" in caplog.text


def test_contract_comparisons_are_not_generic_declarations(tmp_path: Path) -> None:
    path = tmp_path / "generics.rs"
    path.write_text("""\
verus! {
    fn bounded(x: u64) -> (r: u64) requires x < 100, ensures (r) > (x) { x + 1 }
    spec fn model<T: Copy, const N: usize>(x: T) -> bool { true }
    struct S<T> { value: T }
    trait Trait<T> {}
    enum E<T> { Value(T) }
}
""")
    graph = RustParser().parse_file(str(path))
    assert graph.nodes["generics:bounded"].type_parameters == ()
    assert graph.nodes["generics:model"].type_parameters == (
        TypeParameter("T", constraints=(TypeRef("Copy"),)),
        TypeParameter("N", constraints=(TypeRef("usize"),)),
    )
    for name in ("S", "Trait", "E"):
        assert graph.nodes[f"generics:{name}"].type_parameters == (TypeParameter("T"),)


def test_successful_parser_reuse_resets_ranges_and_loads_once(tmp_path: Path) -> None:
    sources = {
        "first": "verus! { fn first() {} }",
        "second": (
            "// π\n\nfn host() {}\nverus! { fn second() { host(); } }\nverus! { fn last() {} }"
        ),
        "plain": "fn plain() {}",
    }
    for name, source in sources.items():
        (tmp_path / f"{name}.rs").write_text(source)
    parser = RustParser()
    with patch.object(verus, "language", wraps=verus.language) as loader:
        graphs = [
            parser.parse_file(str(tmp_path / f"{name}.rs"))
            for name in ("first", "second", "plain", "first")
        ]
    loader.assert_called_once()
    assert [set(g.nodes) for g in graphs] == [
        {"first", "first:first"},
        {"second", "second:host", "second:second", "second:last"},
        {"plain", "plain:plain"},
        {"first", "first:first"},
    ]
    assert [len(g.edges) for g in graphs] == [1, 4, 1, 1]
    assert graphs[0].nodes == graphs[3].nodes
    assert graphs[0].edges == graphs[3].edges
    call = next(e for e in graphs[1].edges if e.kind == EdgeKind.CALLS)
    assert (call.source_id, call.target_id) == ("second:second", "second:host")


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_verus_locations_use_original_utf8_byte_columns(tmp_path: Path, newline: bytes) -> None:
    path = tmp_path / "positions.rs"
    path.write_bytes(
        newline.join(
            [
                b"fn host() {}",
                b"verus! { fn first() {} }",
                "/* π */ verus! { fn second() { host(); } }".encode(),
                b"",
            ]
        )
    )
    graph = RustParser().parse_file(str(path))
    assert graph.nodes["positions:second"].location == SourceLocation(str(path), 3, 3, 18, 41)
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 1
    assert calls[0].location == SourceLocation(str(path), 3, 3, 32, 38)


@pytest.mark.parametrize("wrapper", [False, True])
def test_doc_comment_ownership_and_plain_rust_attributes(tmp_path: Path, wrapper: bool) -> None:
    source = """\
//! Container documentation.
fn first() {}
/// Struct documentation.
#[derive(Debug)]
#[allow(dead_code)]
struct S;
//// Ordinary comment, not a doc attribute.
fn last() {}
"""
    path = tmp_path / "docs.rs"
    path.write_text(f"verus! {{\n{source}}}" if wrapper else source)
    graph = RustParser().parse_file(str(path))
    assert graph.nodes["docs:first"].docstring is None
    assert graph.nodes["docs:S"].docstring == "Struct documentation."
    assert graph.nodes["docs:last"].docstring is None


def test_call_fixture_has_independently_specified_graph() -> None:
    path = Path(__file__).parent / "fixtures/kat/rust/verus_calls.rs"
    graph = RustParser().parse_file(str(path))
    prefix = "verus_calls"
    expected_kinds = {
        prefix: NodeKind.MODULE,
        f"{prefix}.inner": NodeKind.MODULE,
        **{
            f"{prefix}:{name}": NodeKind.FUNCTION
            for name in ("host", "model", "lemma", "checked", "last")
        },
        f"{prefix}.inner:helper": NodeKind.FUNCTION,
        f"{prefix}.inner:checked": NodeKind.FUNCTION,
    }
    assert {k: v.kind for k, v in graph.nodes.items()} == expected_kinds
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert Counter(
        (e.source_id, e.target_id, e.location.start_line, e.location.start_col, e.location.end_col)
        for e in calls
        if e.location is not None
    ) == Counter(
        [
            (f"{prefix}:lemma", f"{prefix}:host", 4, 23, 29),
            (f"{prefix}:lemma", f"{prefix}:host", 4, 31, 37),
            (f"{prefix}:checked", f"{prefix}:model", 9, 11, 19),
            (f"{prefix}:checked", f"{prefix}:lemma", 9, 22, 29),
            (f"{prefix}.inner:checked", f"{prefix}.inner:helper", 15, 32, 40),
            (f"{prefix}:last", f"{prefix}:checked", 17, 21, 31),
        ]
    )
    assert len(graph.edges) == 14  # Eight containment edges and six call sites.
    assert all(e.confidence == EdgeConfidence.CERTAIN for e in graph.edges)
    checked = graph.nodes[f"{prefix}:checked"]
    assert checked.return_type == TypeRef("u64")
    assert checked.type_parameters == ()
    assert checked.cyclomatic_complexity == 2
    assert len(checked.branches) == 1
    assert checked.branches[0].condition == "model(x)"
    assert checked.branches[0].location == SourceLocation(str(path), 9, 9, 8, 32)


def test_declaration_fixture_has_independently_specified_metadata() -> None:
    path = Path(__file__).parent / "fixtures/kat/rust/verus_declarations.rs"
    graph = RustParser().parse_file(str(path))
    prefix = "verus_declarations"
    assert set(graph.nodes) == {prefix} | {
        f"{prefix}:{name}"
        for name in (
            "Store",
            "Choice",
            "Check",
            "Check.valid",
            "Store.valid",
            "Store.get",
            "token",
            "pair",
            "identity",
            "values",
        )
    }
    assert graph.dependencies == ["std"]
    for name, kind, doc in [
        ("Store", NodeKind.STRUCT, "Generic storage."),
        ("Choice", NodeKind.ENUM, "Optional value."),
        ("Check", NodeKind.TRAIT, "Provides a default method."),
    ]:
        node = graph.nodes[f"{prefix}:{name}"]
        assert (node.kind, node.docstring) == (kind, doc)
    for name in ("Store", "Choice"):
        assert graph.nodes[f"{prefix}:{name}"].type_parameters == (TypeParameter("T"),)
    for name, return_name in [
        ("Check.valid", "bool"),
        ("Store.valid", "bool"),
        ("Store.get", "&T"),
        ("token", "u64"),
        ("pair", "(u64, bool)"),
        ("identity", "T"),
        ("values", "Vec<u64>"),
    ]:
        assert graph.nodes[f"{prefix}:{name}"].return_type == TypeRef(return_name)
    assert graph.nodes[f"{prefix}:Store.get"].docstring == "Retains the reference type."
    assert graph.nodes[f"{prefix}:identity"].type_parameters == (
        TypeParameter("T", constraints=(TypeRef("Copy"),)),
    )
    assert [(p.name, p.type_ref) for p in graph.nodes[f"{prefix}:token"].parameters] == [
        ("value", TypeRef("u64"))
    ]
    assert [(e.source_id, e.target_id) for e in graph.edges if e.kind == EdgeKind.IMPLEMENTS] == [
        (f"{prefix}:Store", f"{prefix}:Check")
    ]


@pytest.mark.parametrize(
    "error",
    [
        ImportError("invalid binding"),
        AttributeError("missing entrypoint"),
        ValueError("unsupported ABI"),
    ],
)
def test_failed_load_is_cached_per_parser_and_fresh_parser_can_recover(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, error: Exception
) -> None:
    path = tmp_path / "recover.rs"
    path.write_text("fn host() {}\nfn caller() { host(); }\nverus! { fn verified() { host(); } }")
    parser = RustParser()
    with patch.object(verus, "language", side_effect=error) as loader:
        graphs = [parser.parse_file(str(path)) for _ in range(2)]
    loader.assert_called_once()
    for graph in graphs:
        assert set(graph.nodes) == {"recover", "recover:host", "recover:caller"}
        assert Counter((e.source_id, e.target_id, e.kind) for e in graph.edges) == Counter(
            {
                ("recover", "recover:host", EdgeKind.CONTAINS): 1,
                ("recover", "recover:caller", EdgeKind.CONTAINS): 1,
                ("recover:caller", "recover:host", EdgeKind.CALLS): 1,
            }
        )
    assert len(caplog.records) == 1
    assert caplog.records[0].levelname == "WARNING"
    assert str(error) in caplog.records[0].message
    recovered = RustParser().parse_file(str(path))
    assert set(recovered.nodes) == set(graphs[0].nodes) | {"recover:verified"}
    assert len(recovered.edges) == 5


def test_many_separated_blocks_keep_every_declaration_once(tmp_path: Path) -> None:
    path = tmp_path / "many.rs"
    path.write_text("\n".join(f"verus! {{ fn f{i}() {{}} }}" for i in range(250)))
    graph = RustParser().parse_file(str(path))
    assert set(graph.nodes) == {"many"} | {f"many:f{i}" for i in range(250)}
    assert len(graph.edges) == 250


@pytest.mark.parametrize("wrapper", [False, True])
def test_function_local_macros_do_not_contribute_outer_calls_or_branches(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, wrapper: bool
) -> None:
    path = tmp_path / "local.rs"
    source = "fn outer() { verus! { fn inner() { if true { helper(); } } } }\nfn helper() {}"
    path.write_text(f"verus! {{ {source} }}" if wrapper else source)
    graph = RustParser().parse_file(str(path))
    assert set(graph.nodes) == {"local", "local:outer", "local:helper"}
    assert len(graph.edges) == 2
    assert graph.nodes["local:outer"].branches == ()
    assert graph.nodes["local:outer"].cyclomatic_complexity == 1
    assert len(caplog.records) == 1
    assert "inside a function body" in caplog.records[0].message


def test_uninvoked_macro_definition_inside_verus_is_not_diagnosed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "definition.rs"
    path.write_text(
        "verus! { macro_rules! unused { () => { verus!(fn hidden() {}); }; } fn visible() {} }"
    )
    graph = RustParser().parse_file(str(path))
    assert set(graph.nodes) == {"definition", "definition:visible"}
    assert len(graph.edges) == 1
    assert caplog.records == []


def test_external_module_declaration_does_not_create_inline_scope(tmp_path: Path) -> None:
    path = tmp_path / "external.rs"
    path.write_text("mod elsewhere;\nverus! { fn visible() {} }")
    graph = RustParser().parse_file(str(path))
    assert set(graph.nodes) == {"external", "external:visible"}
    assert len(graph.edges) == 1


@pytest.mark.parametrize("delimiters", [("(", ")"), ("[", "]")])
def test_unsupported_top_level_delimiters_warn_without_loading(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, delimiters: tuple[str, str]
) -> None:
    path = tmp_path / "unsupported.rs"
    path.write_text(f"fn host() {{}}\nverus!{delimiters[0]}fn hidden() {{}}{delimiters[1]};")
    with patch.object(verus, "language") as loader:
        graph = RustParser().parse_file(str(path))
    loader.assert_not_called()
    assert set(graph.nodes) == {"unsupported", "unsupported:host"}
    assert len(caplog.records) == 1
    assert "unsupported Verus delimiter" in caplog.records[0].message


def test_nested_module_unsupported_delimiter_does_not_leak(tmp_path: Path) -> None:
    path = tmp_path / "nested.rs"
    path.write_text("verus! { mod a { verus!(fn hidden() {}); fn visible() {} } }")
    graph = RustParser().parse_file(str(path))
    assert set(graph.nodes) == {"nested", "nested.a", "nested.a:visible"}
    assert len(graph.edges) == 2


@pytest.mark.parametrize("placement", ["before", "after"])
def test_raw_macro_definitions_stay_opaque_in_different_positions(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, placement: str
) -> None:
    path = tmp_path / "definitions.rs"
    definition = "macro_rules! unused { () => { verus!(fn hidden() {}); }; }"
    visible = "fn visible() {}"
    source = f"{definition} {visible}" if placement == "before" else f"{visible} {definition}"
    path.write_text(f"verus! {{ {source} }}")
    graph = RustParser().parse_file(str(path))
    assert set(graph.nodes) == {"definitions", "definitions:visible"}
    assert caplog.records == []


def test_qualified_macro_text_is_not_mistaken_for_bare_verus(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "qualified.rs"
    path.write_text("opaque! { custom::verus! { fn hidden() {} } }\nfn visible() {}")
    graph = RustParser().parse_file(str(path))
    assert set(graph.nodes) == {"qualified", "qualified:visible"}
    assert caplog.records == []
