"""Tests for the Java language parser."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from tests.parse_work import parse_work
from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind
from trailmark.parsers.java.parser import JavaParser


@pytest.mark.mutation_fast
def test_anonymous_fallback_stops_at_its_lexical_owner(tmp_path: Path) -> None:
    source = tmp_path / "sample.java"
    source.write_text(
        "class Outer { class Base {} void helper() {}\n"
        "Base make() { return new Base() { void run() { helper(); } }; } }\n"
    )
    graph, _ = parse_work(source, max_work=100_000)
    call = next(
        edge
        for edge in graph.edges
        if edge.kind == EdgeKind.CALLS
        and "$anonymous@" in edge.source_id
        and edge.source_id.endswith(".run")
    )
    assert call.target_id == "sample:Outer.helper"
    assert call.confidence == EdgeConfidence.INFERRED


SAMPLE_CODE = """\
import java.util.List;
import java.io.IOException;

/** Defines animal behavior. */
interface Animal {
    String speak();
}

/** A dog that extends Pet and implements Animal. */
class Dog extends Pet implements Animal {

    private String name;

    public Dog(String name) {
        this.name = name;
    }

    public String speak() {
        return "woof";
    }

    public boolean fetch(String item, int count) {
        if (item == null || item.isEmpty()) {
            throw new IllegalArgumentException("empty");
        }
        for (int i = 0; i < count; i++) {
            System.out.println(item);
        }
        while (count > 10) {
            count--;
        }
        return true;
    }
}

class Puppy extends Dog {

    public Puppy(String name) {
        super(name);
    }

    public String play() {
        if (name != null) {
            return "playing";
        }
        return "sleeping";
    }
}

enum AnimalType {
    DOG,
    CAT,
    BIRD
}
"""


def _parse_sample() -> tuple[JavaParser, CodeGraph]:
    parser = JavaParser()
    with tempfile.NamedTemporaryFile(
        suffix=".java",
        mode="w",
        delete=False,
    ) as f:
        f.write(SAMPLE_CODE)
        f.flush()
        graph = parser.parse_file(f.name)
    os.unlink(f.name)
    return parser, graph


class TestJavaParserNodes:
    @pytest.mark.mutation_fast
    def test_finds_module(self) -> None:
        _, graph = _parse_sample()
        modules = [n for n in graph.nodes.values() if n.kind == NodeKind.MODULE]
        assert len(modules) == 1

    def test_finds_interface(self) -> None:
        _, graph = _parse_sample()
        interfaces = [n for n in graph.nodes.values() if n.kind == NodeKind.INTERFACE]
        names = {i.name for i in interfaces}
        assert "Animal" in names

    def test_finds_classes(self) -> None:
        _, graph = _parse_sample()
        classes = [n for n in graph.nodes.values() if n.kind == NodeKind.CLASS]
        names = {c.name for c in classes}
        assert "Dog" in names
        assert "Puppy" in names

    def test_finds_enum(self) -> None:
        _, graph = _parse_sample()
        enums = [n for n in graph.nodes.values() if n.kind == NodeKind.ENUM]
        names = {e.name for e in enums}
        assert "AnimalType" in names

    def test_finds_methods(self) -> None:
        _, graph = _parse_sample()
        methods = [n for n in graph.nodes.values() if n.kind == NodeKind.METHOD]
        names = {m.name for m in methods}
        assert "speak" in names
        assert "fetch" in names
        assert "play" in names


class TestJavaParserParameters:
    @pytest.mark.mutation_fast
    def test_typed_parameters(self) -> None:
        _, graph = _parse_sample()
        fetch = next(n for n in graph.nodes.values() if n.name == "fetch")
        assert len(fetch.parameters) == 2
        param_names = {p.name for p in fetch.parameters}
        assert "item" in param_names
        assert "count" in param_names

    @pytest.mark.mutation_fast
    def test_parameter_types(self) -> None:
        _, graph = _parse_sample()
        fetch = next(n for n in graph.nodes.values() if n.name == "fetch")
        params = {p.name: p for p in fetch.parameters}
        assert params["item"].type_ref is not None
        assert params["item"].type_ref.name == "String"
        assert params["count"].type_ref is not None
        assert params["count"].type_ref.name == "int"

    @pytest.mark.mutation_fast
    def test_return_type(self) -> None:
        _, graph = _parse_sample()
        fetch = next(n for n in graph.nodes.values() if n.name == "fetch")
        assert fetch.return_type is not None
        assert fetch.return_type.name == "boolean"


class TestJavaParserComplexity:
    def test_simple_method_complexity(self) -> None:
        _, graph = _parse_sample()
        speak_methods = [
            n for n in graph.nodes.values() if n.name == "speak" and n.kind == NodeKind.METHOD
        ]
        speak = speak_methods[0]
        assert speak.cyclomatic_complexity == 1

    def test_branching_method_complexity(self) -> None:
        _, graph = _parse_sample()
        fetch = next(n for n in graph.nodes.values() if n.name == "fetch")
        assert fetch.cyclomatic_complexity is not None
        assert fetch.cyclomatic_complexity >= 4

    def test_branches_tracked(self) -> None:
        _, graph = _parse_sample()
        fetch = next(n for n in graph.nodes.values() if n.name == "fetch")
        assert len(fetch.branches) > 0


class TestJavaParserEdges:
    def test_contains_edges(self) -> None:
        _, graph = _parse_sample()
        contains = [e for e in graph.edges if e.kind == EdgeKind.CONTAINS]
        assert len(contains) > 0

    def test_inherits_edge(self) -> None:
        _, graph = _parse_sample()
        inherits = [e for e in graph.edges if e.kind == EdgeKind.INHERITS]
        assert len(inherits) >= 1
        targets = {e.target_id for e in inherits}
        pet_targets = [t for t in targets if t.endswith(":Pet")]
        assert len(pet_targets) >= 1

    @pytest.mark.mutation_fast
    def test_implements_edge(self) -> None:
        _, graph = _parse_sample()
        implements = [e for e in graph.edges if e.kind == EdgeKind.IMPLEMENTS]
        assert len(implements) >= 1

    def test_call_edges(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        assert len(calls) > 0

    def test_exception_type(self) -> None:
        _, graph = _parse_sample()
        fetch = next(n for n in graph.nodes.values() if n.name == "fetch")
        assert len(fetch.exception_types) >= 1

    def test_edge_confidence(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        certain = [e for e in calls if e.confidence == EdgeConfidence.CERTAIN]
        inferred = [e for e in calls if e.confidence == EdgeConfidence.INFERRED]
        assert len(certain) > 0 or len(inferred) > 0


class TestJavaParserDependencies:
    @pytest.mark.mutation_fast
    def test_imports_tracked(self) -> None:
        _, graph = _parse_sample()
        assert "java" in graph.dependencies


GENERIC_JAVA_CODE = """\
import java.util.List;
import java.util.Map;

class Container {
    public List<String> getItems(Map<String, Integer> lookup) {
        return null;
    }
}
"""


def _parse_generic() -> CodeGraph:
    parser = JavaParser()
    with tempfile.NamedTemporaryFile(
        suffix=".java",
        mode="w",
        delete=False,
    ) as f:
        f.write(GENERIC_JAVA_CODE)
        f.flush()
        graph = parser.parse_file(f.name)
    os.unlink(f.name)
    return graph


class TestJavaGenericTypes:
    @pytest.mark.mutation_fast
    def test_generic_parameter_type(self) -> None:
        graph = _parse_generic()
        get_items = next(n for n in graph.nodes.values() if n.name == "getItems")
        assert len(get_items.parameters) == 1
        param = get_items.parameters[0]
        assert param.type_ref is not None
        assert param.type_ref.name == "Map"
        assert len(param.type_ref.generic_args) == 2

    def test_generic_return_type(self) -> None:
        graph = _parse_generic()
        get_items = next(n for n in graph.nodes.values() if n.name == "getItems")
        assert get_items.return_type is not None
        assert get_items.return_type.name == "List"
        assert len(get_items.return_type.generic_args) == 1


class TestJavaParseDirectory:
    @pytest.mark.mutation_fast
    def test_parses_multiple_files(self) -> None:
        parser = JavaParser()
        code_a = "class FromA {}\n"
        code_b = "class FromB {}\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            for name, code in [("a.java", code_a), ("b.java", code_b)]:
                path = os.path.join(tmpdir, name)
                with open(path, "w") as f:
                    f.write(code)
            graph = parser.parse_directory(tmpdir)
        assert graph.language == "java"
        assert graph.root_path == tmpdir
        names = {n.name for n in graph.nodes.values()}
        assert "FromA" in names
        assert "FromB" in names

    def test_ignores_wrong_extensions(self) -> None:
        parser = JavaParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "skip.txt")
            with open(path, "w") as f:
                f.write("not source code")
            graph = parser.parse_directory(tmpdir)
        assert len(graph.nodes) == 0
