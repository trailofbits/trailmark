"""Tests for the Dart parser."""

from __future__ import annotations

from pathlib import Path

from trailmark.models.graph import CodeGraph
from trailmark.parsers.dart import DartParser
from trailmark.query.api import QueryEngine


def _parse(tmp_path: Path, body: str, name: str = "app.dart") -> CodeGraph:
    (tmp_path / name).write_text(body)
    return DartParser().parse_directory(str(tmp_path))


class TestBasicExtraction:
    def test_module_node_created(self, tmp_path: Path) -> None:
        graph = _parse(tmp_path, "void greet() {}\n")
        assert "app" in graph.nodes
        assert graph.nodes["app"].kind.value == "module"

    def test_top_level_function(self, tmp_path: Path) -> None:
        graph = _parse(tmp_path, "void greet() {}\n")
        assert "app:greet" in graph.nodes
        assert graph.nodes["app:greet"].kind.value == "function"

    def test_parameters_and_return_type(self, tmp_path: Path) -> None:
        graph = _parse(
            tmp_path,
            "int add(int a, int b) { return a + b; }\n",
        )
        func = graph.nodes["app:add"]
        assert [p.name for p in func.parameters] == ["a", "b"]
        assert func.return_type is not None
        assert func.return_type.name == "int"

    def test_void_return_type(self, tmp_path: Path) -> None:
        graph = _parse(tmp_path, "void log(String s) {}\n")
        func = graph.nodes["app:log"]
        assert func.return_type is not None
        assert func.return_type.name == "void"

    def test_class_with_methods(self, tmp_path: Path) -> None:
        graph = _parse(
            tmp_path,
            "class Foo {\n  int bar(int x) { return x; }\n  void baz() {}\n}\n",
        )
        assert graph.nodes["app:Foo"].kind.value == "class"
        assert "app:Foo.bar" in graph.nodes
        assert "app:Foo.baz" in graph.nodes
        assert graph.nodes["app:Foo.bar"].kind.value == "method"

    def test_abstract_method_extracted(self, tmp_path: Path) -> None:
        graph = _parse(
            tmp_path,
            "abstract class Repo {\n  int findById(int id);\n}\n",
        )
        assert "app:Repo.findById" in graph.nodes


class TestControlFlow:
    def test_complexity_counts_branches(self, tmp_path: Path) -> None:
        code = (
            "String classify(int x) {\n"
            "  if (x < 0) { return 'neg'; }\n"
            "  switch (x) {\n"
            "    case 0: return 'zero';\n"
            "    case 1: return 'one';\n"
            "    default: return 'many';\n"
            "  }\n"
            "}\n"
        )
        graph = _parse(tmp_path, code)
        assert graph.nodes["app:classify"].cyclomatic_complexity is not None
        assert graph.nodes["app:classify"].cyclomatic_complexity >= 3


class TestImports:
    def test_package_import_captured(self, tmp_path: Path) -> None:
        graph = _parse(
            tmp_path,
            "import 'package:flutter/material.dart';\nvoid f() {}\n",
        )
        assert graph.dependencies, "expected at least one dependency"
        assert any("material" in d for d in graph.dependencies)


class TestCallEdges:
    def test_call_edge_recorded(self, tmp_path: Path) -> None:
        graph = _parse(
            tmp_path,
            "void a() { b(); }\nvoid b() {}\n",
        )
        sources = {e.source_id for e in graph.edges if e.kind.value == "calls"}
        assert "app:a" in sources


def test_named_and_factory_constructor_navigation(tmp_path: Path) -> None:
    from trailmark.models.edges import EdgeKind
    from trailmark.query.api import QueryEngine

    graph = _parse(
        tmp_path,
        "class Context {\n"
        "  Context._internal();\n"
        "  factory Context() { return Context._internal(); }\n"
        "}\n"
        "Context make() => Context._internal();\n",
    )
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert {(e.source_id, e.target_id) for e in calls} == {
        ("app:Context.Context", "app:Context.Context._internal"),
        ("app:make", "app:Context.Context._internal"),
    }
    assert {e.location.start_line for e in calls if e.location} == {3, 5}
    engine = QueryEngine.from_graph(graph)
    assert {n["id"] for n in engine.callers_of("app:Context.Context._internal")} == {
        "app:Context.Context",
        "app:make",
    }


def test_constructor_name_shadow_stays_unresolved(tmp_path: Path) -> None:
    from trailmark.models.edges import EdgeKind

    graph = _parse(
        tmp_path,
        "class Context { Context.named(); }\n"
        "void invoke(dynamic Context) { Context.named(); }\n"
        "void external() { Missing.named(); }\n",
    )
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 2
    assert all(e.target_id.startswith("proxy.") for e in calls)


def test_unnamed_constructor_navigation(tmp_path: Path) -> None:
    from trailmark.models.edges import EdgeKind

    graph = _parse(tmp_path, "class Context { Context(); }\nContext make() => Context();\n")
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 1
    assert (calls[0].source_id, calls[0].target_id) == ("app:make", "app:Context.Context")


def test_explicit_this_calls_use_nearest_class_even_with_local_shadow(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        "class Box {\n"
        " void work() {}\n"
        " void run(void Function() work) { this.work(); }\n"
        "}\n"
        "class Other { void work() {} }\n",
    )
    calls = [e for e in graph.edges if e.source_id == "app:Box.run" and e.kind.value == "calls"]
    assert len(calls) == 1 and calls[0].target_id == "app:Box.work"
    assert calls[0].confidence.value == "inferred"
    assert calls[0].location is not None and calls[0].location.start_line == 3
    engine = QueryEngine.from_graph(graph)
    assert {n["id"] for n in engine.callers_of("app:Box.work")} == {"app:Box.run"}


def test_this_missing_and_other_receivers_stay_uncertain(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        "class Base { void work() {} }\n"
        "class Box extends Base {\n"
        " void run(Base other) { this.work(); other.work(); }\n"
        "}\n",
    )
    calls = [e for e in graph.edges if e.source_id == "app:Box.run" and e.kind.value == "calls"]
    assert len(calls) == 2
    assert all(e.target_id != "app:Base.work" for e in calls)


def test_super_call_site_is_retained_without_inheritance_guess(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        "class Base { void work() {} }\nclass Box extends Base { void run() { super.work(); } }\n",
    )
    calls = [e for e in graph.edges if e.source_id == "app:Box.run" and e.kind.value == "calls"]
    assert len(calls) == 1
    assert calls[0].target_id != "app:Base.work"
    assert dict(calls[0].attributes)["raw_target"] == "super.work"
    assert calls[0].location is not None and calls[0].location.start_line == 2
