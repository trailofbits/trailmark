"""Tests for the Haskell language parser."""

from __future__ import annotations

import os
import tempfile

import pytest

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind
from trailmark.parsers.haskell.parser import HaskellParser

SAMPLE_CODE = """\
module Example where

import Data.List (sort)
import qualified Data.Map as Map

-- | A 2D point
data Point = Point { x :: Double, y :: Double }

-- | Available colors
data Color = Red | Green | Blue

-- | Things that can describe themselves
class Describable a where
    describe :: a -> String

instance Describable Point where
    describe p = showPoint p

showPoint :: Point -> String
showPoint p = "point"

abs' :: Double -> Double
abs' x
    | x < 0    = negate x
    | otherwise = x

process :: [Int] -> Int
process items = case total of
    0 -> 0
    n -> n * 2
  where
    total = sum (filter greaterThanZero items)

greaterThanZero :: Int -> Bool
greaterThanZero n = n > 0

greet :: String -> String
greet name = "Hello, " ++ name

factorial :: Integer -> Integer
factorial 0 = 1
factorial n = n * factorial (n - 1)
"""


def _parse_sample() -> tuple[HaskellParser, CodeGraph]:
    parser = HaskellParser()
    with tempfile.NamedTemporaryFile(
        suffix=".hs",
        mode="w",
        delete=False,
    ) as f:
        f.write(SAMPLE_CODE)
        f.flush()
        graph = parser.parse_file(f.name)
    os.unlink(f.name)
    return parser, graph


class TestHaskellParserNodes:
    @pytest.mark.mutation_fast
    def test_finds_module(self) -> None:
        _, graph = _parse_sample()
        modules = [n for n in graph.nodes.values() if n.kind == NodeKind.MODULE]
        assert len(modules) == 1

    @pytest.mark.mutation_fast
    def test_finds_data_types(self) -> None:
        _, graph = _parse_sample()
        structs = [n for n in graph.nodes.values() if n.kind == NodeKind.STRUCT]
        names = {s.name for s in structs}
        assert "Point" in names
        assert "Color" in names

    def test_finds_type_class(self) -> None:
        _, graph = _parse_sample()
        traits = [n for n in graph.nodes.values() if n.kind == NodeKind.TRAIT]
        names = {t.name for t in traits}
        assert "Describable" in names

    @pytest.mark.mutation_fast
    def test_finds_functions(self) -> None:
        _, graph = _parse_sample()
        funcs = [n for n in graph.nodes.values() if n.kind == NodeKind.FUNCTION]
        names = {f.name for f in funcs}
        assert "abs'" in names
        assert "process" in names
        assert "greet" in names
        assert "factorial" in names
        assert "showPoint" in names
        assert "greaterThanZero" in names

    @pytest.mark.mutation_fast
    def test_finds_instance_methods(self) -> None:
        _, graph = _parse_sample()
        methods = [n for n in graph.nodes.values() if n.kind == NodeKind.METHOD]
        names = {m.name for m in methods}
        assert "describe" in names

    @pytest.mark.mutation_fast
    def test_method_id_includes_type(self) -> None:
        _, graph = _parse_sample()
        method_ids = [n.id for n in graph.nodes.values() if n.kind == NodeKind.METHOD]
        has_point = any("Point" in mid for mid in method_ids)
        assert has_point

    def test_multi_equation_function_merged(self) -> None:
        """factorial has two equations that should be merged."""
        _, graph = _parse_sample()
        factorials = [n for n in graph.nodes.values() if n.name == "factorial"]
        assert len(factorials) == 1

    @pytest.mark.mutation_fast
    def test_data_type_docstring(self) -> None:
        _, graph = _parse_sample()
        point = next(n for n in graph.nodes.values() if n.name == "Point")
        assert point.docstring is not None
        assert "2D" in point.docstring

    @pytest.mark.mutation_fast
    def test_class_docstring(self) -> None:
        _, graph = _parse_sample()
        desc = next(n for n in graph.nodes.values() if n.name == "Describable")
        assert desc.docstring is not None
        assert "describe" in desc.docstring


class TestHaskellParserParameters:
    @pytest.mark.mutation_fast
    def test_function_params_from_pattern(self) -> None:
        _, graph = _parse_sample()
        greet = next(n for n in graph.nodes.values() if n.name == "greet")
        assert len(greet.parameters) == 1
        assert greet.parameters[0].name == "name"

    @pytest.mark.mutation_fast
    def test_param_type_from_signature(self) -> None:
        _, graph = _parse_sample()
        greet = next(n for n in graph.nodes.values() if n.name == "greet")
        assert greet.parameters[0].type_ref is not None
        assert greet.parameters[0].type_ref.name == "String"

    def test_return_type(self) -> None:
        _, graph = _parse_sample()
        greet = next(n for n in graph.nodes.values() if n.name == "greet")
        assert greet.return_type is not None
        assert greet.return_type.name == "String"

    def test_multi_param_function(self) -> None:
        """abs' has one parameter from the pattern."""
        _, graph = _parse_sample()
        abs_fn = next(n for n in graph.nodes.values() if n.name == "abs'")
        assert len(abs_fn.parameters) == 1
        assert abs_fn.parameters[0].name == "x"


class TestHaskellParserComplexity:
    def test_simple_function_complexity(self) -> None:
        _, graph = _parse_sample()
        greet = next(n for n in graph.nodes.values() if n.name == "greet")
        assert greet.cyclomatic_complexity == 1

    @pytest.mark.mutation_fast
    def test_guarded_function_complexity(self) -> None:
        _, graph = _parse_sample()
        abs_fn = next(n for n in graph.nodes.values() if n.name == "abs'")
        assert abs_fn.cyclomatic_complexity is not None
        assert abs_fn.cyclomatic_complexity >= 2

    def test_case_expression_complexity(self) -> None:
        _, graph = _parse_sample()
        process = next(n for n in graph.nodes.values() if n.name == "process")
        assert process.cyclomatic_complexity is not None
        assert process.cyclomatic_complexity >= 2

    def test_branches_tracked(self) -> None:
        _, graph = _parse_sample()
        process = next(n for n in graph.nodes.values() if n.name == "process")
        assert len(process.branches) > 0


class TestHaskellParserEdges:
    def test_contains_edges(self) -> None:
        _, graph = _parse_sample()
        contains = [e for e in graph.edges if e.kind == EdgeKind.CONTAINS]
        assert len(contains) > 0

    @pytest.mark.mutation_fast
    def test_implements_edge(self) -> None:
        _, graph = _parse_sample()
        implements = [e for e in graph.edges if e.kind == EdgeKind.IMPLEMENTS]
        assert len(implements) == 1
        assert "Point" in implements[0].source_id
        assert "Describable" in implements[0].target_id

    @pytest.mark.mutation_fast
    def test_call_edges(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        assert len(calls) > 0

    @pytest.mark.mutation_fast
    def test_call_confidence(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        certain = [e for e in calls if e.confidence == EdgeConfidence.CERTAIN]
        assert len(certain) > 0

    def test_module_contains_functions(self) -> None:
        _, graph = _parse_sample()
        contains = [e for e in graph.edges if e.kind == EdgeKind.CONTAINS]
        module_contains = [
            e for e in contains if e.source_id not in ("", None) and e.target_id not in ("", None)
        ]
        assert len(module_contains) > 0


class TestHaskellParserDependencies:
    @pytest.mark.mutation_fast
    def test_imports_tracked(self) -> None:
        _, graph = _parse_sample()
        assert "Data.List" in graph.dependencies
        assert "Data.Map" in graph.dependencies


class TestHaskellParseDirectory:
    @pytest.mark.mutation_fast
    def test_parses_multiple_files(self) -> None:
        parser = HaskellParser()
        code_a = "module A where\n\nfromA :: Int\nfromA = 1\n"
        code_b = "module B where\n\nfromB :: Int\nfromB = 2\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            for name, code in [("A.hs", code_a), ("B.hs", code_b)]:
                path = os.path.join(tmpdir, name)
                with open(path, "w") as f:
                    f.write(code)
            graph = parser.parse_directory(tmpdir)
        assert graph.language == "haskell"
        assert graph.root_path == tmpdir
        names = {n.name for n in graph.nodes.values()}
        assert "fromA" in names
        assert "fromB" in names

    def test_ignores_wrong_extensions(self) -> None:
        parser = HaskellParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "skip.txt")
            with open(path, "w") as f:
                f.write("not source code")
            graph = parser.parse_directory(tmpdir)
        assert len(graph.nodes) == 0
