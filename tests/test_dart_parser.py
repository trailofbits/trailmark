"""Tests for the Dart parser."""

from __future__ import annotations

from pathlib import Path

from trailmark.models.graph import CodeGraph
from trailmark.parsers.dart import DartParser


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
