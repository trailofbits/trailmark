"""Cross-file binding, recovered syntax, and frontend ownership regressions."""

from __future__ import annotations

from pathlib import Path

import pytest

from trailmark.models import CodeEdge, CodeGraph, EdgeConfidence, EdgeKind
from trailmark.parse import parse_directory, parse_file


def _calls(graph: CodeGraph, source: str) -> list[CodeEdge]:
    return [e for e in graph.edges if e.kind == EdgeKind.CALLS and e.source_id == source]


def test_move_error_does_not_poison_unrelated_calls(tmp_path: Path) -> None:
    path = tmp_path / "err.move"
    path.write_text(
        "module 0x1::m { fun a() { b(); c(); } fun b() {} fun c() {} fun broken() { let x = ; } }"
    )
    graph = parse_file(str(path))
    calls = _calls(graph, "err:0x1::m:a")
    assert {(e.target_id, e.confidence) for e in calls} == {
        ("err:0x1::m:b", EdgeConfidence.CERTAIN),
        ("err:0x1::m:c", EdgeConfidence.CERTAIN),
    }
    assert all(not dict(e.attributes).get("binding_blocked") for e in calls)
    assert graph.parse_diagnostics


def test_move_aptos_syntax_and_positioned_shadowing(tmp_path: Path) -> None:
    path = tmp_path / "bank.move"
    path.write_text(
        "module 0x1::bank { struct Account has key { value: u64 } f"
        "un total(p: u64): u64 { p } package fun withdraw(n: u64) a"
        "cquires Account { let total = total(n); for (i in 0..n) { "
        "let x = i; x += 1; }; total(); } }"
    )
    graph = parse_file(str(path), strict=True)
    calls = _calls(graph, "bank:0x1::bank:withdraw")
    assert calls[0].target_id == "bank:0x1::bank:total"
    assert calls[0].confidence == EdgeConfidence.CERTAIN
    assert dict(calls[1].attributes)["binding_blocked"] is True
    assert calls[1].confidence == EdgeConfidence.UNCERTAIN


def test_move_qualified_generic_links_and_import_edges(tmp_path: Path) -> None:
    (tmp_path / "lib.move").write_text("module 0x43::library { public fun make<T>() {} }")
    (tmp_path / "app.move").write_text(
        "module 0x42::app { use 0x43::library; fun run() { library::make<u64>(); } }"
    )
    graph = parse_directory(str(tmp_path), "move", strict=True)
    calls = _calls(graph, "app:0x42::app:run")
    assert [(e.target_id, e.confidence) for e in calls] == [
        ("lib:0x43::library:make", EdgeConfidence.INFERRED)
    ]
    assert any(e.kind == EdgeKind.IMPORTS and e.source_id == "app:0x42::app" for e in graph.edges)


def test_haskell_do_bind_expressions_keep_calls(tmp_path: Path) -> None:
    path = tmp_path / "D.hs"
    path.write_text(
        "module D where\nfetch r = r\nvalidate x = x\nstore x = pure x"
        "\nhandler req = do\n raw <- fetch req\n ok <- validate raw\n s"
        "tore ok\n"
    )
    graph = parse_file(str(path), strict=True)
    assert {e.target_id for e in _calls(graph, "D:handler")} == {"D:fetch", "D:validate", "D:store"}


def test_haskell_operator_proxies_are_readable(tmp_path: Path) -> None:
    path = tmp_path / "Ops.hs"
    path.write_text("module Ops where\nadd x y = x + y\nconcat x y = x ++ y\n")
    graph = parse_file(str(path), strict=True)
    assert _calls(graph, "Ops:add")[0].target_id.endswith("$operator.plus")
    assert _calls(graph, "Ops:concat")[0].target_id.endswith("$operator.plus_plus")


def test_php_namespace_linking_and_global_qualification(tmp_path: Path) -> None:
    (tmp_path / "controller.php").write_text(
        "<?php namespace App; function route($x) { return sanitize($x); }"
    )
    (tmp_path / "helpers.php").write_text(
        "<?php namespace App; function sanitize($x) { return $x; }"
    )
    (tmp_path / "legacy.php").write_text(
        "<?php function legacy($x) { return \\App\\sanitize($x); }"
    )
    graph = parse_directory(str(tmp_path), "php", strict=True)
    for caller in ["controller:App.route", "legacy:legacy"]:
        calls = _calls(graph, caller)
        assert [(e.target_id, e.confidence) for e in calls] == [
            ("helpers:App.sanitize", EdgeConfidence.INFERRED)
        ]


def test_php_namespaced_builtin_falls_back_to_global(tmp_path: Path) -> None:
    path = tmp_path / "example.php"
    path.write_text("<?php namespace App; function fraction($x) { return intdiv($x, 2); }")
    graph = parse_file(str(path), strict=True)
    assert _calls(graph, "example:App.fraction")[0].target_id.endswith(":example:intdiv")


def test_ruby_inherited_and_global_calls_stay_linkable(tmp_path: Path) -> None:
    (tmp_path / "animal.rb").write_text(
        "def top_helper; end\nclass Animal\n def describe; end\nend\n"
    )
    (tmp_path / "dog.rb").write_text(
        "class Dog < Animal\n def bark\n top_helper\n describe\n raise "
        "'x'\n super\n Integer('1')\n end\nend\n"
    )
    graph = parse_directory(str(tmp_path), "ruby", strict=True)
    calls = _calls(graph, "dog:Dog.bark")
    targets = {e.target_id for e in calls}
    assert {"animal:top_helper", "animal:Animal.describe"} <= targets
    assert all(not dict(e.attributes).get("binding_blocked") for e in calls)
    assert not any("Dog.raise" in t or "Dog.super" in t or "Dog.Integer" in t for t in targets)


def test_ruby_binding_forms_are_values(tmp_path: Path) -> None:
    path = tmp_path / "bindings.rb"
    path.write_text(
        "def handle(items)\n a,b=items\n begin\n work()\n rescue => e\n "
        "e\n end\n for x in items\n x\n end\n a\n b\nend\n"
    )
    graph = parse_file(str(path), strict=True)
    calls = _calls(graph, "bindings:handle")
    assert len(calls) == 1
    assert calls[0].target_id.endswith(":bindings:work")


@pytest.mark.parametrize(
    ("extension", "depth", "opening", "closing"),
    [
        ("rb", 600, "module X\n", "end\n"),
        ("kt", 3000, "class X {\n", "}\n"),
        ("swift", 3000, "class X {\n", "}\n"),
    ],
)
def test_deep_declarations_report_limit_without_crashing(
    tmp_path: Path, extension: str, depth: int, opening: str, closing: str
) -> None:
    path = tmp_path / f"deep.{extension}"
    path.write_text(opening * depth + closing * depth)
    graph = parse_file(str(path))
    assert any(
        d.kind == "unsupported_syntax" and "nesting" in d.message for d in graph.parse_diagnostics
    )


def test_dart_initializers_keep_helper_calls(tmp_path: Path) -> None:
    path = tmp_path / "ctor.dart"
    path.write_text(
        "int helper(int i) => i; class B { B(int i); } class A exte"
        "nds B { int v; A(int x) : v = helper(x), super(helper(2));"
        " A.redirect() : this(helper(3)); }"
    )
    graph = parse_file(str(path), strict=True)
    assert sum(e.target_id == "ctor:helper" for e in _calls(graph, "ctor:A.A")) == 2
    assert sum(e.target_id == "ctor:helper" for e in _calls(graph, "ctor:A.A.redirect")) == 1


def test_sql_replaced_overload_has_only_new_dependencies(tmp_path: Path) -> None:
    path = tmp_path / "db.sql"
    path.write_text(
        "CREATE TABLE app.users(id int);\nCREATE TABLE app.orders(id"
        " int);\nCREATE FUNCTION app.lookup(id int) RETURNS int LANG"
        "UAGE sql AS $$ SELECT id FROM app.users; $$;\nCREATE FUNCTI"
        "ON app.lookup(id text) RETURNS text LANGUAGE sql AS $$ SEL"
        "ECT id FROM app.users; $$;\nCREATE OR REPLACE FUNCTION app."
        "lookup(id int) RETURNS int LANGUAGE sql AS $$ SELECT id FR"
        "OM app.orders; $$;\n"
    )
    graph = parse_file(str(path))
    replaced = [
        u for u in graph.nodes.values() if u.name == "lookup" and u.location.start_line == 5
    ]
    assert len(replaced) == 1
    unit = replaced[0]
    dependencies = [
        e.target_id
        for e in graph.edges
        if e.source_id == unit.id and e.kind == EdgeKind.CORRESPONDS_TO
    ]
    assert dependencies == ["db:app.orders"]
    assert sum(e.kind == EdgeKind.CONTAINS and e.target_id == unit.id for e in graph.edges) == 1


def test_objc_self_selector_can_link_in_same_implementation(tmp_path: Path) -> None:
    path = tmp_path / "Worker.m"
    path.write_text(
        "@implementation Worker\n- (int)validate:(int)x { return x; "
        "}\n- (int)run:(int)x { return [self validate:x]; }\n@end\n"
    )
    graph = parse_file(str(path), strict=True)
    calls = _calls(graph, "Worker:Worker.run:")
    assert [(e.target_id, e.confidence) for e in calls] == [
        ("Worker:Worker.validate:", EdgeConfidence.INFERRED)
    ]


def test_circom_same_file_calls_stay_certain_with_include(tmp_path: Path) -> None:
    (tmp_path / "dep.circom").write_text("template Other() {}")
    (tmp_path / "app.circom").write_text(
        'include "dep.circom"; template Local() {} template Main() '
        "{ component a = Local(); component b = Other(); }"
    )
    graph = parse_directory(str(tmp_path), "circom", strict=True)
    calls = {e.target_id: e.confidence for e in _calls(graph, "app:Main")}
    assert calls == {"app:Local": EdgeConfidence.CERTAIN, "dep:Other": EdgeConfidence.INFERRED}
