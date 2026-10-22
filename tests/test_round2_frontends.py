"""Cross-file linking, bounded extraction, and macro confidence regressions."""

from pathlib import Path

import pytest

from trailmark.analysis._sarif_uris import SarifUris
from trailmark.models import EdgeConfidence, EdgeKind
from trailmark.parse import parse_directory, parse_file
from trailmark.parsers.c._macros import _macro, _replace_macro


def test_swift_nested_extension_owners_are_remapped(tmp_path: Path) -> None:
    for filename, source in {
        "a.swift": "class Service {}",
        "b.swift": "extension Service { struct Inner { func go() {} } }",
        "c.swift": "extension Service.Inner { func more() { go() } }",
    }.items():
        (tmp_path / filename).write_text(source)
    graph = parse_directory(str(tmp_path), "swift")
    assert "a:Service.Inner.more" in graph.nodes
    assert any(
        e.source_id == "a:Service.Inner.more"
        and e.target_id == "a:Service.Inner.go"
        and e.confidence == EdgeConfidence.CERTAIN
        for e in graph.edges
    )
    assert all(e.source_id in graph.nodes and e.target_id in graph.nodes for e in graph.edges)


def test_java_constructors_bind_across_files(tmp_path: Path) -> None:
    (tmp_path / "Repo.java").write_text("class Repo { Repo() {} }")
    (tmp_path / "Service.java").write_text("class Service { void run() { new Repo(); } }")
    graph = parse_directory(str(tmp_path), "java")
    edges = [
        e for e in graph.edges if e.source_id == "Service:Service.run" and e.kind == EdgeKind.CALLS
    ]
    assert len(edges) == 1
    assert edges[0].target_id == "Repo:Repo.Repo"
    assert edges[0].confidence == EdgeConfidence.INFERRED
    assert not dict(edges[0].attributes).get("binding_blocked")


@pytest.mark.parametrize(
    ("extension", "deep", "simple"),
    [
        ("cpp", "struct A {" * 400 + "int x;" + "};" * 400, "void simple() {}"),
        (
            "cpp",
            "void deep(){ auto x=" + "[]{return " * 600 + "1" + ";}" * 600 + ";}",
            "void simple() {}",
        ),
        ("kt", "fun deep() {" * 1100 + "println(1)" + "}" * 1100, "fun simple() {}"),
        ("swift", "func deep() {" * 1100 + "print(1)" + "}" * 1100, "func simple() {}"),
        (
            "java",
            "class Deep { void run(){ Runnable r=" + "() -> {" * 650 + "run();" + "};" * 650 + "}}",
            "class Simple { void simple() {} }",
        ),
        (
            "php",
            "<?php function deep(){ $f=" + "function(){ return " * 1100 + "1" + ";}" * 1100 + ";}",
            "<?php function simple() {}",
        ),
    ],
    ids=[
        "cpp-types",
        "cpp-lambdas",
        "kotlin-functions",
        "swift-functions",
        "java-lambdas",
        "php-closures",
    ],
)
def test_deep_file_does_not_abort_directory(
    tmp_path: Path,
    extension: str,
    deep: str,
    simple: str,
) -> None:
    (tmp_path / f"deep.{extension}").write_text(deep)
    (tmp_path / f"simple.{extension}").write_text(simple)
    graph = parse_directory(str(tmp_path), "auto")
    assert any(u.name == "simple" for u in graph.nodes.values())
    assert graph.parse_diagnostics
    assert graph.source_file_counts


@pytest.mark.parametrize(
    ("extension", "prefix", "suffix"),
    [
        ("js", "function deep(x){ let ", "=x; return x(); }"),
        ("py", "def deep(x):\n ", "=x\n return x()\n"),
        ("rs", "fn deep(x: i32) { let ", "=x; x(); }"),
    ],
)
def test_deep_patterns_do_not_recurse(
    tmp_path: Path, extension: str, prefix: str, suffix: str
) -> None:
    path = tmp_path / f"pattern.{extension}"
    path.write_text(prefix + "[" * 1200 + "x" + "]" * 1200 + suffix)
    graph = parse_file(str(path))
    assert graph.nodes


def test_macro_substitution_rejects_oversize_before_expansion() -> None:
    macro = _macro("a", None, "token " * 60_000)
    assert _replace_macro("a " * 4000, "a", macro) is None
    macro = _macro("a", None, "x" * 100)
    assert _replace_macro("a " * 4000, "a", macro) is None


def test_unresolved_wrapper_allows_callback_table_and_preserves_indirect_calls(
    tmp_path: Path,
) -> None:
    path = tmp_path / "wrapper.c"
    path.write_text(
        "struct Ops { void (*run)(void); };\nvoid helper(void) {}\n"
        "static const struct Ops table = { .run = helper };\n"
        "UNKNOWN(action) { table.run(); (*callback)(); }"
    )
    graph = parse_file(str(path), "c")
    edges = [
        e
        for e in graph.edges
        if e.source_id == "wrapper:UNKNOWN(action)" and e.kind == EdgeKind.CALLS
    ]
    table = next(e for e in edges if dict(e.attributes).get("callback_table"))
    assert table.target_id == "wrapper:helper"
    assert table.confidence == EdgeConfidence.INFERRED
    assert dict(table.attributes).get("macro_signature_unresolved")
    indirect = next(e for e in edges if dict(e.attributes).get("binding_blocked"))
    assert indirect.confidence == EdgeConfidence.UNCERTAIN


@pytest.mark.parametrize("order", [("b0", "b9"), ("b9", "b0")])
def test_sarif_chain_limit_is_independent_of_result_order(order: tuple[str, str]) -> None:
    bases = {f"b{i}": {"uri": "./", "uriBaseId": f"b{i + 1}"} for i in range(40)}
    bases["b40"] = {"uri": "file:///project/"}
    resolver = SarifUris({"originalUriBaseIds": bases})
    resolved = {base: resolver.resolve({"uri": "app.py", "uriBaseId": base}) for base in order}
    assert resolved == {"b0": None, "b9": "file:///project/app.py"}


def test_sarif_description_only_base_uses_root() -> None:
    resolver = SarifUris({"originalUriBaseIds": {"ROOT": {"description": {"text": "unknown"}}}})
    assert resolver.resolve({"uri": "app.py", "uriBaseId": "ROOT"}) == "app.py"


def test_move_module_alias_does_not_shadow_bare_function(tmp_path: Path) -> None:
    path = tmp_path / "pkg.move"
    path.write_text(
        "module 0x2::pkg { use sui::package::{Self}; fun package(cap: u64) {} "
        "fun run(cap: u64) { package(cap); } }"
    )
    graph = parse_file(str(path))
    edge = next(
        e for e in graph.edges if e.source_id == "pkg:0x2::pkg:run" and e.kind == EdgeKind.CALLS
    )
    assert edge.target_id == "pkg:0x2::pkg:package"
    assert edge.confidence == EdgeConfidence.CERTAIN


def test_move_macro_callbacks_and_import_modules(tmp_path: Path) -> None:
    path = tmp_path / "macro.move"
    path.write_text(
        "module 0x2::pkg { use sui::coin::{Self, Coin}; "
        "use fun vault_value as Vault.value; "
        "macro fun apply($f: |u64|u64) { $f(1); assert!(true, 0); } }"
    )
    graph = parse_file(str(path))
    unit = graph.nodes["macro:0x2::pkg:apply"]
    assert unit.parameters[0].name == "$f"
    edges = [e for e in graph.edges if e.source_id == unit.id and e.kind == EdgeKind.CALLS]
    callback = next(e for e in edges if dict(e.attributes).get("binding_blocked"))
    assert callback.confidence == EdgeConfidence.UNCERTAIN
    assert any(e.target_id == "builtin.move:assert!" for e in edges)
    assert dict(graph.nodes["builtin.move:assert!"].attributes)["move_builtin"]
    assert [e.target_id for e in graph.edges if e.kind == EdgeKind.IMPORTS] == ["sui::coin"]
    assert graph.dependencies == ["sui::coin"]


def test_ruby_vcalls_use_preceding_assignment_and_self_keeps_receiver(tmp_path: Path) -> None:
    path = tmp_path / "calls.rb"
    path.write_text(
        "def compute; 1; end\ndef name2; 2; end\n"
        "def run; compute; compute=3; compute; compute(); end\n"
        "class Bar; def run; self.name2; end; end\n"
    )
    graph = parse_file(str(path))
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert sum(e.source_id == "calls:run" and e.target_id == "calls:compute" for e in calls) == 2
    edge = next(e for e in calls if e.source_id == "calls:Bar.run")
    assert edge.target_id.endswith(":calls:Bar.name2")


def test_ruby_ambiguous_inheritance_keeps_uncertain_proxy(tmp_path: Path) -> None:
    path = tmp_path / "ambiguous.rb"
    path.write_text(
        "class A; def value; 1; end; end\nclass B; def value; 2; end; end\n"
        "class C < A; end\nclass C < B; def run; value; end; end\n"
    )
    graph = parse_file(str(path))
    edge = next(
        e for e in graph.edges if e.source_id == "ambiguous:C.run" and e.kind == EdgeKind.CALLS
    )
    assert edge.confidence == EdgeConfidence.UNCERTAIN
    assert edge.target_id.endswith(":ambiguous:C.value@ambiguous")


@pytest.mark.parametrize(
    ("extension", "source"),
    [
        ("circom", "template Local() {} template Main() { component c = Local(); }"),
        ("erl", "-module(m).\ncaf(X) -> X.\nrun() -> caf(1).\n"),
    ],
)
def test_resolved_frontend_calls_are_not_binding_blocked(
    tmp_path: Path, extension: str, source: str
) -> None:
    path = tmp_path / f"m.{extension}"
    path.write_text(source)
    graph = parse_file(str(path))
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 1
    assert graph.nodes[calls[0].target_id].origin.value == "source"
    assert calls[0].confidence == EdgeConfidence.CERTAIN
    assert not dict(calls[0].attributes).get("binding_blocked")


def test_move_grouped_address_imports_emit_modules(tmp_path: Path) -> None:
    path = tmp_path / "imports.move"
    path.write_text(
        "module 0x2::pkg { use sui::{coin::{Self, Coin}, balance::{Self, Balance}}; "
        "fun run() { coin::work(); balance::work(); } }"
    )
    graph = parse_file(str(path))
    assert {e.target_id for e in graph.edges if e.kind == EdgeKind.IMPORTS} == {
        "sui::coin",
        "sui::balance",
    }
    assert {
        dict(e.attributes).get("move_target") for e in graph.edges if e.kind == EdgeKind.CALLS
    } == {"sui::coin::work", "sui::balance::work"}


def test_haskell_operator_proxies_are_consistent(tmp_path: Path) -> None:
    path = tmp_path / "ops.hs"
    path.write_text("run x = f . g $ x + 1\n")
    graph = parse_file(str(path))
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    for name in ("compose", "apply", "plus"):
        edge = next(e for e in calls if e.target_id.endswith(f":ops:$operator.{name}"))
        assert not dict(edge.attributes).get("binding_blocked")


def test_recursive_legacy_walker_reports_file_and_continues(tmp_path: Path) -> None:
    (tmp_path / "deep.erl").write_text(
        "-module(deep).\nrun() -> " + "f(" * 1500 + "1" + ")" * 1500 + ".\n"
    )
    (tmp_path / "simple.erl").write_text("-module(simple).\nsimple() -> ok.\n")
    graph = parse_directory(str(tmp_path), "erlang")
    diagnostic = next(d for d in graph.parse_diagnostics if "recursion limit" in d.message)
    assert diagnostic.language == diagnostic.grammar == "erlang"
    assert diagnostic.location.file_path == str(tmp_path / "deep.erl")
    assert diagnostic.location.start_line == 1
    assert diagnostic.kind == "unsupported_syntax"
    assert graph.nodes["deep"].kind.value == "module"
    assert "simple:simple/0" in graph.nodes
    assert graph.source_file_counts == {"erlang": 2}


@pytest.mark.parametrize(
    ("extension", "caller", "callee"),
    [
        ("erl", "-module(app).\nrun() -> helper().\n", "-module(peer).\nhelper() -> ok.\n"),
        ("circom", "template Main() { var x = helper(); }", "function helper() { return 1; }"),
    ],
)
def test_language_scoped_targets_do_not_link_without_import(
    tmp_path: Path,
    extension: str,
    caller: str,
    callee: str,
) -> None:
    (tmp_path / f"app.{extension}").write_text(caller)
    (tmp_path / f"peer.{extension}").write_text(callee)
    graph = parse_directory(str(tmp_path), "auto")
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 1
    assert graph.nodes[calls[0].target_id].origin.value == "proxy"
    assert calls[0].target_id.startswith("proxy.unresolved:app:")


def test_keyword_graph_nesting_limit_is_reported_at_the_declaration(tmp_path: Path) -> None:
    path = tmp_path / "deep.cpp"
    path.write_text("struct A {" * 100 + "int x;" + "};" * 100)
    graph = parse_file(str(path), "cpp")
    assert any(
        diagnostic.message == "Declaration nesting exceeds extraction limit (48)"
        for diagnostic in graph.parse_diagnostics
    )
    assert not any("recursion limit" in d.message for d in graph.parse_diagnostics)
