"""Tests for the C++ language parser."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind
from trailmark.parsers.cpp.parser import CppParser
from trailmark.query.api import QueryEngine

SAMPLE_CODE = """\
#include <iostream>
#include "utils.hpp"

namespace geometry {

struct Point {
    double x;
    double y;
};

enum Shape {
    CIRCLE,
    SQUARE,
    TRIANGLE
};

/** Base class for all animals. */
class Animal {
public:
    virtual void speak() {
        return;
    }

    int age() {
        return 0;
    }
};

class Dog : public Animal {
public:
    void speak() {
        return;
    }

    bool fetch(int item) {
        if (item <= 0) {
            throw std::invalid_argument("bad item");
        }
        return true;
    }
};

}

int compute(int a, int b) {
    int result = a + b;
    if (result > 100) {
        result = 100;
    }
    for (int i = 0; i < b; i++) {
        if (a > i) {
            result += i;
        }
    }
    while (result < 0) {
        result += 10;
    }
    return result;
}

void run_all(void) {
    int val = compute(10, 20);
    std::cout << val << std::endl;
}
"""


def _parse_sample() -> tuple[CppParser, CodeGraph]:
    parser = CppParser()
    with tempfile.NamedTemporaryFile(
        suffix=".cpp",
        mode="w",
        delete=False,
    ) as f:
        f.write(SAMPLE_CODE)
        f.flush()
        graph = parser.parse_file(f.name)
    os.unlink(f.name)
    return parser, graph


class TestCppParserNodes:
    @pytest.mark.mutation_fast
    def test_finds_module(self) -> None:
        _, graph = _parse_sample()
        modules = [n for n in graph.nodes.values() if n.kind == NodeKind.MODULE]
        assert len(modules) == 1

    @pytest.mark.mutation_fast
    def test_finds_namespace(self) -> None:
        _, graph = _parse_sample()
        nss = [n for n in graph.nodes.values() if n.kind == NodeKind.NAMESPACE]
        names = {n.name for n in nss}
        assert "geometry" in names

    def test_finds_classes(self) -> None:
        _, graph = _parse_sample()
        classes = [n for n in graph.nodes.values() if n.kind == NodeKind.CLASS]
        names = {c.name for c in classes}
        assert "Animal" in names
        assert "Dog" in names

    def test_finds_struct(self) -> None:
        _, graph = _parse_sample()
        structs = [n for n in graph.nodes.values() if n.kind == NodeKind.STRUCT]
        names = {s.name for s in structs}
        assert "Point" in names

    def test_finds_enum(self) -> None:
        _, graph = _parse_sample()
        enums = [n for n in graph.nodes.values() if n.kind == NodeKind.ENUM]
        names = {e.name for e in enums}
        assert "Shape" in names

    @pytest.mark.mutation_fast
    def test_finds_functions(self) -> None:
        _, graph = _parse_sample()
        funcs = [n for n in graph.nodes.values() if n.kind == NodeKind.FUNCTION]
        names = {f.name for f in funcs}
        assert "compute" in names
        assert "run_all" in names

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
        assert "animal" in animal.docstring.lower()


class TestCppParserParameters:
    @pytest.mark.mutation_fast
    def test_function_parameters(self) -> None:
        _, graph = _parse_sample()
        comp = next(n for n in graph.nodes.values() if n.name == "compute")
        assert len(comp.parameters) == 2
        names = {p.name for p in comp.parameters}
        assert "a" in names
        assert "b" in names

    def test_parameter_types(self) -> None:
        _, graph = _parse_sample()
        comp = next(n for n in graph.nodes.values() if n.name == "compute")
        for p in comp.parameters:
            assert p.type_ref is not None
            assert p.type_ref.name == "int"

    @pytest.mark.mutation_fast
    def test_return_type(self) -> None:
        _, graph = _parse_sample()
        comp = next(n for n in graph.nodes.values() if n.name == "compute")
        assert comp.return_type is not None
        assert comp.return_type.name == "int"

    def test_method_parameters(self) -> None:
        _, graph = _parse_sample()
        fetch = next(n for n in graph.nodes.values() if n.name == "fetch")
        assert len(fetch.parameters) == 1
        assert fetch.parameters[0].name == "item"


class TestCppParserComplexity:
    def test_simple_function_complexity(self) -> None:
        _, graph = _parse_sample()
        run_fn = next(n for n in graph.nodes.values() if n.name == "run_all")
        assert run_fn.cyclomatic_complexity == 1

    def test_branching_function_complexity(self) -> None:
        _, graph = _parse_sample()
        comp = next(n for n in graph.nodes.values() if n.name == "compute")
        assert comp.cyclomatic_complexity is not None
        assert comp.cyclomatic_complexity >= 4

    def test_branches_tracked(self) -> None:
        _, graph = _parse_sample()
        comp = next(n for n in graph.nodes.values() if n.name == "compute")
        assert len(comp.branches) > 0


class TestCppParserEdges:
    def test_contains_edges(self) -> None:
        _, graph = _parse_sample()
        contains = [e for e in graph.edges if e.kind == EdgeKind.CONTAINS]
        assert len(contains) > 0

    @pytest.mark.mutation_fast
    def test_inherits_edge(self) -> None:
        _, graph = _parse_sample()
        inherits = [e for e in graph.edges if e.kind == EdgeKind.INHERITS]
        assert len(inherits) >= 1
        targets = {e.target_id for e in inherits}
        has_animal = any("Animal" in t for t in targets)
        assert has_animal

    def test_call_edges(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        assert len(calls) > 0

    def test_exception_type_from_throw(self) -> None:
        _, graph = _parse_sample()
        fetch = next(n for n in graph.nodes.values() if n.name == "fetch")
        assert len(fetch.exception_types) >= 1

    @pytest.mark.mutation_fast
    def test_edge_confidence(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        certain = [e for e in calls if e.confidence == EdgeConfidence.CERTAIN]
        inferred = [e for e in calls if e.confidence == EdgeConfidence.INFERRED]
        assert len(certain) > 0 or len(inferred) > 0


class TestCppParserDependencies:
    def test_includes_tracked(self) -> None:
        _, graph = _parse_sample()
        assert "iostream" in graph.dependencies
        assert "utils.hpp" in graph.dependencies


EXTRA_CPP_CODE = """\
template<typename T>
T identity(T x) {
    return x;
}

template<typename T>
class Box {
public:
    T value;
    T get() { return value; }
};

typedef struct TypedefPoint {
    int x;
    int y;
} TypedefPoint;

typedef enum TypedefColor {
    RED,
    GREEN,
    BLUE
} TypedefColor;

class WithInline {
public:
    static void staticHelper() { return; }
};
"""


def _parse_extra() -> CodeGraph:
    parser = CppParser()
    with tempfile.NamedTemporaryFile(
        suffix=".cpp",
        mode="w",
        delete=False,
    ) as f:
        f.write(EXTRA_CPP_CODE)
        f.flush()
        graph = parser.parse_file(f.name)
    os.unlink(f.name)
    return graph


class TestCppExtraFeatures:
    @pytest.mark.mutation_fast
    def test_template_function_extracted(self) -> None:
        graph = _parse_extra()
        names = {n.name for n in graph.nodes.values()}
        assert "identity" in names

    @pytest.mark.mutation_fast
    def test_template_class_extracted(self) -> None:
        graph = _parse_extra()
        names = {n.name for n in graph.nodes.values()}
        assert "Box" in names

    def test_template_class_method(self) -> None:
        graph = _parse_extra()
        names = {n.name for n in graph.nodes.values()}
        assert "get" in names

    @pytest.mark.mutation_fast
    def test_typedef_struct_extracted(self) -> None:
        graph = _parse_extra()
        structs = [n for n in graph.nodes.values() if n.kind == NodeKind.STRUCT]
        names = {s.name for s in structs}
        assert "TypedefPoint" in names

    @pytest.mark.mutation_fast
    def test_typedef_enum_extracted(self) -> None:
        graph = _parse_extra()
        enums = [n for n in graph.nodes.values() if n.kind == NodeKind.ENUM]
        names = {e.name for e in enums}
        assert "TypedefColor" in names

    def test_inline_declaration_method(self) -> None:
        graph = _parse_extra()
        names = {n.name for n in graph.nodes.values()}
        assert "WithInline" in names


class TestCppParseDirectory:
    @pytest.mark.mutation_fast
    def test_parses_multiple_files(self) -> None:
        parser = CppParser()
        code_a = "void fromA() {}\n"
        code_b = "void fromB() {}\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            for name, code in [("a.cpp", code_a), ("b.cpp", code_b)]:
                path = os.path.join(tmpdir, name)
                with open(path, "w") as f:
                    f.write(code)
            graph = parser.parse_directory(tmpdir)
        assert graph.language == "cpp"
        assert graph.root_path == tmpdir
        names = {n.name for n in graph.nodes.values()}
        assert "fromA" in names
        assert "fromB" in names

    def test_ignores_wrong_extensions(self) -> None:
        parser = CppParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "skip.txt")
            with open(path, "w") as f:
                f.write("not source code")
            graph = parser.parse_directory(tmpdir)
        assert len(graph.nodes) == 0


@pytest.mark.mutation_fast
def test_out_of_line_methods_bind_same_class_calls(tmp_path: Path) -> None:
    source = tmp_path / "stream.cpp"
    source.write_text(
        "class Stream { public: void flush(); ~Stream(); };\n"
        "void Stream::flush() {}\n"
        "Stream::~Stream() { flush(); }\n"
    )
    graph = CppParser().parse_file(str(source))
    destructor = graph.nodes["stream:Stream::~Stream"]
    engine = QueryEngine.from_graph(graph)
    assert {n["id"] for n in engine.callers_of("stream:Stream::flush")} == {destructor.id}
    assert destructor.name == "~Stream" and destructor.kind == NodeKind.METHOD
    assert any(
        e.kind == EdgeKind.CONTAINS
        and e.source_id == "stream:Stream"
        and e.target_id == destructor.id
        for e in graph.edges
    )
    assert any(
        e.kind == EdgeKind.CALLS
        and e.source_id == destructor.id
        and e.target_id == "stream:Stream::flush"
        for e in graph.edges
    )


@pytest.mark.mutation_fast
def test_cross_file_out_of_line_method_owner_is_unique(tmp_path: Path) -> None:
    (tmp_path / "stream.hpp").write_text(
        "namespace io { class Stream { public: void flush() {} void run(); }; }\n"
    )
    (tmp_path / "stream.cpp").write_text("void io::Stream::run() { flush(); }\n")
    graph = CppParser().parse_directory(str(tmp_path))
    assert graph.nodes["stream\\.cpp:io::Stream::run"].kind == NodeKind.METHOD
    assert any(
        e.kind == EdgeKind.CALLS and e.target_id == "stream\\.hpp:io.Stream.flush"
        for e in graph.edges
    )


def test_qualified_namespace_function_does_not_become_method(tmp_path: Path) -> None:
    source = tmp_path / "app.cpp"
    source.write_text("namespace io { void run(); }\nvoid io::run() {}\n")
    graph = CppParser().parse_file(str(source))
    assert graph.nodes["app:io::run"].kind == NodeKind.FUNCTION


def test_ambiguous_cross_file_class_owner_remains_unbound(tmp_path: Path) -> None:
    (tmp_path / "first.hpp").write_text("class Stream { public: void flush() {} };\n")
    (tmp_path / "second.hpp").write_text("class Stream { public: void flush() {} };\n")
    (tmp_path / "impl.cpp").write_text("void Stream::run() { flush(); }\n")
    graph = CppParser().parse_directory(str(tmp_path))
    assert graph.nodes["impl:Stream::run"].kind == NodeKind.FUNCTION
    assert not any(
        e.kind == EdgeKind.CONTAINS
        and e.source_id.endswith(":Stream")
        and e.target_id == "impl:Stream::run"
        for e in graph.edges
    )


@pytest.mark.mutation_fast
def test_out_of_line_method_keeps_nested_namespace_and_value_barriers(tmp_path: Path) -> None:
    source = tmp_path / "app.cpp"
    source.write_text(
        "namespace io { class Stream { public: void flush() {} void run(); };\n"
        "void Stream::run() { auto flush = [] {}; flush(); } }\n"
    )
    graph = CppParser().parse_file(str(source))
    assert graph.nodes["app:io.Stream::run"].kind == NodeKind.METHOD
    assert not any(
        e.kind == EdgeKind.CALLS
        and e.source_id == "app:io.Stream::run"
        and e.target_id == "app:io.Stream.flush"
        for e in graph.edges
    )


def test_out_of_line_method_overloads_remain_uncertain(tmp_path: Path) -> None:
    source = tmp_path / "app.cpp"
    source.write_text(
        "class Stream { public: void flush(int); void flush(double); void run(); };\n"
        "void Stream::flush(int x) {}\n"
        "void Stream::flush(double x) {}\n"
        "void Stream::run() { flush(1); }\n"
    )
    graph = CppParser().parse_directory(str(tmp_path))
    targets = {
        n.id for n in graph.nodes.values() if n.kind == NodeKind.METHOD and n.name == "flush"
    }
    calls = [
        e for e in graph.edges if e.source_id == "app:Stream::run" and e.kind == EdgeKind.CALLS
    ]
    assert len(targets) == 2 and len(calls) == 1
    assert calls[0].target_id not in targets
    assert calls[0].confidence == EdgeConfidence.UNCERTAIN


@pytest.mark.mutation_fast
def test_out_of_line_method_callback_parameter_stays_blocked(tmp_path: Path) -> None:
    source = tmp_path / "app.cpp"
    source.write_text(
        "class Stream { public: void flush() {} void run(void (*flush)()); };\n"
        "void Stream::run(void (*flush)()) { flush(); }\n"
    )
    graph = CppParser().parse_file(str(source))
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 1
    assert dict(calls[0].attributes)["binding_blocked"]
    assert calls[0].target_id != "app:Stream.flush"


def test_class_namespace_owner_collision_stays_unattached(tmp_path: Path) -> None:
    (tmp_path / "type.hpp").write_text("class Stream { public: void flush() {} };\n")
    (tmp_path / "namespace.hpp").write_text("namespace Stream { void flush() {} }\n")
    (tmp_path / "impl.cpp").write_text("void Stream::run() { flush(); }\n")
    graph = CppParser().parse_directory(str(tmp_path))
    assert graph.nodes["impl:Stream::run"].kind == NodeKind.FUNCTION


def test_header_prototypes_do_not_compete_with_out_of_line_definitions(tmp_path: Path) -> None:
    (tmp_path / "type.hpp").write_text("class Stream { public: void flush(); void run(); };\n")
    (tmp_path / "impl.cpp").write_text("void Stream::flush() {}\nvoid Stream::run() { flush(); }\n")
    graph = CppParser().parse_directory(str(tmp_path))
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 1
    assert calls[0].source_id == "impl:Stream::run"
    assert calls[0].target_id == "impl:Stream::flush"
    assert calls[0].location is not None and calls[0].location.start_line == 2
