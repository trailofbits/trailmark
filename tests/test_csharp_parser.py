"""Tests for the C# language parser."""

from __future__ import annotations

import os
import tempfile

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind
from trailmark.parsers.csharp.parser import CSharpParser

SAMPLE_CODE = """\
using System;
using System.Collections.Generic;

namespace Animals
{
    /// <summary>Defines animal behavior.</summary>
    public interface IAnimal
    {
        string Speak();
    }

    /// <summary>A dog that implements IAnimal.</summary>
    public class Dog : IAnimal
    {
        private string name;

        public Dog(string name)
        {
            this.name = name;
        }

        public string Speak()
        {
            return "woof";
        }

        public bool Fetch(string item, int count)
        {
            if (string.IsNullOrEmpty(item))
            {
                throw new ArgumentException("empty");
            }
            for (int i = 0; i < count; i++)
            {
                Console.WriteLine(item);
            }
            return true;
        }
    }

    public class Puppy : Dog
    {
        public Puppy(string name) : base(name) {}

        public string Play()
        {
            if (name != null)
            {
                return "playing";
            }
            return "sleeping";
        }
    }

    public enum AnimalType
    {
        Dog,
        Cat,
        Bird
    }
}
"""


def _parse_sample() -> tuple[CSharpParser, CodeGraph]:
    parser = CSharpParser()
    with tempfile.NamedTemporaryFile(
        suffix=".cs",
        mode="w",
        delete=False,
    ) as f:
        f.write(SAMPLE_CODE)
        f.flush()
        graph = parser.parse_file(f.name)
    os.unlink(f.name)
    return parser, graph


class TestCSharpParserNodes:
    def test_finds_module(self) -> None:
        _, graph = _parse_sample()
        modules = [n for n in graph.nodes.values() if n.kind == NodeKind.MODULE]
        assert len(modules) == 1

    def test_finds_namespace(self) -> None:
        _, graph = _parse_sample()
        namespaces = [n for n in graph.nodes.values() if n.kind == NodeKind.NAMESPACE]
        names = {n.name for n in namespaces}
        assert "Animals" in names

    def test_finds_interface(self) -> None:
        _, graph = _parse_sample()
        interfaces = [n for n in graph.nodes.values() if n.kind == NodeKind.INTERFACE]
        names = {i.name for i in interfaces}
        assert "IAnimal" in names

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
        assert "Speak" in names
        assert "Fetch" in names
        assert "Play" in names


class TestCSharpParserParameters:
    def test_typed_parameters(self) -> None:
        _, graph = _parse_sample()
        fetch = next(n for n in graph.nodes.values() if n.name == "Fetch")
        assert len(fetch.parameters) == 2
        param_names = {p.name for p in fetch.parameters}
        assert "item" in param_names
        assert "count" in param_names

    def test_parameter_types(self) -> None:
        _, graph = _parse_sample()
        fetch = next(n for n in graph.nodes.values() if n.name == "Fetch")
        params = {p.name: p for p in fetch.parameters}
        assert params["item"].type_ref is not None
        assert params["item"].type_ref.name == "string"
        assert params["count"].type_ref is not None
        assert params["count"].type_ref.name == "int"

    def test_return_type(self) -> None:
        _, graph = _parse_sample()
        fetch = next(n for n in graph.nodes.values() if n.name == "Fetch")
        assert fetch.return_type is not None
        assert fetch.return_type.name == "bool"


class TestCSharpParserComplexity:
    def test_simple_method_complexity(self) -> None:
        _, graph = _parse_sample()
        speak = next(
            n for n in graph.nodes.values() if n.name == "Speak" and n.kind == NodeKind.METHOD
        )
        assert speak.cyclomatic_complexity == 1

    def test_branching_method_complexity(self) -> None:
        _, graph = _parse_sample()
        fetch = next(n for n in graph.nodes.values() if n.name == "Fetch")
        assert fetch.cyclomatic_complexity is not None
        assert fetch.cyclomatic_complexity >= 3

    def test_branches_tracked(self) -> None:
        _, graph = _parse_sample()
        fetch = next(n for n in graph.nodes.values() if n.name == "Fetch")
        assert len(fetch.branches) > 0


class TestCSharpParserEdges:
    def test_contains_edges(self) -> None:
        _, graph = _parse_sample()
        contains = [e for e in graph.edges if e.kind == EdgeKind.CONTAINS]
        assert len(contains) > 0

    def test_inherits_edge(self) -> None:
        _, graph = _parse_sample()
        inherits = [e for e in graph.edges if e.kind == EdgeKind.INHERITS]
        assert len(inherits) >= 1

    def test_call_edges(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        assert len(calls) > 0

    def test_exception_type(self) -> None:
        _, graph = _parse_sample()
        fetch = next(n for n in graph.nodes.values() if n.name == "Fetch")
        assert len(fetch.exception_types) >= 1

    def test_edge_confidence(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        certain = [e for e in calls if e.confidence == EdgeConfidence.CERTAIN]
        inferred = [e for e in calls if e.confidence == EdgeConfidence.INFERRED]
        assert len(certain) > 0 or len(inferred) > 0


class TestCSharpParserDependencies:
    def test_imports_tracked(self) -> None:
        _, graph = _parse_sample()
        assert "System" in graph.dependencies


GENERIC_CS_CODE = """\
using System.Collections.Generic;

namespace Generics
{
    public class Container
    {
        public List<int> GetItems(Dictionary<string, int> lookup)
        {
            return new List<int>();
        }
    }
}
"""


def _parse_generic() -> CodeGraph:
    parser = CSharpParser()
    with tempfile.NamedTemporaryFile(
        suffix=".cs",
        mode="w",
        delete=False,
    ) as f:
        f.write(GENERIC_CS_CODE)
        f.flush()
        graph = parser.parse_file(f.name)
    os.unlink(f.name)
    return graph


class TestCSharpGenericTypes:
    def test_generic_parameter_type(self) -> None:
        graph = _parse_generic()
        get_items = next(n for n in graph.nodes.values() if n.name == "GetItems")
        assert len(get_items.parameters) == 1
        param = get_items.parameters[0]
        assert param.type_ref is not None
        assert param.type_ref.name == "Dictionary"
        assert len(param.type_ref.generic_args) == 2

    def test_generic_return_type(self) -> None:
        graph = _parse_generic()
        get_items = next(n for n in graph.nodes.values() if n.name == "GetItems")
        assert get_items.return_type is not None
        assert get_items.return_type.name == "List"
        assert len(get_items.return_type.generic_args) == 1


FILE_SCOPED_NAMESPACE_CODE = """\
using System;

namespace Animals;

/// <summary>Defines animal behavior.</summary>
public interface IAnimal
{
    string Speak();
}

/// <summary>A dog that implements IAnimal.</summary>
public class Dog : IAnimal
{
    public string Speak()
    {
        return "woof";
    }
}
"""


def _parse_file_scoped_namespace() -> tuple[str, CodeGraph]:
    parser = CSharpParser()
    with tempfile.NamedTemporaryFile(
        suffix=".cs",
        mode="w",
        delete=False,
    ) as f:
        f.write(FILE_SCOPED_NAMESPACE_CODE)
        f.flush()
        graph = parser.parse_file(f.name)
    os.unlink(f.name)
    return f.name, graph


def _contains_count(graph: CodeGraph, source_id: str, target_id: str) -> int:
    return sum(
        1
        for e in graph.edges
        if e.kind == EdgeKind.CONTAINS and e.source_id == source_id and e.target_id == target_id
    )


class TestCSharpFileScopedNamespace:
    def test_finds_namespace(self) -> None:
        _, graph = _parse_file_scoped_namespace()
        namespaces = [n for n in graph.nodes.values() if n.kind == NodeKind.NAMESPACE]
        names = {n.name for n in namespaces}
        assert "Animals" in names

    def test_module_contains_namespace(self) -> None:
        path, graph = _parse_file_scoped_namespace()
        module = next(n for n in graph.nodes.values() if n.kind == NodeKind.MODULE)
        namespace = next(n for n in graph.nodes.values() if n.kind == NodeKind.NAMESPACE)
        assert namespace.id == f"{module.id}:Animals"
        assert namespace.location.file_path == path
        assert _contains_count(graph, module.id, namespace.id) == 1

    def test_class_attributed_to_namespace(self) -> None:
        path, graph = _parse_file_scoped_namespace()
        module = next(n for n in graph.nodes.values() if n.kind == NodeKind.MODULE)
        namespace = next(n for n in graph.nodes.values() if n.kind == NodeKind.NAMESPACE)
        dog = next(n for n in graph.nodes.values() if n.name == "Dog")
        assert dog.id == f"{module.id}:Dog"
        assert dog.location.file_path == path
        assert _contains_count(graph, namespace.id, dog.id) == 1
        assert _contains_count(graph, module.id, dog.id) == 0

    def test_interface_attributed_to_namespace(self) -> None:
        _, graph = _parse_file_scoped_namespace()
        module = next(n for n in graph.nodes.values() if n.kind == NodeKind.MODULE)
        namespace = next(n for n in graph.nodes.values() if n.kind == NodeKind.NAMESPACE)
        animal = next(n for n in graph.nodes.values() if n.name == "IAnimal")
        assert animal.id == f"{module.id}:IAnimal"
        assert _contains_count(graph, namespace.id, animal.id) == 1
        assert _contains_count(graph, module.id, animal.id) == 0

    def test_using_before_namespace_still_imported(self) -> None:
        _, graph = _parse_file_scoped_namespace()
        assert "System" in graph.dependencies

    def test_method_still_found(self) -> None:
        _, graph = _parse_file_scoped_namespace()
        methods = [n for n in graph.nodes.values() if n.kind == NodeKind.METHOD]
        names = {m.name for m in methods}
        assert "Speak" in names


class TestCSharpParseDirectory:
    def test_parses_multiple_files(self) -> None:
        parser = CSharpParser()
        code_a = "class FromA {}\n"
        code_b = "class FromB {}\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            for name, code in [("a.cs", code_a), ("b.cs", code_b)]:
                path = os.path.join(tmpdir, name)
                with open(path, "w") as f:
                    f.write(code)
            graph = parser.parse_directory(tmpdir)
        assert graph.language == "csharp"
        assert graph.root_path == tmpdir
        names = {n.name for n in graph.nodes.values()}
        assert "FromA" in names
        assert "FromB" in names

    def test_ignores_wrong_extensions(self) -> None:
        parser = CSharpParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "skip.txt")
            with open(path, "w") as f:
                f.write("not source code")
            graph = parser.parse_directory(tmpdir)
        assert len(graph.nodes) == 0
