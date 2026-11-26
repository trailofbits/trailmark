"""Tests for the Ruby language parser."""

from __future__ import annotations

import os
import tempfile

import pytest

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind
from trailmark.parsers.ruby.parser import RubyParser

SAMPLE_CODE = """\
require 'json'
require_relative 'helpers/utils'

# A base animal class.
class Animal
  # Make the animal speak.
  def speak
    "..."
  end
end

class Dog < Animal
  def speak
    "woof"
  end

  def fetch(item)
    if item.nil?
      raise ArgumentError, "empty item"
    end
    unless item.empty?
      puts item
    end
    true
  end
end

def greet(name, loud = false)
  if loud
    name.upcase
  else
    name
  end
end

def process(items)
  total = 0
  items.each do |item|
    if item > 0
      total += item
    end
  end
  while total > 100
    total -= 10
  end
  total
end
"""


def _parse_sample() -> tuple[RubyParser, CodeGraph]:
    parser = RubyParser()
    with tempfile.NamedTemporaryFile(
        suffix=".rb",
        mode="w",
        delete=False,
    ) as f:
        f.write(SAMPLE_CODE)
        f.flush()
        graph = parser.parse_file(f.name)
    os.unlink(f.name)
    return parser, graph


class TestRubyParserNodes:
    @pytest.mark.mutation_fast
    def test_finds_module(self) -> None:
        _, graph = _parse_sample()
        modules = [n for n in graph.nodes.values() if n.kind == NodeKind.MODULE]
        assert len(modules) == 1

    @pytest.mark.mutation_fast
    def test_finds_classes(self) -> None:
        _, graph = _parse_sample()
        classes = [n for n in graph.nodes.values() if n.kind == NodeKind.CLASS]
        names = {c.name for c in classes}
        assert "Animal" in names
        assert "Dog" in names

    def test_finds_functions(self) -> None:
        _, graph = _parse_sample()
        funcs = [n for n in graph.nodes.values() if n.kind == NodeKind.FUNCTION]
        names = {f.name for f in funcs}
        assert "greet" in names
        assert "process" in names

    @pytest.mark.mutation_fast
    def test_finds_methods(self) -> None:
        _, graph = _parse_sample()
        methods = [n for n in graph.nodes.values() if n.kind == NodeKind.METHOD]
        names = {m.name for m in methods}
        assert "speak" in names
        assert "fetch" in names

    @pytest.mark.mutation_fast
    def test_class_docstring(self) -> None:
        _, graph = _parse_sample()
        animal = next(n for n in graph.nodes.values() if n.name == "Animal")
        assert animal.docstring is not None
        assert "base animal" in animal.docstring.lower()


class TestRubyParserParameters:
    @pytest.mark.mutation_fast
    def test_simple_parameter(self) -> None:
        _, graph = _parse_sample()
        fetch = next(n for n in graph.nodes.values() if n.name == "fetch")
        assert len(fetch.parameters) == 1
        assert fetch.parameters[0].name == "item"

    @pytest.mark.mutation_fast
    def test_default_parameter(self) -> None:
        _, graph = _parse_sample()
        greet = next(n for n in graph.nodes.values() if n.name == "greet")
        params = {p.name: p for p in greet.parameters}
        assert "name" in params
        assert "loud" in params
        assert params["loud"].default == "false"

    def test_no_type_annotations(self) -> None:
        _, graph = _parse_sample()
        greet = next(n for n in graph.nodes.values() if n.name == "greet")
        for p in greet.parameters:
            assert p.type_ref is None


class TestRubyParserComplexity:
    def test_simple_method_complexity(self) -> None:
        _, graph = _parse_sample()
        speak = next(
            n for n in graph.nodes.values() if n.name == "speak" and n.kind == NodeKind.METHOD
        )
        assert speak.cyclomatic_complexity == 1

    @pytest.mark.mutation_fast
    def test_branching_function_complexity(self) -> None:
        _, graph = _parse_sample()
        process = next(n for n in graph.nodes.values() if n.name == "process")
        assert process.cyclomatic_complexity is not None
        assert process.cyclomatic_complexity >= 2

    def test_branches_tracked(self) -> None:
        _, graph = _parse_sample()
        fetch = next(n for n in graph.nodes.values() if n.name == "fetch")
        assert len(fetch.branches) > 0


class TestRubyParserEdges:
    def test_contains_edges(self) -> None:
        _, graph = _parse_sample()
        contains = [e for e in graph.edges if e.kind == EdgeKind.CONTAINS]
        assert len(contains) > 0

    @pytest.mark.mutation_fast
    def test_inherits_edge(self) -> None:
        _, graph = _parse_sample()
        inherits = [e for e in graph.edges if e.kind == EdgeKind.INHERITS]
        assert len(inherits) == 1
        assert inherits[0].target_id.endswith(":Animal")

    @pytest.mark.mutation_fast
    def test_call_edges(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        assert len(calls) > 0

    def test_edge_confidence(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        certain = [e for e in calls if e.confidence == EdgeConfidence.CERTAIN]
        inferred = [e for e in calls if e.confidence == EdgeConfidence.INFERRED]
        assert len(certain) > 0 or len(inferred) > 0


class TestRubyParserDependencies:
    @pytest.mark.mutation_fast
    def test_imports_tracked(self) -> None:
        _, graph = _parse_sample()
        assert "json" in graph.dependencies
        assert "helpers/utils" in graph.dependencies


MODULE_RUBY_CODE = """\
module Utilities
  def helper
    "help"
  end

  class Formatter
    def format(text)
      text.strip
    end
  end
end
"""


def _parse_module() -> CodeGraph:
    parser = RubyParser()
    with tempfile.NamedTemporaryFile(
        suffix=".rb",
        mode="w",
        delete=False,
    ) as f:
        f.write(MODULE_RUBY_CODE)
        f.flush()
        graph = parser.parse_file(f.name)
    os.unlink(f.name)
    return graph


class TestRubyModule:
    def test_module_node_created(self) -> None:
        graph = _parse_module()
        modules = [
            n for n in graph.nodes.values() if n.kind == NodeKind.MODULE and n.name == "Utilities"
        ]
        assert len(modules) == 1

    def test_method_inside_module(self) -> None:
        graph = _parse_module()
        methods = [n for n in graph.nodes.values() if n.kind == NodeKind.METHOD]
        names = {m.name for m in methods}
        assert "helper" in names

    @pytest.mark.mutation_fast
    def test_class_inside_module(self) -> None:
        graph = _parse_module()
        classes = [n for n in graph.nodes.values() if n.kind == NodeKind.CLASS]
        names = {c.name for c in classes}
        assert "Formatter" in names


class TestRubyParseDirectory:
    @pytest.mark.mutation_fast
    def test_parses_multiple_files(self) -> None:
        parser = RubyParser()
        code_a = "def from_a\n  1\nend\n"
        code_b = "def from_b\n  2\nend\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            for name, code in [("a.rb", code_a), ("b.rb", code_b)]:
                path = os.path.join(tmpdir, name)
                with open(path, "w") as f:
                    f.write(code)
            graph = parser.parse_directory(tmpdir)
        assert graph.language == "ruby"
        assert graph.root_path == tmpdir
        names = {n.name for n in graph.nodes.values()}
        assert "from_a" in names
        assert "from_b" in names

    def test_ignores_wrong_extensions(self) -> None:
        parser = RubyParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "skip.txt")
            with open(path, "w") as f:
                f.write("not source code")
            graph = parser.parse_directory(tmpdir)
        assert len(graph.nodes) == 0
