"""Lua lexical scope, mutation, dynamic dispatch, and public API behavior."""

from pathlib import Path

import pytest
from tree_sitter import Parser
from tree_sitter_language_pack import get_language

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind
from trailmark.parse import detect_languages, parse_directory, parse_file
from trailmark.query.api import QueryEngine


def _source(tmp_path: Path, text: str) -> Path:
    assert not Parser(get_language("lua")).parse(text.encode()).root_node.has_error
    path = tmp_path / "sample.lua"
    path.write_text(text)
    return path


def _calls(graph: CodeGraph, caller: str) -> list[tuple[str, EdgeConfidence]]:
    return [
        (e.target_id, e.confidence)
        for e in graph.edges
        if e.kind == EdgeKind.CALLS and e.source_id == caller
    ]


def test_functions_methods_signatures_and_chunk(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        """local function helper(x, ...)
  if x then return x end
end
local M = {}
function M:handle(x) return helper(x) end
local callback = function(x) return helper(x) end
M.other = function(x) return callback(x) end
helper(1)
""",
    )
    graph = parse_file(str(path))
    assert [p.name for p in graph.nodes["sample:helper"].parameters] == ["x", "..."]
    assert graph.nodes["sample:helper"].cyclomatic_complexity == 2
    method = graph.nodes["sample:M.handle"]
    assert method.kind == NodeKind.METHOD
    assert [p.name for p in method.parameters] == ["self", "x"]
    assert _calls(graph, method.id) == [("sample:helper", EdgeConfidence.CERTAIN)]
    assert _calls(graph, "sample:M.other") == [("sample:callback", EdgeConfidence.CERTAIN)]
    assert _calls(graph, "sample:<chunk>") == [("sample:helper", EdgeConfidence.CERTAIN)]
    assert graph.nodes["sample:<chunk>"].cyclomatic_complexity == 1


def test_local_recursion_and_closure_binding(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        """local function recurse(x) if x > 0 then return recurse(x-1) end end
local function outer()
  local function inner() return recurse(1) end
  return inner()
end
""",
    )
    graph = parse_file(str(path))
    assert _calls(graph, "sample:recurse") == [("sample:recurse", EdgeConfidence.CERTAIN)]
    assert _calls(graph, "sample:outer") == [("sample:outer.inner", EdgeConfidence.CERTAIN)]
    assert _calls(graph, "sample:outer.inner") == [("sample:recurse", EdgeConfidence.CERTAIN)]


def test_parameters_receivers_and_aliases_do_not_bind_to_namesakes(tmp_path: Path) -> None:
    _source(
        tmp_path,
        """local function helper() end
local M = {}
function M.run() end
local function caller(helper, receiver)
  helper(); receiver.run(); M.run()
end
local alias = helper
alias()
""",
    )
    (tmp_path / "other.lua").write_text("function alias() end")
    graph = parse_directory(str(tmp_path), language="lua")
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 4
    assert all(e.confidence == EdgeConfidence.UNCERTAIN for e in calls)
    assert all(graph.nodes[e.target_id].kind == NodeKind.PROXY for e in calls)


def test_reassignments_including_closures_invalidate_binding(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        """local function helper() end
local function caller() helper() end
local function mutate() helper = replacement end
helper()
""",
    )
    graph = parse_file(str(path))
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 2
    assert all(e.confidence == EdgeConfidence.UNCERTAIN for e in calls)


def test_block_scope_and_repeated_names(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        """local function helper() end
do
  local function helper() end
  helper()
end
helper()
""",
    )
    graph = parse_file(str(path))
    local_ids = [u.id for u in graph.nodes.values() if u.name == "helper"]
    assert len(local_ids) == 2 and len(set(local_ids)) == 2
    assert _calls(graph, "sample:<chunk>") == [
        (local_ids[1], EdgeConfidence.CERTAIN),
        (local_ids[0], EdgeConfidence.CERTAIN),
    ]


def test_declaration_order_and_initializer_scope(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        """local function helper() end
do
  local helper = helper()
  helper()
end
before()
local function before() end
local selfref = function() return selfref() end
""",
    )
    graph = parse_file(str(path))
    chunk_calls = _calls(graph, "sample:<chunk>")
    assert chunk_calls[0] == ("sample:helper", EdgeConfidence.CERTAIN)
    assert all(confidence == EdgeConfidence.UNCERTAIN for _, confidence in chunk_calls[1:])
    assert _calls(graph, "sample:selfref")[0][1] == EdgeConfidence.UNCERTAIN


def test_loop_and_repeat_scopes(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        """local function helper() end
for helper in iterator() do helper() end
for helper = 1, 3 do helper() end
repeat local helper = unknown helper() until helper()
helper()
""",
    )
    graph = parse_file(str(path))
    calls = _calls(graph, "sample:<chunk>")
    assert len(calls) == 6
    assert all(c == EdgeConfidence.UNCERTAIN for _, c in calls[:-1])
    assert calls[-1] == ("sample:helper", EdgeConfidence.CERTAIN)


def test_require_literals_and_shadowing(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        """local dep = require "one"
require [[two]]
require(dynamic_name)
local function shadow(require) require("fake") end
do local require = custom require("also_fake") end
""",
    )
    graph = parse_file(str(path))
    assert graph.dependencies == ["one", "two"]
    imports = [e.target_id for e in graph.edges if e.kind == EdgeKind.IMPORTS]
    assert imports == ["one", "two"]


def test_anonymous_bodies_are_owned_and_table_fields_are_named(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        """local function helper() end
local M = { run = function() helper() end }
register(function() if flag then helper() end end)
""",
    )
    graph = parse_file(str(path))
    assert _calls(graph, "sample:M.run") == [("sample:helper", EdgeConfidence.CERTAIN)]
    anonymous = [u for u in graph.nodes.values() if u.name.startswith("<lambda>")]
    assert len(anonymous) == 1 and anonymous[0].cyclomatic_complexity == 2
    assert _calls(graph, anonymous[0].id) == [("sample:helper", EdgeConfidence.CERTAIN)]
    assert len(_calls(graph, "sample:<chunk>")) == 1


def test_global_inference_and_mutation(tmp_path: Path) -> None:
    path = _source(tmp_path, "function global() end function main() global() end")
    graph = parse_file(str(path))
    assert _calls(graph, "sample:main") == [("sample:global", EdgeConfidence.INFERRED)]
    path.write_text("function global() end function main() global() end global = unknown")
    graph = parse_file(str(path))
    assert _calls(graph, "sample:main")[0][1] == EdgeConfidence.UNCERTAIN


def test_auto_directory_entrypoints_and_overrides(tmp_path: Path) -> None:
    _source(
        tmp_path,
        "local function helper() end function main() helper() end "
        "function host_callback() helper() end",
    )
    assert detect_languages(str(tmp_path)) == ["lua"]
    assert parse_directory(str(tmp_path), language="auto").source_file_counts == {"lua": 1}
    engine = QueryEngine.from_directory(str(tmp_path), language="auto")
    assert [e["node_id"] for e in engine.attack_surface()] == ["sample:main"]
    assert engine.entrypoint_paths_to("helper")
    config = tmp_path / ".trailmark"
    config.mkdir()
    (config / "entrypoints.toml").write_text(
        '[[entrypoint]]\nnode = "sample:host_callback"\nkind = "api"\n'
        'trust = "untrusted_external"\n'
    )
    engine = QueryEngine.from_directory(str(tmp_path), language="lua")
    assert {e["node_id"] for e in engine.attack_surface()} == {
        "sample:main",
        "sample:host_callback",
    }


def test_parse_errors_warn(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "broken.lua"
    path.write_text("function broken(")
    parse_file(str(path))
    assert "Lua syntax errors" in caplog.text
