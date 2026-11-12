"""PHP callable creation and execution have distinct graph semantics."""

from __future__ import annotations

from pathlib import Path

from trailmark.analysis._callable_references import discover_callable_references
from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.parsers.php.parser import PHPParser
from trailmark.query.api import QueryEngine


def _parse(tmp_path: Path, source: str) -> CodeGraph:
    path = tmp_path / "app.php"
    path.write_text(source)
    graph = PHPParser().parse_file(str(path))
    discover_callable_references(graph)
    return graph


def _calls(graph: CodeGraph, source: str) -> list[CodeEdge]:
    return [
        edge for edge in graph.edges if edge.source_id == source and edge.kind == EdgeKind.CALLS
    ]


def test_pipe_chain_invokes_each_static_stage_and_inline_closure(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        """<?php
function first($x) { return $x; }
function leaf($x) { return $x; }
class Stage { public static function second($x) { return $x; } }
function pipeline($x) {
    return $x
        |> first(...)
        |> Stage::second(...)
        |> (/* stage */ fn($v) => leaf($v))
        |> (function($v) { return leaf($v); });
}
""",
    )
    calls = _calls(graph, "app:pipeline")
    lambdas = {
        node.id for node in graph.nodes.values() if node.id.startswith("app:pipeline.$lambda@")
    }
    assert {edge.target_id for edge in calls} == {"app:first", "app:Stage.second", *lambdas}
    assert len(calls) == 4
    assert len(lambdas) == 2
    assert {edge.location.start_line for edge in calls if edge.location} == {7, 8, 9, 10}
    for identifier in lambdas:
        assert [edge.target_id for edge in _calls(graph, identifier)] == ["app:leaf"]
    engine = QueryEngine.from_graph(graph)
    assert "app:leaf" in {node["id"] for node in engine.reachable_from("app:pipeline")}


def test_partial_creation_preserves_eager_calls_without_executing_target(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        """<?php
function target($first, $second) { return $second; }
function prepare() { return 1; }
function create() {
    $partial = target(prepare(), ?);
    $named = target(second: ?, first: prepare());
    $rest = target(prepare(), ...);
    $firstClass = target(...);
}
""",
    )
    assert [edge.target_id for edge in _calls(graph, "app:create")] == ["app:prepare"] * 3
    facts = [fact for fact in graph.entrypoint_evidence.values() if fact.source_id == "app:create"]
    assert [dict(fact.details)["form"] for fact in facts].count("partial_application") == 3
    assert [dict(fact.details)["form"] for fact in facts].count("first_class_callable") == 1
    assert {fact.node_id for fact in facts} == {"app:target"}
    assert all(fact.binding == "resolved" for fact in facts)
    assert all("invocation_context" in fact.unresolved for fact in facts)
    engine = QueryEngine.from_graph(graph)
    assert "app:target" not in {node["id"] for node in engine.reachable_from("app:create")}


def test_explicit_inline_invocation_executes_partial_and_first_class_callable(
    tmp_path: Path,
) -> None:
    graph = _parse(
        tmp_path,
        """<?php
function target($first, $second) { return $second; }
function prepare() { return 1; }
function invoke($x) {
    (target(prepare(), ?))($x);
    (target(...))(1, $x);
    ((fn($value) => target(1, $value)))($x);
}
""",
    )
    calls = _calls(graph, "app:invoke")
    assert [edge.target_id for edge in calls].count("app:target") == 2
    assert [edge.target_id for edge in calls].count("app:prepare") == 1
    (closure,) = [edge.target_id for edge in calls if ".$lambda@" in edge.target_id]
    assert [edge.target_id for edge in _calls(graph, closure)] == ["app:target"]
    assert len(calls) == 4


def test_pipe_partial_invocation_keeps_fixed_argument_evaluation(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        """<?php
namespace App;
function prepare() { return 1; }
function target($first, $second) { return $second; }
class Stage { public static function run($first, $second) { return $second; } }
function pipeline($x) {
    $unused = target(prepare(), ?);
    return $x |> target(prepare(), ?) |> Stage::run(first: prepare(), second: ?);
}
""",
    )
    calls = _calls(graph, "app:App.pipeline")
    targets = [edge.target_id for edge in calls]
    assert targets.count("app:App.target") == 1
    assert targets.count("app:App.Stage.run") == 1
    assert targets.count("app:App.prepare") == 3
    assert len(calls) == 5
    assert all(edge.location is not None for edge in calls)


def test_dynamic_pipe_targets_are_uncertain_and_factory_evaluation_is_separate(
    tmp_path: Path,
) -> None:
    graph = _parse(
        tmp_path,
        """<?php
function target($value) { return $value; }
function factory() { return target(...); }
function pipeline($x, $dynamic) {
    $saved = target(?);
    $a = $x |> $dynamic;
    $b = $x |> factory();
    return $saved($x);
}
""",
    )
    calls = _calls(graph, "app:pipeline")
    unknown = [edge for edge in calls if "$pipe@" in edge.target_id]
    assert len(unknown) == 2
    assert all(edge.confidence == EdgeConfidence.UNCERTAIN for edge in unknown)
    assert [edge.target_id for edge in calls].count("app:factory") == 1
    assert "app:target" not in {edge.target_id for edge in calls}
    saved = next(edge for edge in calls if edge.target_id == "app:$saved")
    assert saved.confidence == EdgeConfidence.UNCERTAIN
    assert not any(edge.target_id in graph.nodes for edge in unknown)
    unresolved = QueryEngine.from_graph(graph).unresolved_calls("app:pipeline")
    assert {
        row["attributes"]["php_callable_expression"]
        for row in unresolved
        if row["attributes"].get("php_callable_invocation") == "pipe"
    } == {"$dynamic", "factory()"}
    assert all(row["location"]["file_path"] == str(tmp_path / "app.php") for row in unresolved)


def test_partial_reference_namespace_resolution_and_dynamic_receiver_uncertainty(
    tmp_path: Path,
) -> None:
    graph = _parse(
        tmp_path,
        """<?php
namespace App;
function target($value) { return $value; }
class Stage {
    public static function run($value) { return $value; }
    public function configure($receiver) {
        $a = target(?);
        $b = Stage::run(?);
        $c = self::run(?);
        $d = $receiver->run(?);
    }
}
""",
    )
    facts = [
        fact
        for fact in graph.entrypoint_evidence.values()
        if fact.source_id == "app:App.Stage.configure"
    ]
    assert {fact.node_id for fact in facts if fact.binding == "resolved"} == {
        "app:App.target",
        "app:App.Stage.run",
    }
    dynamic = next(fact for fact in facts if fact.symbol == "$receiver->run(?)")
    assert dynamic.binding == "unresolved"
    assert "receiver_dispatch" in dynamic.unresolved
    assert all(dict(fact.details)["form"] == "partial_application" for fact in facts)
    assert _calls(graph, "app:App.Stage.configure") == []


def test_nested_ternary_argument_is_not_a_partial_placeholder(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        """<?php
function target($value) { return $value; }
function call($flag) { return target($flag ? target(1) : null); }
""",
    )
    assert [edge.target_id for edge in _calls(graph, "app:call")] == ["app:target", "app:target"]
    assert not graph.entrypoint_evidence


def test_psalm_7_annotations_remain_source_evidence(tmp_path: Path) -> None:
    annotation = """@psalm-capabilities read-props|io
@psalm-purity-template P
@psalm-taint-source input return['original']
@psalm-taint-sink ($mode is 0 ? null : 'html') $content
@psalm-flow ($compute()) -> return
@psalm-taint-escape ~(html|has_quotes)"""
    comment = "/**\n" + "\n".join(" * " + line for line in annotation.splitlines()) + "\n */"
    graph = _parse(tmp_path, "<?php\n" + comment + "\nfunction model() {}\n")
    assert graph.nodes["app:model"].docstring == annotation
    assert comment in (tmp_path / "app.php").read_text()
