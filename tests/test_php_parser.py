"""Tests for the PHP language parser."""

from __future__ import annotations

import os
import tempfile

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind
from trailmark.parsers.php.parser import PHPParser

SAMPLE_CODE = """\
<?php

use App\\Models\\Base;
use App\\Contracts\\Speakable;

/** A base animal class. */
class Animal extends Base implements Speakable {
    /** Speak method. */
    public function speak(): string {
        return "...";
    }
}

class Dog extends Animal {
    public function speak(): string {
        return "woof";
    }

    public function fetch(string $item): bool {
        if (!$item) {
            throw new ValueError("empty item");
        }
        return true;
    }
}

function greet(string $name, bool $loud = false): string {
    if ($loud) {
        return strtoupper($name);
    }
    return $name;
}

function process(array $items): int {
    $total = 0;
    foreach ($items as $item) {
        if ($item > 0) {
            $total += $item;
        }
    }
    return $total;
}
"""


def _parse_sample() -> tuple[PHPParser, CodeGraph]:
    parser = PHPParser()
    with tempfile.NamedTemporaryFile(
        suffix=".php",
        mode="w",
        delete=False,
    ) as f:
        f.write(SAMPLE_CODE)
        f.flush()
        graph = parser.parse_file(f.name)
    os.unlink(f.name)
    return parser, graph


class TestPHPParserNodes:
    def test_finds_module(self) -> None:
        _, graph = _parse_sample()
        modules = [n for n in graph.nodes.values() if n.kind == NodeKind.MODULE]
        assert len(modules) == 1

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

    def test_finds_methods(self) -> None:
        _, graph = _parse_sample()
        methods = [n for n in graph.nodes.values() if n.kind == NodeKind.METHOD]
        names = {m.name for m in methods}
        assert "speak" in names
        assert "fetch" in names

    def test_class_docstring(self) -> None:
        _, graph = _parse_sample()
        animal = next(n for n in graph.nodes.values() if n.name == "Animal")
        assert animal.docstring is not None
        assert "base animal" in animal.docstring.lower()


class TestPHPParserParameters:
    def test_typed_parameter(self) -> None:
        _, graph = _parse_sample()
        fetch = next(n for n in graph.nodes.values() if n.name == "fetch")
        assert len(fetch.parameters) == 1
        assert fetch.parameters[0].name == "item"
        assert fetch.parameters[0].type_ref is not None
        assert fetch.parameters[0].type_ref.name == "string"

    def test_default_parameter(self) -> None:
        _, graph = _parse_sample()
        greet = next(n for n in graph.nodes.values() if n.name == "greet")
        params = {p.name: p for p in greet.parameters}
        assert "name" in params
        assert "loud" in params
        assert params["loud"].default == "false"

    def test_return_type(self) -> None:
        _, graph = _parse_sample()
        greet = next(n for n in graph.nodes.values() if n.name == "greet")
        assert greet.return_type is not None
        assert greet.return_type.name == "string"


class TestPHPParserComplexity:
    def test_simple_method_complexity(self) -> None:
        _, graph = _parse_sample()
        speak = next(
            n for n in graph.nodes.values() if n.name == "speak" and n.kind == NodeKind.METHOD
        )
        assert speak.cyclomatic_complexity == 1

    def test_branching_function_complexity(self) -> None:
        _, graph = _parse_sample()
        process = next(n for n in graph.nodes.values() if n.name == "process")
        assert process.cyclomatic_complexity is not None
        assert process.cyclomatic_complexity >= 3

    def test_branches_tracked(self) -> None:
        _, graph = _parse_sample()
        process = next(n for n in graph.nodes.values() if n.name == "process")
        assert len(process.branches) > 0


class TestPHPParserEdges:
    def test_contains_edges(self) -> None:
        _, graph = _parse_sample()
        contains = [e for e in graph.edges if e.kind == EdgeKind.CONTAINS]
        assert len(contains) > 0

    def test_inherits_edge(self) -> None:
        _, graph = _parse_sample()
        inherits = [e for e in graph.edges if e.kind == EdgeKind.INHERITS]
        assert len(inherits) >= 1
        targets = {e.target_id for e in inherits}
        has_base = any("Base" in t for t in targets)
        has_animal = any("Animal" in t for t in targets)
        assert has_base or has_animal

    def test_implements_edge(self) -> None:
        _, graph = _parse_sample()
        implements = [e for e in graph.edges if e.kind == EdgeKind.IMPLEMENTS]
        assert len(implements) >= 1
        targets = {e.target_id for e in implements}
        assert any("Speakable" in t for t in targets)

    def test_call_edges(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        assert len(calls) > 0

    def test_exception_type(self) -> None:
        _, graph = _parse_sample()
        fetch = next(n for n in graph.nodes.values() if n.name == "fetch")
        assert len(fetch.exception_types) == 1
        assert fetch.exception_types[0].name == "ValueError"

    def test_edge_confidence(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        certain = [e for e in calls if e.confidence == EdgeConfidence.CERTAIN]
        inferred = [e for e in calls if e.confidence == EdgeConfidence.INFERRED]
        assert len(certain) > 0 or len(inferred) > 0


class TestPHPParserDependencies:
    def test_imports_tracked(self) -> None:
        _, graph = _parse_sample()
        assert "App" in graph.dependencies


NAMESPACE_PHP_CODE = """\
<?php

namespace App\\Services {
    class UserService {
        public function find(int $id): string {
            return "user";
        }
    }

    function helper(): void {}
}
"""


def _parse_namespace() -> CodeGraph:
    parser = PHPParser()
    with tempfile.NamedTemporaryFile(
        suffix=".php",
        mode="w",
        delete=False,
    ) as f:
        f.write(NAMESPACE_PHP_CODE)
        f.flush()
        graph = parser.parse_file(f.name)
    os.unlink(f.name)
    return graph


class TestPHPNamespace:
    def test_namespace_node_created(self) -> None:
        graph = _parse_namespace()
        nss = [n for n in graph.nodes.values() if n.kind == NodeKind.NAMESPACE]
        assert len(nss) >= 1
        names = {n.name for n in nss}
        assert "App\\Services" in names

    def test_namespace_contains_edge(self) -> None:
        graph = _parse_namespace()
        contains = [e for e in graph.edges if e.kind == EdgeKind.CONTAINS]
        ns_edges = [
            e for e in contains if "App.Services" in e.source_id or "App.Services" in e.target_id
        ]
        assert len(ns_edges) >= 1

    def test_class_inside_namespace(self) -> None:
        graph = _parse_namespace()
        classes = [n for n in graph.nodes.values() if n.kind == NodeKind.CLASS]
        names = {c.name for c in classes}
        assert "UserService" in names


class TestPHPParseDirectory:
    def test_parses_multiple_files(self) -> None:
        parser = PHPParser()
        code_a = "<?php\nfunction fromA(): void {}\n"
        code_b = "<?php\nfunction fromB(): void {}\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            for name, code in [("a.php", code_a), ("b.php", code_b)]:
                path = os.path.join(tmpdir, name)
                with open(path, "w") as f:
                    f.write(code)
            graph = parser.parse_directory(tmpdir)
        assert graph.language == "php"
        assert graph.root_path == tmpdir
        names = {n.name for n in graph.nodes.values()}
        assert "fromA" in names
        assert "fromB" in names

    def test_ignores_wrong_extensions(self) -> None:
        parser = PHPParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "skip.txt")
            with open(path, "w") as f:
                f.write("not source code")
            graph = parser.parse_directory(tmpdir)
        assert len(graph.nodes) == 0
