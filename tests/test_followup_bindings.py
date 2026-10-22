"""Lexical namespaces, callback reachability, and declaration preservation."""

from __future__ import annotations

from pathlib import Path

import pytest

from trailmark.models import CodeGraph, EdgeConfidence, EdgeKind
from trailmark.parse import parse_file
from trailmark.query.api import QueryEngine


def _parse(tmp_path: Path, filename: str, text: str) -> CodeGraph:
    source = tmp_path / filename
    source.write_text(text)
    return parse_file(str(source))


def test_python_method_does_not_retarget_module_function(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        "case.py",
        "def process(x): return x\nclass Handler:\n def process(self,x): return process(x)\n",
    )
    calls = [
        e for e in graph.edges if e.kind == EdgeKind.CALLS and e.source_id == "case:Handler.process"
    ]
    assert [(e.target_id, e.confidence) for e in calls] == [
        ("case:process", EdgeConfidence.CERTAIN)
    ]


@pytest.mark.parametrize(
    ("filename", "text", "caller", "target"),
    [
        (
            "case.rs",
            "fn config() -> i32 { 1 } fn use_config() { let config = config(); }",
            "case:use_config",
            "case:config",
        ),
        (
            "case.go",
            "package main\nfunc config() int { return 1 }; func use_conf"
            "ig() { config := config(); _ = config }",
            "case:use_config",
            "case:config",
        ),
        (
            "case.kt",
            "fun items(): Int = 1\nfun useItems() { val items = items() }",
            "useItems",
            "items",
        ),
    ],
)
def test_initializer_uses_previous_binding(
    tmp_path: Path, filename: str, text: str, caller: str, target: str
) -> None:
    graph = _parse(tmp_path, filename, text)
    edges = [e for e in graph.edges if e.kind == EdgeKind.CALLS and e.source_id.endswith(caller)]
    assert len(edges) == 1
    assert edges[0].target_id.endswith(target)
    assert edges[0].confidence == EdgeConfidence.CERTAIN
    assert not dict(edges[0].attributes).get("binding_blocked")


def test_java_variables_do_not_shadow_methods(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        "Box.java",
        "class Box { int size() { return 1; } void name() {} void u"
        "se() { int size = size(); } void rename(String name) { nam"
        "e(); } }",
    )
    calls = {(e.source_id, e.target_id): e for e in graph.edges if e.kind == EdgeKind.CALLS}
    for source, target in [("Box:Box.use", "Box:Box.size"), ("Box:Box.rename", "Box:Box.name")]:
        assert calls[source, target].confidence == EdgeConfidence.CERTAIN
        assert not dict(calls[source, target].attributes).get("binding_blocked")


def test_go_closure_calls_are_owned(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        "case.go",
        "package main\nfunc helper() {}\nfunc Outer(register func(fun"
        "c())) { go func() { helper() }(); defer func() { helper() "
        "}(); register(func() { helper() }) }",
    )
    calls = [
        e
        for e in graph.edges
        if e.kind == EdgeKind.CALLS and e.source_id == "case:Outer" and e.target_id == "case:helper"
    ]
    assert len(calls) == 3


@pytest.mark.parametrize(
    ("filename", "text", "root", "targets"),
    [
        (
            "case.js",
            "function process(x) {} function validate(x) {} function ma"
            "in(items) { items.map(x => process(x)); items.forEach(func"
            "tion(x) { validate(x); }); }",
            "main",
            {"process", "validate"},
        ),
        (
            "Case.java",
            "class Case { static void process(int i) {} void main(java."
            "util.List<Integer> items) { items.forEach(i -> process(i))"
            "; Runnable r = new Runnable() { public void run() { proces"
            "s(1); } }; } }",
            "Case.main",
            {"process"},
        ),
        (
            "case.php",
            "<?php function process($x) {} function main($items) { arra"
            "y_map(function($x) { process($x); }, $items); }",
            "main",
            {"process"},
        ),
        (
            "case.py",
            "def process(x): pass\ndef factory():\n class Local:\n  def m("
            "self): process(1)\n return Local\n",
            "factory",
            {"process"},
        ),
    ],
)
def test_callbacks_remain_reachable(
    tmp_path: Path, filename: str, text: str, root: str, targets: set[str]
) -> None:
    graph = _parse(tmp_path, filename, text)
    engine = QueryEngine.from_directory(str(tmp_path), language="auto")
    reachable = engine.reachable_from(root)
    assert targets <= {item["name"] for item in reachable}
    assert any(
        e.confidence == EdgeConfidence.UNCERTAIN and dict(e.attributes).get("deferred")
        for e in graph.edges
        if e.kind == EdgeKind.CALLS
    )


@pytest.mark.parametrize("extension", ["js", "ts"])
def test_object_methods_do_not_overwrite_functions(tmp_path: Path, extension: str) -> None:
    graph = _parse(
        tmp_path,
        f"case.{extension}",
        "function init() { loadConfig(); }\nconst api = { init() { s"
        "tartApi(); }, get() { fetchUser(); } };\nconst admin = { ge"
        "t() { fetchAdmin(); } };\n",
    )
    calls = {(e.source_id, e.target_id) for e in graph.edges if e.kind == EdgeKind.CALLS}
    for source, target in [
        ("init", "loadConfig"),
        ("api.init", "startApi"),
        ("api.get", "fetchUser"),
        ("admin.get", "fetchAdmin"),
    ]:
        assert any(s == f"case:{source}" and t.endswith(f":case:{target}") for s, t in calls)
    assert all(
        key in graph.nodes
        for key in ["case:init", "case:api.init", "case:api.get", "case:admin.get"]
    )


def test_repeated_python_nested_definitions_keep_bodies(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path, "case.py", "def two():\n def inner(): first()\n def inner(): second()\n inner()\n"
    )
    nested = [
        u
        for u in graph.nodes.values()
        if u.id.startswith("case:two.inner") and u.kind.value == "function"
    ]
    assert len(nested) == 2
    assert {u.location.start_line for u in nested} == {2, 3}
    calls = {
        e.source_id: e.target_id
        for e in graph.edges
        if e.kind == EdgeKind.CALLS and not dict(e.attributes).get("deferred")
    }
    assert any(
        s.startswith("case:two.inner@2_") and t.endswith(":case:first") for s, t in calls.items()
    )
    assert any(
        s.startswith("case:two.inner@3_") and t.endswith(":case:second") for s, t in calls.items()
    )


def test_javascript_local_require_destructure_links_across_files(tmp_path: Path) -> None:
    from trailmark.parse import parse_directory

    (tmp_path / "app.js").write_text(
        "function handler(x) { const { sanitize } = require('./util'); return sanitize(x); }"
    )
    (tmp_path / "util.js").write_text("export function sanitize(x) { return x; }")
    graph = parse_directory(str(tmp_path), "javascript")
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS and e.target_id == "util:sanitize"]
    assert len(calls) == 1
    assert calls[0].source_id == "app:handler"
    assert calls[0].confidence == EdgeConfidence.INFERRED
    assert not dict(calls[0].attributes).get("binding_blocked")


def test_unmatched_entrypoint_override_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "app.py").write_text("def handler(): pass\n")
    (tmp_path / ".trailmark").mkdir()
    (tmp_path / ".trailmark/entrypoints.toml").write_text(
        '[[entrypoint]]\nnode = "app:old_handler"\nkind = "api"\n'
    )
    QueryEngine.from_directory(str(tmp_path))
    assert "Entrypoint override matches no node: app:old_handler" in caplog.text


@pytest.mark.parametrize(
    ("filename", "text"),
    [
        (
            "case.py",
            "def validate(): pass\ndef run(register): register(lambda validate: validate())\n",
        ),
        (
            "case.go",
            "package main\nfunc validate() {}\n"
            "func run() { register(func(validate func()) { validate() }) }",
        ),
    ],
)
def test_unextracted_callback_parameters_do_not_bind_globals(
    tmp_path: Path, filename: str, text: str
) -> None:
    graph = _parse(tmp_path, filename, text)
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS and e.source_id == "case:run"]
    assert not any(e.target_id == "case:validate" for e in calls)
    blocked = [e for e in calls if e.target_id.endswith("$binding.validate")]
    assert len(blocked) == 1
    assert blocked[0].confidence == EdgeConfidence.UNCERTAIN
