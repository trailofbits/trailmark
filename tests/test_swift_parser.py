"""Tests for the Swift parser."""

from __future__ import annotations

from pathlib import Path

from trailmark.models.graph import CodeGraph
from trailmark.parsers.swift import SwiftParser


def _parse(tmp_path: Path, body: str, name: str = "app.swift") -> CodeGraph:
    (tmp_path / name).write_text(body)
    return SwiftParser().parse_directory(str(tmp_path))


class TestBasicExtraction:
    def test_module_node_created(self, tmp_path: Path) -> None:
        graph = _parse(tmp_path, "func greet() {}\n")
        assert "app" in graph.nodes
        assert graph.nodes["app"].kind.value == "module"

    def test_top_level_function(self, tmp_path: Path) -> None:
        graph = _parse(tmp_path, "func greet() {}\n")
        assert "app:greet" in graph.nodes
        func = graph.nodes["app:greet"]
        assert func.kind.value == "function"

    def test_parameters_and_types(self, tmp_path: Path) -> None:
        graph = _parse(tmp_path, "func add(a: Int, b: Int) -> Int { return a + b }\n")
        func = graph.nodes["app:add"]
        assert [p.name for p in func.parameters] == ["a", "b"]
        assert func.return_type is not None
        assert func.return_type.name == "Int"

    def test_class_with_method(self, tmp_path: Path) -> None:
        graph = _parse(
            tmp_path,
            "class Foo {\n    func bar(x: Int) -> Int { return x }\n}\n",
        )
        assert "app:Foo" in graph.nodes
        assert graph.nodes["app:Foo"].kind.value == "class"
        assert "app:Foo.bar" in graph.nodes
        assert graph.nodes["app:Foo.bar"].kind.value == "method"

    def test_struct_and_enum_kinds(self, tmp_path: Path) -> None:
        graph = _parse(
            tmp_path,
            "struct Point {}\nenum Direction { case north }\nprotocol Drawable {}\n",
        )
        assert graph.nodes["app:Point"].kind.value == "struct"
        assert graph.nodes["app:Direction"].kind.value == "enum"
        assert graph.nodes["app:Drawable"].kind.value == "interface"


class TestControlFlow:
    def test_complexity_counts_branches(self, tmp_path: Path) -> None:
        code = (
            "func check(x: Int) -> Bool {\n"
            "    if x > 0 {\n"
            "        if x > 10 { return true }\n"
            "    }\n"
            "    return false\n"
            "}\n"
        )
        graph = _parse(tmp_path, code)
        assert graph.nodes["app:check"].cyclomatic_complexity is not None
        assert graph.nodes["app:check"].cyclomatic_complexity >= 3

    def test_throws_clause_does_not_crash(self, tmp_path: Path) -> None:
        """Swift throws are not captured yet; verify the parser is graceful.

        Swift uses `control_transfer_statement` for `throw`, which collides
        with `return`/`break`/`continue`. Capturing requires a Swift-specific
        walk that filters by `throw_keyword` — deferred. This test locks in
        that the parser at least doesn't crash on `throws` signatures.
        """
        code = "func auth() throws {\n    throw AuthError.invalid\n}\n"
        graph = _parse(tmp_path, code)
        assert "app:auth" in graph.nodes


class TestImports:
    def test_import_captured(self, tmp_path: Path) -> None:
        graph = _parse(tmp_path, "import Foundation\nfunc f() {}\n")
        assert "Foundation" in graph.dependencies


class TestCallEdges:
    def test_call_edge_recorded(self, tmp_path: Path) -> None:
        graph = _parse(
            tmp_path,
            "func a() { b() }\nfunc b() {}\n",
        )
        sources = {e.source_id for e in graph.edges if e.kind.value == "calls"}
        assert "app:a" in sources
