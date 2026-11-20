"""Explicit ECMAScript imports select a module even when exports share a name."""

from pathlib import Path

import pytest

from trailmark.models.edges import EdgeKind
from trailmark.models.nodes import NodeOrigin
from trailmark.parse import parse_directory, parse_file


@pytest.mark.parametrize("language,extension", [("javascript", ".js"), ("typescript", ".ts")])
@pytest.mark.parametrize("alias", [False, True])
def test_named_import_selects_its_explicit_module(
    tmp_path: Path, language: str, extension: str, alias: bool
) -> None:
    (tmp_path / f"selected{extension}").write_text("export function work() {}\n")
    (tmp_path / f"unrelated{extension}").write_text("export function work() {}\n")
    local = "renamed" if alias else "work"
    binding = f"work as {local}" if alias else "work"
    (tmp_path / f"entry{extension}").write_text(
        f"import {{ {binding} }} from './selected';\nexport function entry() {{ {local}(); }}\n"
    )
    graph = parse_directory(str(tmp_path), language)
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    assert len(calls) == 1
    assert calls[0].source_id == "entry:entry"
    assert calls[0].target_id == "selected:work"
    assert calls[0].location is not None and calls[0].location.start_line == 2


@pytest.mark.parametrize("language,extension", [("javascript", ".js"), ("typescript", ".ts")])
def test_missing_import_does_not_bind_to_unrelated_unique_export(
    tmp_path: Path, language: str, extension: str
) -> None:
    (tmp_path / f"unrelated{extension}").write_text("export function work() {}\n")
    (tmp_path / f"entry{extension}").write_text(
        "import { work } from './absent';\nexport function entry() { work(); }\n"
    )
    graph = parse_directory(str(tmp_path), language)
    call = next(edge for edge in graph.edges if edge.kind == EdgeKind.CALLS)
    assert call.target_id != "unrelated:work"
    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY
    assert dict(call.attributes)["import_binding"]


@pytest.mark.parametrize("language,extension", [("javascript", ".js"), ("typescript", ".ts")])
def test_local_parameter_shadows_an_import(tmp_path: Path, language: str, extension: str) -> None:
    (tmp_path / f"selected{extension}").write_text("export function work() {}\n")
    (tmp_path / f"entry{extension}").write_text(
        "import { work } from './selected';\nexport function entry(work) { work(); }\n"
    )
    graph = parse_directory(str(tmp_path), language)
    call = next(edge for edge in graph.edges if edge.kind == EdgeKind.CALLS)
    assert call.target_id != "selected:work"
    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY
    assert dict(call.attributes)["binding_blocked"]


@pytest.mark.parametrize("language,extension", [("javascript", ".js"), ("typescript", ".ts")])
def test_export_alias_selects_the_underlying_declaration(
    tmp_path: Path, language: str, extension: str
) -> None:
    (tmp_path / f"selected{extension}").write_text(
        "function actual() {}\nfunction work() {}\nexport { actual as work };\n"
    )
    (tmp_path / f"entry{extension}").write_text(
        "import { work as invoke } from './selected';\nexport function entry() { invoke(); }\n"
    )
    graph = parse_directory(str(tmp_path), language)
    call = next(edge for edge in graph.edges if edge.kind == EdgeKind.CALLS)
    assert call.target_id == "selected:actual"


@pytest.mark.parametrize("declaration", ["import type { work }", "import { type work }"])
def test_typescript_type_only_import_does_not_create_a_runtime_binding(
    tmp_path: Path, declaration: str
) -> None:
    (tmp_path / "selected.ts").write_text("export function work() {}\n")
    (tmp_path / "entry.ts").write_text(
        f"{declaration} from './selected';\nexport function entry() {{ work(); }}\n"
    )
    graph = parse_directory(str(tmp_path), "typescript")
    call = next(edge for edge in graph.edges if edge.kind == EdgeKind.CALLS)
    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY
    assert dict(call.attributes)["binding_blocked"]


@pytest.mark.parametrize("language,extension", [("javascript", ".js"), ("typescript", ".ts")])
@pytest.mark.parametrize("exports", ["function work() {}", "export { work } from './other';"])
def test_unexported_or_reexported_names_do_not_bind_to_local_decoys(
    tmp_path: Path, language: str, extension: str, exports: str
) -> None:
    (tmp_path / f"selected{extension}").write_text(exports + "\n")
    (tmp_path / f"other{extension}").write_text("export function work() {}\n")
    (tmp_path / f"entry{extension}").write_text(
        "import { work } from './selected';\nexport function entry() { work(); }\n"
    )
    graph = parse_directory(str(tmp_path), language)
    call = next(edge for edge in graph.edges if edge.kind == EdgeKind.CALLS)
    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY
    assert dict(call.attributes)["import_binding"]


@pytest.mark.parametrize("language,extension", [("javascript", ".js"), ("typescript", ".ts")])
def test_nested_function_binding_takes_precedence_over_import(
    tmp_path: Path, language: str, extension: str
) -> None:
    (tmp_path / f"selected{extension}").write_text("export function work() {}\n")
    (tmp_path / f"entry{extension}").write_text(
        "import { work } from './selected';\n"
        "export function entry() { function work() {} work(); }\n"
    )
    graph = parse_directory(str(tmp_path), language)
    call = next(edge for edge in graph.edges if edge.kind == EdgeKind.CALLS)
    assert call.target_id == "entry:entry.work"


@pytest.mark.parametrize("language,extension", [("javascript", ".js"), ("typescript", ".ts")])
def test_named_import_resolves_a_directory_index(
    tmp_path: Path, language: str, extension: str
) -> None:
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / f"index{extension}").write_text("export const work = () => {};\n")
    (tmp_path / f"entry{extension}").write_text(
        "import { work } from './selected';\nexport function entry() { work(); }\n"
    )
    graph = parse_directory(str(tmp_path), language)
    call = next(edge for edge in graph.edges if edge.kind == EdgeKind.CALLS)
    assert call.target_id == "selected.index:work"


def test_typescript_javascript_import_suffix_resolves_its_source(tmp_path: Path) -> None:
    (tmp_path / "selected.ts").write_text("export function work() {}\n")
    (tmp_path / "entry.ts").write_text(
        "import { work } from './selected.js';\nexport function entry() { work(); }\n"
    )
    graph = parse_directory(str(tmp_path), "typescript")
    call = next(edge for edge in graph.edges if edge.kind == EdgeKind.CALLS)
    assert call.target_id == "selected:work"


@pytest.mark.parametrize("language,extension", [("javascript", ".js"), ("typescript", ".ts")])
def test_dotted_source_basename_does_not_select_a_shorter_decoy(
    tmp_path: Path, language: str, extension: str
) -> None:
    (tmp_path / f"selected.v1{extension}").write_text("export function work() {}\n")
    (tmp_path / f"selected{extension}").write_text("export function work() {}\n")
    (tmp_path / f"entry{extension}").write_text(
        "import { work } from './selected.v1';\nexport function entry() { work(); }\n"
    )
    graph = parse_directory(str(tmp_path), language)
    call = next(edge for edge in graph.edges if edge.kind == EdgeKind.CALLS)
    target = graph.nodes[call.target_id]
    assert target.location.file_path == str(tmp_path / f"selected.v1{extension}")


@pytest.mark.parametrize("language,extension", [("javascript", ".js"), ("typescript", ".ts")])
@pytest.mark.parametrize("hidden", ["outside", "vendor", "directory_symlink"])
def test_unselected_import_target_does_not_bind_to_an_inspected_same_stem(
    tmp_path: Path, language: str, extension: str, hidden: str
) -> None:
    selected = tmp_path / "inspected"
    selected.mkdir()
    (selected / f"base{extension}").write_text("export function work() {}\n")
    if hidden == "vendor":
        omitted = selected / "vendor"
        specifier = "./vendor/base"
    else:
        omitted = tmp_path / "outside"
        specifier = "../outside/base"
    omitted.mkdir()
    (omitted / f"base{extension}").write_text("export function work() {}\n")
    if hidden == "directory_symlink":
        (selected / "linked").symlink_to(omitted, target_is_directory=True)
        specifier = "./linked/base"
    (selected / f"entry{extension}").write_text(
        f"import {{ work }} from '{specifier}';\nexport function entry() {{ work(); }}\n"
    )
    graph = parse_directory(str(selected), language)
    call = next(edge for edge in graph.edges if edge.kind == EdgeKind.CALLS)
    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY
    assert call.target_id != "base:work"


def test_typescript_selection_does_not_bind_an_excluded_javascript_import(tmp_path: Path) -> None:
    hidden = tmp_path / "hidden"
    hidden.mkdir()
    (hidden / "base.js").write_text("export function work() {}\n")
    (tmp_path / "base.ts").write_text("export function work() {}\n")
    (tmp_path / "entry.ts").write_text(
        "import { work } from './hidden/base.js';\nexport function entry() { work(); }\n"
    )
    graph = parse_directory(str(tmp_path), "typescript")
    call = next(edge for edge in graph.edges if edge.kind == EdgeKind.CALLS)
    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY
    assert call.target_id != "base:work"


def test_single_file_import_has_no_selected_target_context(tmp_path: Path) -> None:
    (tmp_path / "base.js").write_text("export function work() {}\n")
    entry = tmp_path / "entry.js"
    entry.write_text("import { work } from './base';\nexport function entry() { work(); }\n")
    graph = parse_file(str(entry), "javascript")
    call = next(edge for edge in graph.edges if edge.kind == EdgeKind.CALLS)
    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY
    assert dict(call.attributes)["import_binding"]


def test_mixed_selection_uses_the_targets_planned_language_and_identity(tmp_path: Path) -> None:
    (tmp_path / "base.js").write_text("export function work() {}\n")
    (tmp_path / "base.ts").write_text("export function work() {}\n")
    (tmp_path / "entry.ts").write_text(
        "import { work } from './base.js';\nexport function entry() { work(); }\n"
    )
    graph = parse_directory(str(tmp_path), "typescript,javascript")
    call = next(edge for edge in graph.edges if edge.kind == EdgeKind.CALLS)
    target = graph.nodes[call.target_id]
    assert target.location.file_path == str(tmp_path / "base.js")
    assert target.origin == NodeOrigin.SOURCE
