"""Class evaluation and merged TypeScript containers retain their lexical owners."""

from pathlib import Path

import pytest

from trailmark.models import EdgeKind
from trailmark.parse import parse_file


@pytest.mark.parametrize("extension", ["js", "ts"])
def test_local_class_evaluation_keeps_calls(tmp_path: Path, extension: str) -> None:
    path = tmp_path / f"app.{extension}"
    path.write_text(
        "function helper(){} function Mixin(B){return B;}\n"
        "function run(){class K extends Mixin(Base){\n"
        " x=helper(); static y=helper(); static {helper();} [helper()](){}\n}}"
    )
    graph = parse_file(str(path))
    calls = [
        e
        for e in graph.edges
        if e.kind == EdgeKind.CALLS
        and e.source_id == "app:run"
        and not dict(e.attributes).get("deferred")
    ]
    assert len(calls) == 5
    assert sum(e.target_id == "app:helper" for e in calls) == 4
    assert sum(e.target_id == "app:Mixin" for e in calls) == 1


@pytest.mark.parametrize("extension", ["js", "ts"])
def test_mixin_class_field_evaluation_keeps_calls(tmp_path: Path, extension: str) -> None:
    path = tmp_path / f"app.{extension}"
    path.write_text("function helper(){} const Mix=(B)=>class extends B {x=helper();};")
    graph = parse_file(str(path))
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 1 and calls[0].target_id == "app:helper"
    assert calls[0].source_id == "app:Mix"


@pytest.mark.parametrize("keyword", ["namespace", "module"])
def test_repeated_typescript_namespace_blocks_merge(tmp_path: Path, keyword: str) -> None:
    path = tmp_path / "app.ts"
    path.write_text(
        f"{keyword} NS {{export function util(){{return 1;}}}}\n"
        f"{keyword} NS {{export function util2(){{return util();}}}}"
    )
    graph = parse_file(str(path))
    assert {u.id for u in graph.nodes.values() if u.name == "NS"} == {"app:NS"}
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)
    assert (call.source_id, call.target_id, call.confidence.value) == (
        "app:NS.util2",
        "app:NS.util",
        "certain",
    )


@pytest.mark.parametrize("declaration", ["class Bar {}", "function Bar(){}"])
def test_namespace_merges_with_class_or_function(tmp_path: Path, declaration: str) -> None:
    path = tmp_path / "app.ts"
    path.write_text(declaration + "\nnamespace Bar {export function make(){return Bar();}}")
    graph = parse_file(str(path))
    expected = "app:Bar.$namespace.make" if declaration.startswith("function") else "app:Bar.make"
    assert expected in graph.nodes
    assert not any(u.name == "Bar" and "@" in u.id for u in graph.nodes.values())


def test_string_named_typescript_module_owns_functions(tmp_path: Path) -> None:
    path = tmp_path / "app.ts"
    path.write_text('declare module "legacy" {export function helper():void; }')
    graph = parse_file(str(path))
    assert "app:legacy" in graph.nodes
    assert not any(u.name == "helper" for u in graph.nodes.values())
