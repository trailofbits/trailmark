"""Qualified calls navigate to visible local implementation declarations."""

from pathlib import Path

import pytest

from trailmark.models.edges import EdgeKind
from trailmark.parse import parse_file


@pytest.mark.mutation_fast
def test_qualified_impl_calls_resolve_through_super_imports(tmp_path: Path) -> None:
    source = tmp_path / "packing.cairo"
    source.write_text(
        "trait Packing { fn pack(value: u128) -> u128; }\n"
        "impl StorePacking of Packing { fn pack(value: u128) -> u128 { value } }\n"
        "mod tests {\n"
        "  use super::{Packing, StorePacking};\n"
        "  fn run() -> u128 { StorePacking::pack(1) }\n"
        "}\n"
    )

    graph = parse_file(str(source), language="cairo", strict=True)
    call = next(edge for edge in graph.edges if edge.kind == EdgeKind.CALLS)

    assert (call.source_id, call.target_id) == ("packing:tests.run", "packing:StorePacking.pack")
    assert call.location is not None
    assert call.location.start_line == 5


@pytest.mark.mutation_fast
def test_qualified_calls_respect_import_alias_shadowing(tmp_path: Path) -> None:
    source = tmp_path / "packing.cairo"
    source.write_text(
        "trait Packing { fn pack(value: u128) -> u128; }\n"
        "impl StorePacking of Packing { fn pack(value: u128) -> u128 { value } }\n"
        "mod tests {\n"
        "  use external::Packing as StorePacking;\n"
        "  fn run() -> u128 { StorePacking::pack(1) }\n"
        "}\n"
    )

    graph = parse_file(str(source), language="cairo", strict=True)
    call = next(edge for edge in graph.edges if edge.kind == EdgeKind.CALLS)

    assert call.target_id != "packing:StorePacking.pack"
    assert graph.nodes[call.target_id].origin.value == "proxy"


@pytest.mark.mutation_fast
@pytest.mark.parametrize("parameters", ["StorePacking", "T, impl StorePacking: Packing<T>"])
def test_generic_path_heads_do_not_bind_same_name_local_impl(
    tmp_path: Path, parameters: str
) -> None:
    source = tmp_path / "packing.cairo"
    source.write_text(
        "trait Packing<T> { fn pack(value: T) -> T; }\n"
        "impl StorePacking of Packing<u128> { fn pack(value: u128) -> u128 { value } }\n"
        f"fn run<{parameters}>() {{ StorePacking::pack(1); }}\n"
    )

    graph = parse_file(str(source), language="cairo", strict=True)
    call = next(edge for edge in graph.edges if edge.kind == EdgeKind.CALLS)

    assert call.target_id != "packing:StorePacking.pack"
    assert graph.nodes[call.target_id].origin.value == "proxy"


@pytest.mark.mutation_fast
def test_function_local_imports_do_not_leak_to_sibling_functions(tmp_path: Path) -> None:
    source = tmp_path / "scopes.cairo"
    source.write_text(
        "mod target { fn helper() {} }\n"
        "mod decoy { fn helper() {} }\n"
        "use target as Selected;\n"
        "fn first() { use decoy as Selected; Selected::helper(); }\n"
        "fn second() { Selected::helper(); }\n"
    )

    graph = parse_file(str(source), language="cairo", strict=True)

    assert {
        (edge.source_id, edge.target_id) for edge in graph.edges if edge.kind == EdgeKind.CALLS
    } == {("scopes:first", "scopes:decoy.helper"), ("scopes:second", "scopes:target.helper")}


def test_child_modules_require_explicit_parent_paths_or_imports(tmp_path: Path) -> None:
    source = tmp_path / "modules.cairo"
    source.write_text(
        "mod target { fn helper() {} }\n"
        "use target as Selected;\n"
        "mod child {\n"
        "  fn inherited_alias() { Selected::helper(); }\n"
        "  fn inherited_item() { target::helper(); }\n"
        "  fn explicit_parent() { super::target::helper(); }\n"
        "}\n"
    )

    graph = parse_file(str(source), language="cairo", strict=True)
    calls = {edge.source_id: edge.target_id for edge in graph.edges if edge.kind == EdgeKind.CALLS}

    assert calls["modules:child.explicit_parent"] == "modules:target.helper"
    assert graph.nodes[calls["modules:child.inherited_alias"]].origin.value == "proxy"
    assert graph.nodes[calls["modules:child.inherited_item"]].origin.value == "proxy"
