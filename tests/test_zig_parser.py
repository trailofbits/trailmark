"""Zig extraction, conservative binding, and public API regression tests."""

from pathlib import Path

import pytest
from tree_sitter import Parser
from tree_sitter_language_pack import get_language

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.nodes import NodeKind
from trailmark.parse import detect_languages, parse_directory, parse_file
from trailmark.query.api import QueryEngine


def _source(tmp_path: Path, text: str) -> Path:
    assert not Parser(get_language("zig")).parse(text.encode()).root_node.has_error
    path = tmp_path / "sample.zig"
    path.write_text(text)
    return path


def test_signatures_containers_calls_and_imports(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        """const dep = @import("dep.zig");
const S = struct {
    const E = enum { a, b };
    pub fn run(self: *S, comptime T: type, x: []const u8) !usize {
        if (x.len > 0) { return helper(); }
        return 0;
    }
};
fn helper() usize { return 1; }
fn caller() void { S.run(); dep.run(); }
""",
    )
    graph = parse_file(str(path))
    unit = graph.nodes["sample:S.run"]
    assert unit.kind == NodeKind.METHOD
    assert [(p.name, p.type_ref.name) for p in unit.parameters if p.type_ref] == [
        ("self", "*S"),
        ("T", "type"),
        ("x", "[]const u8"),
    ]
    assert unit.return_type and unit.return_type.name == "!usize"
    assert unit.cyclomatic_complexity == 2
    assert dict(unit.attributes)["zig_public"] is True
    assert graph.nodes["sample:S.E"].kind == NodeKind.ENUM
    assert graph.dependencies == ["dep.zig"]
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert {
        (e.source_id, e.target_id) for e in calls if e.confidence == EdgeConfidence.CERTAIN
    } == {("sample:S.run", "sample:helper"), ("sample:caller", "sample:S.run")}
    assert [
        dict(e.attributes)["raw_target"] for e in calls if e.confidence == EdgeConfidence.UNCERTAIN
    ] == ["dep.run"]


def test_dynamic_calls_do_not_bind_to_namesakes(tmp_path: Path) -> None:
    _source(
        tmp_path,
        """fn helper() void {}
const S = struct { fn run() void {} };
fn caller(helper: fn() void, receiver: S) void {
    helper(); receiver.run(); @panic("bad");
}
fn local() void { const helper = unknown; helper(); }
""",
    )
    graph = parse_directory(str(tmp_path), language="zig")
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 4
    assert all(e.confidence == EdgeConfidence.UNCERTAIN for e in calls)
    assert all(graph.nodes[e.target_id].kind == NodeKind.PROXY for e in calls)


def test_entrypoints_and_auto_directory(tmp_path: Path) -> None:
    _source(
        tmp_path,
        """// export fn decoy() void {}
pub fn main() void { helper(); }
pub fn helper() void {}
export fn api() void { helper(); }
extern "c" fn imported() void;
""",
    )
    assert detect_languages(str(tmp_path)) == ["zig"]
    assert parse_directory(str(tmp_path), language="auto").source_file_counts == {"zig": 1}
    engine = QueryEngine.from_directory(str(tmp_path), language="auto")
    entries = {e["node_id"]: e for e in engine.attack_surface()}
    assert set(entries) == {"sample:main", "sample:api"}
    assert entries["sample:api"]["trust_level"] == "untrusted_external"
    assert engine.entrypoint_paths_to("helper")


def test_nested_container_body_is_not_attributed_to_outer(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        """fn outer() void {
    const S = struct { fn nested() void { missing(); } };
}
""",
    )
    graph = parse_file(str(path))
    assert "sample:outer" in graph.nodes
    assert not any(e.kind == EdgeKind.CALLS for e in graph.edges)


def test_parse_errors_warn(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "broken.zig"
    path.write_text("fn broken(")
    parse_file(str(path))
    assert "Zig syntax errors" in caplog.text


def test_loop_capture_does_not_bind_to_free_function(tmp_path: Path) -> None:
    path = _source(
        tmp_path, "fn helper() void {} fn f(xs: []u8) void { for (xs) |helper| { helper(); } }"
    )
    graph = parse_file(str(path))
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 1
    assert calls[0].confidence == EdgeConfidence.UNCERTAIN
    assert graph.nodes[calls[0].target_id].kind == NodeKind.PROXY


def test_container_constant_alias_does_not_bind_across_files(tmp_path: Path) -> None:
    _source(tmp_path, "const alias = callback; fn caller() void { alias(); }")
    (tmp_path / "other.zig").write_text("fn alias() void {}")
    graph = parse_directory(str(tmp_path), language="zig")
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 1
    assert calls[0].confidence == EdgeConfidence.UNCERTAIN
    assert graph.nodes[calls[0].target_id].kind == NodeKind.PROXY
