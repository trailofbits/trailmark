"""Tests for the Erlang language parser."""

from __future__ import annotations

import os
import tempfile

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind
from trailmark.parsers.erlang.parser import ErlangParser

SAMPLE_CODE = """\
-module(example).
-export([greet/1, factorial/1]).
-behaviour(gen_server).
-import(lists, [sort/1]).

-record(point, {x = 0 :: integer(), y = 0 :: integer()}).

-type color() :: red | green | blue.

-spec greet(Name :: string()) -> string().
%% @doc Greets the given name.
greet(Name) ->
    io:format("Hello ~s~n", [Name]),
    "Hello, " ++ Name.

-spec factorial(non_neg_integer()) -> pos_integer().
factorial(0) -> 1;
factorial(N) when N > 0 -> N * factorial(N - 1).

process(Items) ->
    case lists:sum(Items) of
        0 -> zero;
        N when N > 0 -> positive;
        _ -> negative
    end.

handle_call(Request, _From, State) ->
    try
        Result = do_something(Request),
        {reply, Result, State}
    catch
        error:Reason -> {reply, {error, Reason}, State};
        throw:Term -> {reply, {error, Term}, State}
    end.

simple() -> ok.
"""


def _parse_sample() -> tuple[ErlangParser, CodeGraph]:
    parser = ErlangParser()
    with tempfile.NamedTemporaryFile(
        suffix=".erl",
        mode="w",
        delete=False,
    ) as f:
        f.write(SAMPLE_CODE)
        f.flush()
        graph = parser.parse_file(f.name)
    os.unlink(f.name)
    return parser, graph


class TestErlangParserNodes:
    def test_finds_module(self) -> None:
        _, graph = _parse_sample()
        modules = [n for n in graph.nodes.values() if n.kind == NodeKind.MODULE]
        assert len(modules) == 1
        assert modules[0].name == "example"

    def test_finds_functions(self) -> None:
        _, graph = _parse_sample()
        funcs = [n for n in graph.nodes.values() if n.kind == NodeKind.FUNCTION]
        names = {f.name for f in funcs}
        assert "greet" in names
        assert "factorial" in names
        assert "process" in names
        assert "handle_call" in names
        assert "simple" in names

    def test_finds_record(self) -> None:
        _, graph = _parse_sample()
        structs = [n for n in graph.nodes.values() if n.kind == NodeKind.STRUCT]
        names = {s.name for s in structs}
        assert "point" in names

    def test_finds_type_alias(self) -> None:
        _, graph = _parse_sample()
        structs = [n for n in graph.nodes.values() if n.kind == NodeKind.STRUCT]
        names = {s.name for s in structs}
        assert "color" in names

    def test_multi_clause_function_merged(self) -> None:
        """factorial has two clauses that should be merged."""
        _, graph = _parse_sample()
        factorials = [n for n in graph.nodes.values() if n.name == "factorial"]
        assert len(factorials) == 1

    def test_module_id_from_attribute(self) -> None:
        """Module ID should come from -module(example)."""
        _, graph = _parse_sample()
        module = next(n for n in graph.nodes.values() if n.kind == NodeKind.MODULE)
        assert module.id == "example"

    def test_function_ids_use_module_name(self) -> None:
        _, graph = _parse_sample()
        greet = next(n for n in graph.nodes.values() if n.name == "greet")
        assert greet.id == "example:greet/1"


class TestErlangParserParameters:
    def test_function_params_from_pattern(self) -> None:
        _, graph = _parse_sample()
        greet = next(n for n in graph.nodes.values() if n.name == "greet")
        assert len(greet.parameters) >= 1
        assert greet.parameters[0].name == "Name"

    def test_param_type_from_spec(self) -> None:
        _, graph = _parse_sample()
        greet = next(n for n in graph.nodes.values() if n.name == "greet")
        assert greet.parameters[0].type_ref is not None
        assert "string" in greet.parameters[0].type_ref.name

    def test_return_type_from_spec(self) -> None:
        _, graph = _parse_sample()
        greet = next(n for n in graph.nodes.values() if n.name == "greet")
        assert greet.return_type is not None
        assert "string" in greet.return_type.name

    def test_multi_param_function(self) -> None:
        _, graph = _parse_sample()
        handle = next(n for n in graph.nodes.values() if n.name == "handle_call")
        assert len(handle.parameters) == 3


class TestErlangParserComplexity:
    def test_simple_function_complexity(self) -> None:
        _, graph = _parse_sample()
        simple = next(n for n in graph.nodes.values() if n.name == "simple")
        assert simple.cyclomatic_complexity == 1

    def test_guarded_clause_complexity(self) -> None:
        """factorial's guarded clause should add complexity."""
        _, graph = _parse_sample()
        factorial = next(n for n in graph.nodes.values() if n.name == "factorial")
        assert factorial.cyclomatic_complexity is not None
        assert factorial.cyclomatic_complexity >= 2

    def test_case_expression_complexity(self) -> None:
        _, graph = _parse_sample()
        process = next(n for n in graph.nodes.values() if n.name == "process")
        assert process.cyclomatic_complexity is not None
        assert process.cyclomatic_complexity >= 2

    def test_try_catch_complexity(self) -> None:
        _, graph = _parse_sample()
        handle = next(n for n in graph.nodes.values() if n.name == "handle_call")
        assert handle.cyclomatic_complexity is not None
        assert handle.cyclomatic_complexity >= 2

    def test_branches_tracked(self) -> None:
        _, graph = _parse_sample()
        process = next(n for n in graph.nodes.values() if n.name == "process")
        assert len(process.branches) > 0


class TestErlangParserEdges:
    def test_contains_edges(self) -> None:
        _, graph = _parse_sample()
        contains = [e for e in graph.edges if e.kind == EdgeKind.CONTAINS]
        assert len(contains) > 0

    def test_call_edges(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        assert len(calls) > 0

    def test_remote_call_edge(self) -> None:
        """io:format should create a call edge."""
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        targets = {e.target_id for e in calls}
        assert "io:format/2" in targets

    def test_local_call_edge(self) -> None:
        """factorial calling itself should create a call edge."""
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        has_factorial = any("factorial" in e.target_id for e in calls)
        assert has_factorial

    def test_call_confidence(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        certain = [e for e in calls if e.confidence == EdgeConfidence.CERTAIN]
        assert len(certain) > 0

    def test_module_contains_functions(self) -> None:
        _, graph = _parse_sample()
        contains = [e for e in graph.edges if e.kind == EdgeKind.CONTAINS]
        module_contains = [e for e in contains if e.source_id == "example"]
        assert len(module_contains) > 0


class TestErlangParserDependencies:
    def test_behaviour_tracked(self) -> None:
        _, graph = _parse_sample()
        assert "gen_server" in graph.dependencies

    def test_import_tracked(self) -> None:
        _, graph = _parse_sample()
        assert "lists" in graph.dependencies


class TestErlangParseDirectory:
    def test_parses_multiple_files(self) -> None:
        parser = ErlangParser()
        code_a = "-module(mod_a).\n-export([from_a/0]).\nfrom_a() -> ok.\n"
        code_b = "-module(mod_b).\n-export([from_b/0]).\nfrom_b() -> ok.\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            for name, code in [("mod_a.erl", code_a), ("mod_b.erl", code_b)]:
                path = os.path.join(tmpdir, name)
                with open(path, "w") as f:
                    f.write(code)
            graph = parser.parse_directory(tmpdir)
        assert graph.language == "erlang"
        assert graph.root_path == tmpdir
        names = {n.name for n in graph.nodes.values()}
        assert "from_a" in names
        assert "from_b" in names

    def test_ignores_wrong_extensions(self) -> None:
        parser = ErlangParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "skip.txt")
            with open(path, "w") as f:
                f.write("not source code")
            graph = parser.parse_directory(tmpdir)
        assert len(graph.nodes) == 0
