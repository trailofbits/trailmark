"""Tests for the Kotlin parser."""

from __future__ import annotations

from pathlib import Path

from trailmark.models.graph import CodeGraph
from trailmark.parsers.kotlin import KotlinParser


def _parse(tmp_path: Path, body: str, name: str = "App.kt") -> CodeGraph:
    (tmp_path / name).write_text(body)
    return KotlinParser().parse_directory(str(tmp_path))


class TestBasicExtraction:
    def test_module_node_created(self, tmp_path: Path) -> None:
        graph = _parse(tmp_path, "fun greet() {}\n")
        assert "App" in graph.nodes
        assert graph.nodes["App"].kind.value == "module"

    def test_top_level_function(self, tmp_path: Path) -> None:
        graph = _parse(tmp_path, "fun greet() {}\n")
        assert "App:greet" in graph.nodes
        assert graph.nodes["App:greet"].kind.value == "function"

    def test_parameters_and_return_type(self, tmp_path: Path) -> None:
        graph = _parse(
            tmp_path,
            "fun add(a: Int, b: Int): Int { return a + b }\n",
        )
        func = graph.nodes["App:add"]
        assert [p.name for p in func.parameters] == ["a", "b"]
        assert func.return_type is not None
        assert func.return_type.name == "Int"

    def test_nullable_return_type(self, tmp_path: Path) -> None:
        graph = _parse(
            tmp_path,
            "fun maybe(): User? { return null }\nclass User\n",
        )
        func = graph.nodes["App:maybe"]
        assert func.return_type is not None
        assert func.return_type.name == "User?"

    def test_class_with_methods(self, tmp_path: Path) -> None:
        graph = _parse(
            tmp_path,
            "class Foo {\n    fun bar(x: Int): Int { return x }\n    private fun baz() {}\n}\n",
        )
        assert graph.nodes["App:Foo"].kind.value == "class"
        assert "App:Foo.bar" in graph.nodes
        assert "App:Foo.baz" in graph.nodes
        assert graph.nodes["App:Foo.bar"].kind.value == "method"

    def test_interface_detected(self, tmp_path: Path) -> None:
        graph = _parse(
            tmp_path,
            "interface Repository {\n    fun find(id: Long): String?\n}\n",
        )
        assert graph.nodes["App:Repository"].kind.value == "interface"
        assert "App:Repository.find" in graph.nodes

    def test_data_class_detected(self, tmp_path: Path) -> None:
        graph = _parse(tmp_path, "data class User(val id: Long)\n")
        assert graph.nodes["App:User"].kind.value == "class"


class TestControlFlow:
    def test_complexity_counts_when_and_if(self, tmp_path: Path) -> None:
        code = (
            "fun classify(x: Int): String {\n"
            '    if (x < 0) return "neg"\n'
            "    return when (x) {\n"
            '        0 -> "zero"\n'
            '        1 -> "one"\n'
            '        else -> "many"\n'
            "    }\n"
            "}\n"
        )
        graph = _parse(tmp_path, code)
        assert graph.nodes["App:classify"].cyclomatic_complexity is not None
        assert graph.nodes["App:classify"].cyclomatic_complexity >= 3


class TestImports:
    def test_import_captured(self, tmp_path: Path) -> None:
        graph = _parse(
            tmp_path,
            "import kotlinx.coroutines.runBlocking\nfun f() {}\n",
        )
        assert graph.dependencies, "expected at least one dependency"


class TestCallEdges:
    def test_call_edge_recorded(self, tmp_path: Path) -> None:
        graph = _parse(
            tmp_path,
            "fun a() { b() }\nfun b() {}\n",
        )
        sources = {e.source_id for e in graph.edges if e.kind.value == "calls"}
        assert "App:a" in sources

    def test_method_call_via_navigation(self, tmp_path: Path) -> None:
        graph = _parse(
            tmp_path,
            "fun a(b: Thing) { b.run() }\nclass Thing { fun run() {} }\n",
        )
        # Call via `b.run()` should produce an INFERRED edge from a.
        sources_targets = {
            (e.source_id, e.target_id) for e in graph.edges if e.kind.value == "calls"
        }
        assert any(src == "App:a" for src, _ in sources_targets), sources_targets
