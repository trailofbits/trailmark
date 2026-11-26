"""Local Lua tables can supply explicit method bodies without global-name guesses."""

from pathlib import Path

import pytest

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.nodes import NodeOrigin
from trailmark.parse import parse_file


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    "definition,invocation",
    [
        ("function M.leaf(value) return value end", "M.leaf(value)"),
        ("function M:leaf(value) return value end", "M:leaf(value)"),
        ("M.leaf = function(value) return value end", "M.leaf(value)"),
    ],
)
def test_explicit_local_table_methods_bind_to_their_bodies(
    tmp_path: Path, definition: str, invocation: str
) -> None:
    source = tmp_path / "api.lua"
    source.write_text(
        f"local M = {{}}\n{definition}\nfunction M.entry(value) return {invocation} end\n"
    )
    graph = parse_file(str(source), "lua")
    call = next(edge for edge in graph.edges if edge.kind == EdgeKind.CALLS)
    assert call.source_id == "api:M.entry"
    assert call.target_id == "api:M.leaf"
    assert call.confidence == EdgeConfidence.INFERRED
    assert call.location is not None and call.location.start_line == 3


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    "declaration,body",
    [
        ("function M.entry(M)", "return M.leaf()"),
        ("function M.entry()", "local M = {}; return M.leaf()"),
    ],
)
def test_local_table_receivers_respect_parameter_and_local_shadowing(
    tmp_path: Path, declaration: str, body: str
) -> None:
    source = tmp_path / "shadow.lua"
    source.write_text(f"local M = {{}}\nfunction M.leaf() end\n{declaration} {body} end\n")
    graph = parse_file(str(source), "lua")
    call = next(edge for edge in graph.edges if edge.kind == EdgeKind.CALLS)
    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    "mutation",
    [
        "M = replacement",
        "M.leaf = replacement",
        "M[key] = replacement",
        "M[key] = function() end",
        "(M).leaf = replacement",
        "local alias = M; alias.leaf = replacement",
        "local alias = (M); alias.leaf = replacement",
        "rawset(M, 'leaf', replacement)",
        "replace(M)",
        "replace((M))",
        "replace({M})",
        "local holder = {M}; holder[1].leaf = replacement",
        "setmetatable(M, {__newindex = replacement})",
        'setmetatable(M, {["__newindex"] = replacement})',
        "setmetatable(M, {[key] = replacement})",
        "setmetatable(M, {__call = function(self) self.leaf = replacement end}); M()",
        "setmetatable(M, {__call = function(self) self.leaf = replacement end}); (M)()",
        "pairs = replacement; pairs(M)",
        "function M.mutate(t) t.leaf = replacement end; M.mutate(M)",
        "function M.mutate(t) local alias = t; alias.leaf = replacement end; M.mutate(M)",
        "function M.mutate(t) replace(t) end; M.mutate(M)",
        "function M:mutate() self.leaf = replacement end; M:mutate()",
        "function M:mutate() replace(self) end; M:mutate()",
        "function M.mutate(t) replace({t}) end; M.mutate(M)",
        "function M.mutate(t) return t end; local alias = M.mutate(M); alias.leaf = replacement",
        "function M.mutate(t) local change = function() replace(t) end; change() end; M.mutate(M)",
        "local function forward(t) replace(t) end; "
        "function M.mutate(t) forward(t) end; M.mutate(M)",
        "function M.mutate(t) M.mutate(t) end; M.mutate(M)",
        "function M.mutate(...) local t = ...; t.leaf = replacement end; M.mutate(M)",
        "function M.mutate(...) replace(...) end; M.mutate(M)",
        "setmetatable(M, {__call = function(self) self.leaf = replacement end}); "
        "function M.mutate(t) for item in t do end end; M.mutate(M)",
        "function M.get() return M end; local alias = M.get(); alias.leaf = replacement",
        "local function read(t) return type(t) end; "
        "function M.mutate(t) do local read = replace; read(t) end end; M.mutate(M)",
        "local function selectiter(t) if flag then local pairs = replace; return pairs end; "
        "return pairs end; function M.mutate(t) local iter = selectiter(t); iter(t) end; "
        "M.mutate(M)",
        "function M.leaf() end",
    ],
)
def test_modified_aliased_or_escaped_tables_do_not_keep_stale_targets(
    tmp_path: Path, mutation: str
) -> None:
    source = tmp_path / "mutated.lua"
    source.write_text(
        "local M = {}\nfunction M.leaf() end\n"
        + mutation
        + "\nfunction M.entry() return M.leaf() end\n"
    )
    graph = parse_file(str(source), "lua")
    call = next(
        edge
        for edge in graph.edges
        if edge.kind == EdgeKind.CALLS and edge.source_id == "mutated:M.entry"
    )
    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY


@pytest.mark.mutation_fast
def test_conditional_table_method_definition_remains_uncertain(tmp_path: Path) -> None:
    source = tmp_path / "conditional.lua"
    source.write_text(
        "local M = {}\nif flag then function M.leaf() end end\n"
        "function M.entry() return M.leaf() end\n"
    )
    graph = parse_file(str(source), "lua")
    call = next(edge for edge in graph.edges if edge.kind == EdgeKind.CALLS)
    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY


@pytest.mark.mutation_fast
@pytest.mark.parametrize("iterator", ["pairs", "iter"])
def test_source_backed_readonly_helper_preserves_table_methods(
    tmp_path: Path, iterator: str
) -> None:
    source = tmp_path / "readonly.lua"
    source.write_text(
        "local M = {}\nlocal pairs = pairs\n"
        "local function selectiter(t) if type(t) == 'table' then return pairs end end\n"
        "function M.leaf() end\n"
        "function M.read(t) local iter = selectiter(t); "
        f"for k, v in {iterator}(t) do print(k, v) end end\n"
        "M.read(M)\nfunction M.entry() return M.leaf() end\n"
    )
    graph = parse_file(str(source), "lua")
    call = next(
        edge
        for edge in graph.edges
        if edge.kind == EdgeKind.CALLS and edge.source_id == "readonly:M.entry"
    )
    assert call.target_id == "readonly:M.leaf"
    assert call.confidence == EdgeConfidence.INFERRED
