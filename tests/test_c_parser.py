"""Tests for the C language parser."""

from __future__ import annotations

import os
import tempfile

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind
from trailmark.parsers.c.parser import CParser

SAMPLE_CODE = """\
#include <stdio.h>
#include "myheader.h"

typedef struct {
    int x;
    int y;
} Point;

struct Color {
    int r;
    int g;
    int b;
};

enum Direction {
    NORTH,
    SOUTH,
    EAST,
    WEST
};

/** Compute the distance between two points. */
int distance(int x1, int y1, int x2, int y2) {
    int dx = x2 - x1;
    int dy = y2 - y1;
    if (dx < 0) {
        dx = -dx;
    }
    if (dy < 0) {
        dy = -dy;
    }
    return dx + dy;
}

int process(int *items, int count) {
    int total = 0;
    for (int i = 0; i < count; i++) {
        if (items[i] > 0) {
            total += items[i];
        } else {
            total -= items[i];
        }
    }
    while (total > 100) {
        total = total / 2;
    }
    return total;
}

void run(void) {
    int d = distance(0, 0, 3, 4);
    int result = process(&d, 1);
    printf("result: %d\\n", result);
}
"""


def _parse_sample() -> tuple[CParser, CodeGraph]:
    parser = CParser()
    with tempfile.NamedTemporaryFile(
        suffix=".c",
        mode="w",
        delete=False,
    ) as f:
        f.write(SAMPLE_CODE)
        f.flush()
        graph = parser.parse_file(f.name)
    os.unlink(f.name)
    return parser, graph


class TestCParserNodes:
    def test_finds_module(self) -> None:
        _, graph = _parse_sample()
        modules = [n for n in graph.nodes.values() if n.kind == NodeKind.MODULE]
        assert len(modules) == 1

    def test_finds_functions(self) -> None:
        _, graph = _parse_sample()
        funcs = [n for n in graph.nodes.values() if n.kind == NodeKind.FUNCTION]
        names = {f.name for f in funcs}
        assert "distance" in names
        assert "process" in names
        assert "run" in names

    def test_finds_struct(self) -> None:
        _, graph = _parse_sample()
        structs = [n for n in graph.nodes.values() if n.kind == NodeKind.STRUCT]
        names = {s.name for s in structs}
        assert "Color" in names

    def test_finds_enum(self) -> None:
        _, graph = _parse_sample()
        enums = [n for n in graph.nodes.values() if n.kind == NodeKind.ENUM]
        names = {e.name for e in enums}
        assert "Direction" in names

    def test_function_docstring(self) -> None:
        _, graph = _parse_sample()
        dist = next(n for n in graph.nodes.values() if n.name == "distance")
        assert dist.docstring is not None
        assert "distance" in dist.docstring.lower()


class TestCParserParameters:
    def test_function_parameters(self) -> None:
        _, graph = _parse_sample()
        dist = next(n for n in graph.nodes.values() if n.name == "distance")
        assert len(dist.parameters) == 4
        names = {p.name for p in dist.parameters}
        assert "x1" in names
        assert "y1" in names
        assert "x2" in names
        assert "y2" in names

    def test_parameter_types(self) -> None:
        _, graph = _parse_sample()
        dist = next(n for n in graph.nodes.values() if n.name == "distance")
        for p in dist.parameters:
            assert p.type_ref is not None
            assert p.type_ref.name == "int"

    def test_return_type(self) -> None:
        _, graph = _parse_sample()
        dist = next(n for n in graph.nodes.values() if n.name == "distance")
        assert dist.return_type is not None
        assert dist.return_type.name == "int"


class TestCParserComplexity:
    def test_simple_function_complexity(self) -> None:
        _, graph = _parse_sample()
        run_fn = next(n for n in graph.nodes.values() if n.name == "run")
        assert run_fn.cyclomatic_complexity == 1

    def test_branching_function_complexity(self) -> None:
        _, graph = _parse_sample()
        proc = next(n for n in graph.nodes.values() if n.name == "process")
        assert proc.cyclomatic_complexity is not None
        assert proc.cyclomatic_complexity >= 4

    def test_branches_tracked(self) -> None:
        _, graph = _parse_sample()
        proc = next(n for n in graph.nodes.values() if n.name == "process")
        assert len(proc.branches) > 0


class TestCParserEdges:
    def test_contains_edges(self) -> None:
        _, graph = _parse_sample()
        contains = [e for e in graph.edges if e.kind == EdgeKind.CONTAINS]
        assert len(contains) > 0

    def test_call_edges(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        assert len(calls) > 0

    def test_call_edge_targets(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        targets = {e.target_id for e in calls}
        has_distance = any("distance" in t for t in targets)
        has_process = any("process" in t for t in targets)
        assert has_distance
        assert has_process

    def test_edge_confidence(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        certain = [e for e in calls if e.confidence == EdgeConfidence.CERTAIN]
        assert len(certain) > 0


class TestCParserDependencies:
    def test_includes_tracked(self) -> None:
        _, graph = _parse_sample()
        assert "stdio.h" in graph.dependencies
        assert "myheader.h" in graph.dependencies


PREPROC_GUARDED_CODE = """\
void before_guard(void) {}

#ifdef USE_FEATURE
void feature_a(int x) {
    x = x + 1;
}
#else
void feature_b(int x) {
    x = x - 1;
}
#endif

void after_guard(void) {}
"""

PREPROC_SPLIT_SIGNATURE_CODE = """\
void before_split(void) {}

#if defined(_WIN32)
unsigned int __stdcall thread_worker(void * arg)
#else
void * thread_worker(void * arg)
#endif
{
    return 0;
}

void after_split(void) {
    thread_worker(0);
}
"""


class TestCParserPreprocessorRecovery:
    def test_finds_functions_inside_ifdef_branches(self) -> None:
        parser = CParser()
        with tempfile.NamedTemporaryFile(suffix=".c", mode="w", delete=False) as f:
            f.write(PREPROC_GUARDED_CODE)
            f.flush()
            graph = parser.parse_file(f.name)
        os.unlink(f.name)
        names = {n.name for n in graph.nodes.values() if n.kind == NodeKind.FUNCTION}
        assert "before_guard" in names
        assert "after_guard" in names
        assert "feature_a" in names or "feature_b" in names

    def test_finds_functions_after_split_signature(self) -> None:
        """Functions after a #if/#else split signature are still extracted."""
        parser = CParser()
        with tempfile.NamedTemporaryFile(suffix=".c", mode="w", delete=False) as f:
            f.write(PREPROC_SPLIT_SIGNATURE_CODE)
            f.flush()
            graph = parser.parse_file(f.name)
        os.unlink(f.name)
        names = {n.name for n in graph.nodes.values() if n.kind == NodeKind.FUNCTION}
        assert "before_split" in names
        assert "after_split" in names


class TestCParseDirectory:
    def test_parses_multiple_files(self) -> None:
        parser = CParser()
        code_a = "void fromA(void) {}\n"
        code_b = "void fromB(void) {}\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            for name, code in [("a.c", code_a), ("b.c", code_b)]:
                path = os.path.join(tmpdir, name)
                with open(path, "w") as f:
                    f.write(code)
            graph = parser.parse_directory(tmpdir)
        assert graph.language == "c"
        assert graph.root_path == tmpdir
        names = {n.name for n in graph.nodes.values()}
        assert "fromA" in names
        assert "fromB" in names

    def test_ignores_wrong_extensions(self) -> None:
        parser = CParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "skip.txt")
            with open(path, "w") as f:
                f.write("not source code")
            graph = parser.parse_directory(tmpdir)
        assert len(graph.nodes) == 0
