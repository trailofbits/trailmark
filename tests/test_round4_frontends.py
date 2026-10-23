"""Nested classes and callable bodies retain their own graph owners."""

from pathlib import Path
from time import perf_counter

import pytest

from trailmark.models import EdgeKind, NodeKind
from trailmark.parse import parse_directory, parse_file


@pytest.mark.parametrize("extension", ["js", "ts"])
@pytest.mark.parametrize("shape", ["default", "mixin", "variable"])
def test_unextracted_class_members_never_become_free_functions(
    tmp_path: Path, extension: str, shape: str
) -> None:
    source = {
        "default": "export default class extends Component { save(){ return 1; } }",
        "mixin": "function mixin(base){ return class extends base { save(){return 1;} }; }",
        "variable": "const Widget = class { save(){return 1;} };",
    }[shape]
    (tmp_path / f"comp.{extension}").write_text(source)
    (tmp_path / f"other.{extension}").write_text("function main(){ return save(); }")
    graph = parse_directory(str(tmp_path), "auto")
    methods = [u for u in graph.nodes.values() if u.name == "save"]
    assert len(methods) == 1 and methods[0].kind == NodeKind.METHOD
    assert methods[0].id.count(".") >= 1
    assert any(e.kind == EdgeKind.CONTAINS and e.target_id == methods[0].id for e in graph.edges)
    assert not any(
        e.kind == EdgeKind.CALLS and e.source_id == "other:main" and e.target_id == methods[0].id
        for e in graph.edges
    )


@pytest.mark.parametrize("namespace", [False, True])
def test_nested_typescript_class_has_qualified_methods(tmp_path: Path, namespace: bool) -> None:
    path = tmp_path / "app.ts"
    prefix = "namespace NS " if namespace else ""
    path.write_text(
        prefix + "{class Inner { go(){} }}\nfunction go(){}\nexport function main(){return go();}\n"
    )
    graph = parse_file(str(path))
    methods = [u for u in graph.nodes.values() if u.name == "go" and u.kind == NodeKind.METHOD]
    assert len(methods) == 1
    if namespace:
        assert methods[0].id == "app:NS.Inner.go"
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS and e.source_id == "app:main")
    assert call.target_id == "app:go" and call.confidence.value == "certain"


@pytest.mark.parametrize("nested", [False, True])
def test_csharp_delegate_keeps_body_calls(tmp_path: Path, nested: bool) -> None:
    path = tmp_path / "app.cs"
    expression = (
        "delegate { System.Action b = delegate { Y(); }; }" if nested else "delegate { Y(); }"
    )
    path.write_text("class K { void M(){ System.Action a = " + expression + "; } void Y(){} }")
    graph = parse_file(str(path))
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS and e.target_id == "app:K.Y"]
    assert len(calls) == 1 and "$lambda" in calls[0].source_id
    assert calls[0].source_id.count("$lambda") == (2 if nested else 1)


@pytest.mark.parametrize("extension", ["cs", "dart"])
def test_curried_lambda_has_one_innermost_call(tmp_path: Path, extension: str) -> None:
    path = tmp_path / f"app.{extension}"
    path.write_text(
        "class K { void M(){ var x = a => b => d => X(); } void X(){} }"
        if extension == "cs"
        else "Function c() => () => () => x();"
    )
    graph = parse_file(str(path))
    name = "X" if extension == "cs" else "x"
    calls = [
        e
        for e in graph.edges
        if e.kind == EdgeKind.CALLS and e.target_id.endswith((":" + name, "." + name))
    ]
    assert len(calls) == 1
    assert calls[0].source_id.count("$lambda") == (3 if extension == "cs" else 2)


@pytest.mark.parametrize("expression", [False, True])
def test_deep_typescript_classes_are_bounded(tmp_path: Path, expression: bool) -> None:
    path = tmp_path / "app.ts"
    opening = "const A = class { m(){" if expression else "class A { m(){"
    closing = "}};" if expression else "}}"
    path.write_text("function run(){" + opening * 3000 + "work();" + closing * 3000 + "}")
    start = perf_counter()
    graph = parse_file(str(path))
    assert perf_counter() - start < 2
    assert graph.parse_diagnostics and len(graph.nodes) < 150


def test_missing_move_module_has_consistent_confidence(tmp_path: Path) -> None:
    path = tmp_path / "app.move"
    path.write_text(
        "module app::m { use std::vector; fun run(xs: vector<u64>){"
        "vector::length(&xs); std::vector::length(&xs);} }"
    )
    graph = parse_file(str(path))
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 2 and len({e.target_id for e in calls}) == 1
    assert all(e.confidence.value == "uncertain" for e in calls)
