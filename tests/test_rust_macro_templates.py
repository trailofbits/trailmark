"""Macro templates retain source navigation separately from executable code."""

from pathlib import Path

import pytest

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.nodes import NodeKind, NodeOrigin
from trailmark.parse import parse_file
from trailmark.query.api import QueryEngine


def test_literal_function_template_has_its_own_source_dependencies(tmp_path: Path) -> None:
    source = tmp_path / "literal.rs"
    source.write_text(
        "fn helper() {}\nmacro_rules! make { () => { fn generated() { helper(); } }; }\nmake!();\n"
    )
    graph = parse_file(str(source), "rust", strict=True)
    generated = next(n for n in graph.nodes.values() if n.name == "generated")
    assert generated.kind == NodeKind.TEMPLATE
    assert dict(generated.attributes)["executable"] is False
    assert generated.location.start_line == 2
    refs = [e for e in graph.edges if e.kind == EdgeKind.REFERENCES]
    assert len(refs) == 1
    assert (refs[0].source_id, refs[0].target_id) == (generated.id, "literal:helper")
    assert refs[0].confidence == EdgeConfidence.UNCERTAIN
    assert refs[0].location and refs[0].location.start_line == 2
    assert not any(e.kind == EdgeKind.CALLS for e in graph.edges)
    engine = QueryEngine.from_graph(graph)
    assert engine.paths_between(generated.id, "literal:helper", edge_kinds=(EdgeKind.REFERENCES,))


def test_macro_arms_keep_distinct_declarations_and_do_not_export_functions(tmp_path: Path) -> None:
    source = tmp_path / "arms.rs"
    source.write_text(
        "fn helper() {}\nmacro_rules! make {\n"
        " (one) => { fn generated() { helper(); } };\n"
        " (two) => { fn generated() { helper(); } };\n}\n"
        "fn ordinary() { generated(); }\n"
    )
    graph = parse_file(str(source), "rust", strict=True)
    templates = [n for n in graph.nodes.values() if n.name == "generated"]
    assert len(templates) == 2
    assert len({n.id for n in templates}) == 2
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 1
    assert calls[0].source_id == "arms:ordinary"
    assert graph.nodes[calls[0].target_id].origin == NodeOrigin.PROXY


def test_metavariable_impl_template_retains_method_and_global_helper(tmp_path: Path) -> None:
    source = tmp_path / "methods.rs"
    source.write_text(
        "fn helper() {}\nmacro_rules! make {\n"
        " ($T:ty) => { impl $T { fn work(&self) { helper(); } } };\n}\n"
    )
    graph = parse_file(str(source), "rust", strict=True)
    work = next(n for n in graph.nodes.values() if n.name == "work")
    assert work.kind == NodeKind.TEMPLATE
    assert any(
        e.kind == EdgeKind.REFERENCES and e.source_id == work.id and e.target_id == "methods:helper"
        for e in graph.edges
    )
    assert not any(e.kind == EdgeKind.CALLS for e in graph.edges)


def test_metavariable_callee_does_not_bind_a_literal_namesake(tmp_path: Path) -> None:
    source = tmp_path / "dynamic.rs"
    source.write_text(
        "fn target() {}\nmacro_rules! make {\n"
        " ($target:ident) => { fn generated() { $target(); } };\n}\n"
    )
    graph = parse_file(str(source), "rust", strict=True)
    refs = [e for e in graph.edges if e.kind == EdgeKind.REFERENCES]
    assert len(refs) == 1
    assert graph.nodes[refs[0].target_id].origin == NodeOrigin.PROXY
    assert refs[0].target_id != "dynamic:target"
    assert refs[0].location
    line = source.read_text().splitlines()[refs[0].location.start_line - 1]
    assert line[refs[0].location.start_col : refs[0].location.end_col] == "$target()"


def test_template_type_names_cannot_supply_real_type_uses(tmp_path: Path) -> None:
    source = tmp_path / "types.rs"
    source.write_text(
        "macro_rules! make { () => { struct Generated; }; }\nfn ordinary(value: Generated) {}\n"
    )
    graph = parse_file(str(source), "rust", strict=True)
    generated = next(n for n in graph.nodes.values() if n.name == "Generated")
    assert generated.kind == NodeKind.TEMPLATE
    assert not any(
        e.kind == EdgeKind.TYPE_USES and e.target_id == generated.id for e in graph.edges
    )


def test_repetition_remains_an_opaque_template_with_a_diagnostic(tmp_path: Path) -> None:
    source = tmp_path / "repeated.rs"
    source.write_text("macro_rules! make { ($($name:ident),*) => { $(fn $name() {})* }; }\n")
    graph = parse_file(str(source), "rust")
    assert any(n.kind == NodeKind.TEMPLATE and n.name == "make" for n in graph.nodes.values())
    assert graph.parse_diagnostics
    assert not any(e.kind == EdgeKind.CALLS for e in graph.edges)


def test_sibling_literal_template_functions_have_concrete_source_relationships(
    tmp_path: Path,
) -> None:
    source = tmp_path / "siblings.rs"
    source.write_text(
        "macro_rules! make { () => { fn helper() {} fn generated() { helper(); } }; }\n"
    )
    graph = parse_file(str(source), "rust", strict=True)
    units = {n.name: n for n in graph.nodes.values()}
    assert any(
        e.kind == EdgeKind.REFERENCES
        and e.source_id == units["generated"].id
        and e.target_id == units["helper"].id
        for e in graph.edges
    )


def test_inline_module_owns_its_macro_and_source_dependencies(tmp_path: Path) -> None:
    source = tmp_path / "nested.rs"
    source.write_text(
        "mod inner { fn helper() {}\n"
        "macro_rules! make { () => { fn generated() { helper(); } }; }\n}\n"
    )
    graph = parse_file(str(source), "rust", strict=True)
    macro = next(n for n in graph.nodes.values() if n.name == "make")
    generated = next(n for n in graph.nodes.values() if n.name == "generated")
    assert any(
        e.kind == EdgeKind.CONTAINS and e.source_id == "nested.inner" and e.target_id == macro.id
        for e in graph.edges
    )
    assert any(
        e.kind == EdgeKind.REFERENCES
        and e.source_id == generated.id
        and e.target_id == "nested.inner:helper"
        for e in graph.edges
    )


def test_metavariable_declaration_cannot_supply_a_literal_target(tmp_path: Path) -> None:
    source = tmp_path / "names.rs"
    source.write_text(
        "macro_rules! make { ($target:ident) => {\n"
        "fn $target() {} fn generated() { target(); } }; }\n"
    )
    graph = parse_file(str(source), "rust", strict=True)
    placeholder = next(n for n in graph.nodes.values() if n.name == "$target")
    refs = [e for e in graph.edges if e.kind == EdgeKind.REFERENCES]
    assert len(refs) == 1
    assert refs[0].target_id != placeholder.id
    assert graph.nodes[refs[0].target_id].origin == NodeOrigin.PROXY


def test_empty_arm_and_function_local_macro_do_not_add_runtime_edges(tmp_path: Path) -> None:
    source = tmp_path / "local.rs"
    source.write_text("fn outer() { macro_rules! empty { () => {}; } }\n")
    graph = parse_file(str(source), "rust", strict=True)
    macro = next(n for n in graph.nodes.values() if n.name == "empty")
    assert any(
        e.kind == EdgeKind.CONTAINS and e.source_id == "local:outer" and e.target_id == macro.id
        for e in graph.edges
    )
    assert not any(e.kind == EdgeKind.CALLS for e in graph.edges)


def test_function_local_macro_can_reference_an_enclosing_helper_item(tmp_path: Path) -> None:
    source = tmp_path / "lexical.rs"
    source.write_text(
        "fn outer() { fn helper() {}\n"
        "macro_rules! make { () => { fn generated() { helper(); } }; }\n}\n"
    )
    graph = parse_file(str(source), "rust", strict=True)
    generated = next(n for n in graph.nodes.values() if n.name == "generated")
    assert any(
        e.kind == EdgeKind.REFERENCES
        and e.source_id == generated.id
        and e.target_id == "lexical:outer.helper"
        for e in graph.edges
    )


@pytest.mark.parametrize("binding", ["helper: fn()", ""])
def test_enclosing_values_block_unrelated_global_template_dependencies(
    tmp_path: Path, binding: str
) -> None:
    source = tmp_path / "shadow.rs"
    source.write_text(
        "fn helper() {} fn outer("
        + binding
        + ") {"
        + ("let helper = || {};" if not binding else "")
        + "macro_rules! make { () => { fn generated() { helper(); } }; } }"
    )
    graph = parse_file(str(source), "rust", strict=True)
    ref = next(e for e in graph.edges if e.kind == EdgeKind.REFERENCES)
    assert graph.nodes[ref.target_id].origin == NodeOrigin.PROXY
    assert ref.target_id != "shadow:helper"


def test_literal_template_helper_precedes_an_enclosing_value_binding(tmp_path: Path) -> None:
    source = tmp_path / "inner.rs"
    source.write_text(
        "fn outer(helper: fn()) { macro_rules! make { () => {"
        "fn helper() {} fn generated() { helper(); } }; } }"
    )
    graph = parse_file(str(source), "rust", strict=True)
    helper = next(n for n in graph.nodes.values() if n.name == "helper")
    assert any(e.kind == EdgeKind.REFERENCES and e.target_id == helper.id for e in graph.edges)
