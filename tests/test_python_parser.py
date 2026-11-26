"""Tests for the Python language parser."""

from __future__ import annotations

import os
import tempfile

import pytest

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind
from trailmark.parsers.python.parser import PythonParser

SAMPLE_CODE = """\
import os
from pathlib import Path


class Animal:
    \"\"\"A base animal class.\"\"\"

    def speak(self) -> str:
        return "..."


class Dog(Animal):
    def speak(self) -> str:
        return "woof"

    def fetch(self, item: str) -> bool:
        if not item:
            raise ValueError("empty item")
        return True


def greet(name: str, loud: bool = False) -> str:
    if loud:
        return name.upper()
    return name


def process(items: list[int]) -> int:
    total = 0
    for item in items:
        if item > 0:
            total += item
        elif item < 0:
            total -= item
    return total
"""


def _parse_sample() -> tuple[PythonParser, CodeGraph]:
    parser = PythonParser()
    with tempfile.NamedTemporaryFile(
        suffix=".py",
        mode="w",
        delete=False,
    ) as f:
        f.write(SAMPLE_CODE)
        f.flush()
        graph = parser.parse_file(f.name)
    os.unlink(f.name)
    return parser, graph


class TestPythonParserNodes:
    @pytest.mark.mutation_fast
    def test_finds_module(self) -> None:
        _, graph = _parse_sample()
        modules = [n for n in graph.nodes.values() if n.kind == NodeKind.MODULE]
        assert len(modules) == 1

    @pytest.mark.mutation_fast
    def test_finds_exactly_two_classes(self) -> None:
        _, graph = _parse_sample()
        classes = [n for n in graph.nodes.values() if n.kind == NodeKind.CLASS]
        assert len(classes) == 2
        names = {c.name for c in classes}
        assert names == {"Animal", "Dog"}

    @pytest.mark.mutation_fast
    def test_finds_exactly_two_functions(self) -> None:
        _, graph = _parse_sample()
        funcs = [n for n in graph.nodes.values() if n.kind == NodeKind.FUNCTION]
        assert len(funcs) == 2
        names = {f.name for f in funcs}
        assert names == {"greet", "process"}

    @pytest.mark.mutation_fast
    def test_finds_methods(self) -> None:
        _, graph = _parse_sample()
        methods = [n for n in graph.nodes.values() if n.kind == NodeKind.METHOD]
        names = {m.name for m in methods}
        assert "speak" in names
        assert "fetch" in names

    def test_node_ids_use_module_prefix(self) -> None:
        _, graph = _parse_sample()
        greet = next(n for n in graph.nodes.values() if n.name == "greet")
        assert ":" in greet.id
        assert greet.id.endswith(":greet")

    def test_method_ids_use_dot_separator(self) -> None:
        _, graph = _parse_sample()
        fetch = next(n for n in graph.nodes.values() if n.name == "fetch")
        assert ".fetch" in fetch.id

    @pytest.mark.mutation_fast
    def test_class_docstring_exact(self) -> None:
        _, graph = _parse_sample()
        animal = next(n for n in graph.nodes.values() if n.name == "Animal")
        assert animal.docstring == "A base animal class."

    def test_location_has_file_path_and_lines(self) -> None:
        _, graph = _parse_sample()
        greet = next(n for n in graph.nodes.values() if n.name == "greet")
        assert greet.location.file_path.endswith(".py")
        assert greet.location.start_line > 0
        assert greet.location.end_line >= greet.location.start_line
        assert greet.location.start_col is not None
        assert greet.location.end_col is not None

    def test_graph_language_is_python(self) -> None:
        _, graph = _parse_sample()
        assert graph.language == "python"


class TestPythonParserParameters:
    @pytest.mark.mutation_fast
    def test_typed_parameter(self) -> None:
        _, graph = _parse_sample()
        fetch = next(n for n in graph.nodes.values() if n.name == "fetch")
        assert len(fetch.parameters) == 1
        assert fetch.parameters[0].name == "item"
        assert fetch.parameters[0].type_ref is not None
        assert fetch.parameters[0].type_ref.name == "str"

    @pytest.mark.mutation_fast
    def test_typed_default_parameter(self) -> None:
        _, graph = _parse_sample()
        greet = next(n for n in graph.nodes.values() if n.name == "greet")
        params = {p.name: p for p in greet.parameters}
        assert len(params) == 2
        assert "name" in params
        assert "loud" in params
        assert params["loud"].default == "False"
        assert params["loud"].type_ref is not None
        assert params["loud"].type_ref.name == "bool"
        assert params["name"].type_ref is not None
        assert params["name"].type_ref.name == "str"

    @pytest.mark.mutation_fast
    def test_return_type(self) -> None:
        _, graph = _parse_sample()
        greet = next(n for n in graph.nodes.values() if n.name == "greet")
        assert greet.return_type is not None
        assert greet.return_type.name == "str"

    def test_generic_return_type(self) -> None:
        _, graph = _parse_sample()
        process = next(n for n in graph.nodes.values() if n.name == "process")
        assert process.return_type is not None
        assert process.return_type.name == "int"

    @pytest.mark.mutation_fast
    def test_generic_parameter_type(self) -> None:
        _, graph = _parse_sample()
        process = next(n for n in graph.nodes.values() if n.name == "process")
        assert len(process.parameters) == 1
        p = process.parameters[0]
        assert p.name == "items"
        assert p.type_ref is not None
        assert p.type_ref.name == "list"
        assert len(p.type_ref.generic_args) == 1
        assert p.type_ref.generic_args[0].name == "int"

    def test_self_excluded_from_parameters(self) -> None:
        _, graph = _parse_sample()
        fetch = next(n for n in graph.nodes.values() if n.name == "fetch")
        param_names = [p.name for p in fetch.parameters]
        assert "self" not in param_names


class TestPythonParserComplexity:
    def test_simple_function_complexity(self) -> None:
        _, graph = _parse_sample()
        speak = next(
            n for n in graph.nodes.values() if n.name == "speak" and n.kind == NodeKind.METHOD
        )
        assert speak.cyclomatic_complexity == 1

    def test_branching_function_complexity(self) -> None:
        _, graph = _parse_sample()
        process = next(n for n in graph.nodes.values() if n.name == "process")
        # for + if + elif = 3 branches, complexity = 4
        assert process.cyclomatic_complexity == 4

    def test_branches_tracked_exact(self) -> None:
        _, graph = _parse_sample()
        process = next(n for n in graph.nodes.values() if n.name == "process")
        assert len(process.branches) == 3

    def test_fetch_complexity(self) -> None:
        _, graph = _parse_sample()
        fetch = next(n for n in graph.nodes.values() if n.name == "fetch")
        # if not item -> 1 branch, complexity = 2
        assert fetch.cyclomatic_complexity == 2


class TestPythonParserEdges:
    def test_contains_edges_exact_count(self) -> None:
        _, graph = _parse_sample()
        contains = [e for e in graph.edges if e.kind == EdgeKind.CONTAINS]
        # module->Animal, module->Dog, module->greet, module->process,
        # Animal->speak, Dog->speak, Dog->fetch = 7
        assert len(contains) == 7

    @pytest.mark.mutation_fast
    def test_inherits_edge_exact(self) -> None:
        _, graph = _parse_sample()
        inherits = [e for e in graph.edges if e.kind == EdgeKind.INHERITS]
        assert len(inherits) == 1
        assert inherits[0].source_id.endswith(":Dog")
        assert inherits[0].target_id.endswith(":Animal")
        assert inherits[0].confidence == EdgeConfidence.INFERRED

    def test_call_edges_exist(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        assert len(calls) >= 1

    def test_raise_exception_type(self) -> None:
        _, graph = _parse_sample()
        fetch = next(n for n in graph.nodes.values() if n.name == "fetch")
        assert len(fetch.exception_types) == 1
        assert fetch.exception_types[0].name == "ValueError"

    @pytest.mark.mutation_fast
    def test_attribute_call_is_inferred_confidence(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        found = False
        for edge in calls:
            if edge.target_id.endswith(".upper"):
                assert edge.confidence == EdgeConfidence.INFERRED
                found = True
                break
        assert found, "Expected a call edge to .upper"


class TestPythonParserDependencies:
    @pytest.mark.mutation_fast
    def test_imports_tracked_exact(self) -> None:
        _, graph = _parse_sample()
        assert "os" in graph.dependencies
        assert "pathlib" in graph.dependencies
        assert len(graph.dependencies) == 2


class TestPythonParserDirectory:
    @pytest.mark.mutation_fast
    def test_parse_directory(self) -> None:
        parser = PythonParser()
        graph = parser.parse_directory("src/trailmark/models")
        class_names = {n.name for n in graph.nodes.values() if n.kind == NodeKind.CLASS}
        assert "CodeUnit" in class_names
        assert "CodeEdge" in class_names
        assert "CodeGraph" in class_names
        assert graph.language == "python"
        assert graph.root_path == "src/trailmark/models"
