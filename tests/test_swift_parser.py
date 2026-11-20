"""Tests for the Swift parser."""

from __future__ import annotations

from pathlib import Path

from trailmark.models.graph import CodeGraph
from trailmark.parsers.swift import SwiftParser


def _parse(tmp_path: Path, body: str, name: str = "app.swift") -> CodeGraph:
    (tmp_path / name).write_text(body)
    return SwiftParser().parse_directory(str(tmp_path))


class TestBasicExtraction:
    def test_module_node_created(self, tmp_path: Path) -> None:
        graph = _parse(tmp_path, "func greet() {}\n")
        assert "app" in graph.nodes
        assert graph.nodes["app"].kind.value == "module"

    def test_top_level_function(self, tmp_path: Path) -> None:
        graph = _parse(tmp_path, "func greet() {}\n")
        assert "app:greet" in graph.nodes
        func = graph.nodes["app:greet"]
        assert func.kind.value == "function"

    def test_parameters_and_types(self, tmp_path: Path) -> None:
        graph = _parse(tmp_path, "func add(a: Int, b: Int) -> Int { return a + b }\n")
        func = graph.nodes["app:add"]
        assert [p.name for p in func.parameters] == ["a", "b"]
        assert func.return_type is not None
        assert func.return_type.name == "Int"

    def test_class_with_method(self, tmp_path: Path) -> None:
        graph = _parse(
            tmp_path,
            "class Foo {\n    func bar(x: Int) -> Int { return x }\n}\n",
        )
        assert "app:Foo" in graph.nodes
        assert graph.nodes["app:Foo"].kind.value == "class"
        assert "app:Foo.bar" in graph.nodes
        assert graph.nodes["app:Foo.bar"].kind.value == "method"

    def test_struct_and_enum_kinds(self, tmp_path: Path) -> None:
        graph = _parse(
            tmp_path,
            "struct Point {}\nenum Direction { case north }\nprotocol Drawable {}\n",
        )
        assert graph.nodes["app:Point"].kind.value == "struct"
        assert graph.nodes["app:Direction"].kind.value == "enum"
        assert graph.nodes["app:Drawable"].kind.value == "interface"


class TestControlFlow:
    def test_complexity_counts_branches(self, tmp_path: Path) -> None:
        code = (
            "func check(x: Int) -> Bool {\n"
            "    if x > 0 {\n"
            "        if x > 10 { return true }\n"
            "    }\n"
            "    return false\n"
            "}\n"
        )
        graph = _parse(tmp_path, code)
        assert graph.nodes["app:check"].cyclomatic_complexity is not None
        assert graph.nodes["app:check"].cyclomatic_complexity >= 3

    def test_throws_clause_and_throw_are_captured(self, tmp_path: Path) -> None:
        """Throwing signatures and operands remain distinct evidence."""
        code = "func auth() throws {\n    throw AuthError.invalid\n}\n"
        graph = _parse(tmp_path, code)
        assert "app:auth" in graph.nodes
        assert [ref.name for ref in graph.nodes["app:auth"].exception_types] == ["AuthError"]
        assert [event.kind.value for event in graph.nodes["app:auth"].exception_events] == [
            "declares",
            "raise",
        ]


class TestImports:
    def test_import_captured(self, tmp_path: Path) -> None:
        graph = _parse(tmp_path, "import Foundation\nfunc f() {}\n")
        assert "Foundation" in graph.dependencies


class TestCallEdges:
    def test_call_edge_recorded(self, tmp_path: Path) -> None:
        graph = _parse(
            tmp_path,
            "func a() { b() }\nfunc b() {}\n",
        )
        sources = {e.source_id for e in graph.edges if e.kind.value == "calls"}
        assert "app:a" in sources


def test_defer_keeps_calls_and_branches_in_enclosing_method(tmp_path: Path) -> None:
    from trailmark.models.edges import EdgeKind

    graph = _parse(
        tmp_path,
        "struct Lock {\n"
        " func lock() {}\n"
        " func unlock() {}\n"
        " func run() {\n"
        "  self.lock()\n"
        "  defer { if true { self.unlock() } }\n"
        " }\n"
        "}\n",
    )
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert {(e.source_id, e.target_id) for e in calls} == {
        ("app:Lock.run", "app:Lock.lock"),
        ("app:Lock.run", "app:Lock.unlock"),
    }
    assert {e.location.start_line for e in calls if e.location} == {5, 6}
    assert graph.nodes["app:Lock.run"].cyclomatic_complexity == 2
    assert not any("$lambda" in key or key.endswith(":defer") for key in graph.nodes)


def test_nested_defer_preserves_real_closure_ownership(tmp_path: Path) -> None:
    from trailmark.models.edges import EdgeKind

    graph = _parse(
        tmp_path,
        "func cleanup() {}\n"
        "func run() {\n"
        " defer {\n"
        "  defer { cleanup() }\n"
        "  let callback = { cleanup() }\n"
        " }\n"
        "}\n",
    )
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert any(e.source_id == "app:run" and e.target_id == "app:cleanup" for e in calls)
    closure_calls = [e for e in calls if "$lambda" in e.source_id]
    assert len(closure_calls) == 1
    assert closure_calls[0].target_id == "app:cleanup"
    assert not any(e.target_id.endswith(":defer") for e in calls)


def test_self_overloads_and_other_receivers_remain_unresolved(tmp_path: Path) -> None:
    from trailmark.models.edges import EdgeKind

    graph = _parse(
        tmp_path,
        "struct Worker {\n"
        " func work() {}\n"
        " func work(_ value: Int) {}\n"
        " func run(_ other: Worker) { self.work(); other.work() }\n"
        "}\n",
    )
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 2
    assert all(e.target_id.startswith("proxy.") for e in calls)


def test_backticked_defer_function_remains_a_call(tmp_path: Path) -> None:
    from trailmark.models.edges import EdgeKind

    graph = _parse(tmp_path, "func `defer`() {}\nfunc run() { `defer`() }\n")
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 1
    assert calls[0].source_id == "app:run"


def test_defer_method_navigation_is_visible_to_queries(tmp_path: Path) -> None:
    from trailmark.query.api import QueryEngine

    graph = _parse(
        tmp_path, "struct Lock { func unlock() {}\n func run() { defer { self.unlock() } } }\n"
    )
    engine = QueryEngine.from_graph(graph)
    assert {n["id"] for n in engine.callers_of("app:Lock.unlock")} == {"app:Lock.run"}


def test_capture_rebound_self_does_not_bind_lexical_type(tmp_path: Path) -> None:
    from trailmark.models.edges import EdgeConfidence, EdgeKind

    graph = _parse(
        tmp_path,
        "struct Other { func work() {} }\n"
        "struct Box { func work() {}\n"
        "func run() { let action = { [self = Other()] in self.work() }; action() } }\n",
    )
    calls = [e for e in graph.edges if dict(e.attributes).get("swift_self_method") == "work"]
    assert len(calls) == 1
    assert calls[0].kind == EdgeKind.CALLS
    assert calls[0].target_id.startswith("proxy.")
    assert calls[0].confidence == EdgeConfidence.UNCERTAIN
    assert dict(calls[0].attributes)["binding_blocked"] is True
