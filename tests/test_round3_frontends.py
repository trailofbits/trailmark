"""Regression coverage for callable ownership and bounded extraction."""

from pathlib import Path
from time import perf_counter

import pytest

from trailmark.models import EdgeKind, NodeKind
from trailmark.parse import parse_directory, parse_file


@pytest.mark.parametrize("extension", ["php", "java"])
@pytest.mark.parametrize("depth", [4, 12, 20, 100])
def test_expression_lambdas_are_extracted_once(tmp_path: Path, extension: str, depth: int) -> None:
    expression = ("fn() => " if extension == "php" else "() -> ") * depth + "x()"
    source = (
        "<?php function run(){ return " + expression + "; }"
        if extension == "php"
        else "class A { Object run(){ return " + expression + "; } }"
    )
    path = tmp_path / f"a.{extension}"
    path.write_text(source)
    start = perf_counter()
    graph = parse_file(str(path))
    assert perf_counter() - start < 1
    lambdas = [u for u in graph.nodes.values() if u.name.startswith("$lambda")]
    assert len(lambdas) == min(depth, 47 if extension == "php" else 46)
    calls = [
        e for e in graph.edges if e.kind == EdgeKind.CALLS and e.target_id.endswith((":x", ".x"))
    ]
    assert len(calls) == (1 if depth < 48 else 0)
    assert bool(graph.parse_diagnostics) == (depth >= 48)


def test_typescript_abstract_class_owns_methods(tmp_path: Path) -> None:
    (tmp_path / "h.ts").write_text(
        "export abstract class Handler { step(d:number){return d;} "
        "handle(){return [1].map(x=>this.step(x));} }"
    )
    (tmp_path / "user.ts").write_text("export function run(){return step(1);}")
    graph = parse_directory(str(tmp_path), "typescript")
    for name in ("step", "handle"):
        assert graph.nodes[f"h:Handler.{name}"].kind == NodeKind.METHOD
    assert any(e.target_id == "h:Handler.step" and "$lambda" in e.source_id for e in graph.edges)
    assert not any(e.source_id == "user:run" and e.target_id.startswith("h:") for e in graph.edges)


@pytest.mark.parametrize("extension", ["js", "ts"])
@pytest.mark.parametrize("declaration", ["return class extends base", "class Inner"])
def test_unextracted_class_has_no_enclosing_this(
    tmp_path: Path, extension: str, declaration: str
) -> None:
    path = tmp_path / f"a.{extension}"
    path.write_text(
        "function factory(base){ function render(){} "
        + declaration
        + " { mount(){ return [1].map(()=>this.render()) } } }"
    )
    graph = parse_file(str(path))
    assert not any(
        e.target_id == "a:factory.render" and "$lambda" in e.source_id for e in graph.edges
    )


@pytest.mark.parametrize("extension", ["js", "ts"])
def test_class_field_arrows_are_methods(tmp_path: Path, extension: str) -> None:
    path = tmp_path / f"a.{extension}"
    path.write_text("class Handler { onData=()=>read(); static make=()=>create(); }")
    graph = parse_file(str(path))
    assert graph.nodes["a:Handler.onData"].kind == NodeKind.METHOD
    assert graph.nodes["a:Handler.make"].kind == NodeKind.METHOD


def test_php_large_class_uses_constant_time_namespace_lookup(tmp_path: Path) -> None:
    path = tmp_path / "big.php"
    path.write_text(
        "<?php namespace App; class A {\n"
        + "".join(f"function f{i}() {{\n x();\n}}\n" for i in range(6000))
        + "}"
    )
    start = perf_counter()
    graph = parse_file(str(path))
    assert perf_counter() - start < 1
    assert "big:App.A.f5999" in graph.nodes


def test_unbound_constructor_proxies_use_each_overload_id(tmp_path: Path) -> None:
    path = tmp_path / "a.java"
    path.write_text("class Repo { Repo(){this(1);} Repo(int x){this();} }")
    graph = parse_file(str(path))
    calls = [e for e in graph.edges if dict(e.attributes).get("constructor_type") == "this"]
    assert len(calls) == 2
    assert len({e.target_id for e in calls}) == 2
    assert all(
        dict(e.attributes)["raw_target"] == f"{e.source_id}.$constructor.this" for e in calls
    )


def test_move_address_block_import_registers_module_aliases(tmp_path: Path) -> None:
    path = tmp_path / "a.move"
    path.write_text(
        "module 0x2::pkg { use 0x1::{math, vector}; "
        "fun run(){math::max(1, 2); vector::empty<u64>();} }"
    )
    graph = parse_file(str(path))
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert {dict(e.attributes).get("raw_target", e.target_id) for e in calls} == {
        "0x1::math::max",
        "0x1::vector::empty",
    }
    assert all(e.confidence.value == "inferred" for e in calls)
