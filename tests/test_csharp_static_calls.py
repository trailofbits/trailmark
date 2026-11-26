"""Qualified static calls respect namespace, partial type, and shadow scope."""

from pathlib import Path

import pytest

from trailmark.models.edges import EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeOrigin
from trailmark.parse import parse_directory
from trailmark.query.api import QueryEngine


def _parse(tmp_path: Path, caller: str, target: str = "") -> CodeGraph:
    (tmp_path / "Caller.cs").write_text(caller)
    if target:
        (tmp_path / "Target.cs").write_text(target)
    return parse_directory(str(tmp_path), "c_sharp", strict=True)


@pytest.mark.mutation_fast
def test_cross_file_namespace_static_call_navigates_to_partial_class(tmp_path: Path) -> None:
    (tmp_path / "OtherPart.cs").write_text("namespace N { static partial class Target {} }")
    graph = _parse(
        tmp_path,
        "using System; namespace N { class Caller { void Run() { Target.Work(); } } }",
        "namespace N { static partial class Target { public static void Work() {} } }",
    )
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)
    assert call.target_id == "Target:N.Target.Work" and call.confidence.value == "inferred"
    assert {n["id"] for n in QueryEngine.from_graph(graph).callers_of(call.target_id)} == {
        "Caller:N.Caller.Run"
    }


@pytest.mark.mutation_fast
def test_inspected_interface_base_does_not_hide_namespace_type(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        "namespace N { class Caller : Target.Marker { void Run() { Target.Work(); } } }",
        "namespace N { static partial class Target { public interface Marker {} "
        "public static void Work() {} } }",
    )
    assert any(
        e.kind == EdgeKind.CALLS and e.target_id == "Target:N.Target.Work" for e in graph.edges
    )


@pytest.mark.mutation_fast
@pytest.mark.parametrize("declaration", ["dynamic Target", "System.Action Target"])
def test_parameter_receiver_does_not_bind_type(tmp_path: Path, declaration: str) -> None:
    graph = _parse(
        tmp_path,
        f"namespace N {{ class Caller {{ void Run({declaration}) {{ Target.Work(); }} }} }}",
        "namespace N { static class Target { public static void Work() {} } }",
    )
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert calls and all(graph.nodes[e.target_id].origin == NodeOrigin.PROXY for e in calls)


@pytest.mark.mutation_fast
@pytest.mark.parametrize("prefix", ["using Target = Other;", "using Other;"])
def test_alias_or_imported_outer_type_does_not_receive_guess(tmp_path: Path, prefix: str) -> None:
    graph = _parse(
        tmp_path,
        prefix + " namespace N { class Caller { void Run() { Target.Work(); } } }",
        "static class Target { public static void Work() {} }",
    )
    assert all(
        graph.nodes[e.target_id].origin == NodeOrigin.PROXY
        for e in graph.edges
        if e.kind == EdgeKind.CALLS
    )


@pytest.mark.mutation_fast
def test_global_qualification_bypasses_alias_and_value_names(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        "using Target = Other; namespace N { class Caller { void Run(dynamic Target) { "
        "global::N.Target.Work(); } } }",
        "namespace N { static class Target { public static void Work() {} } }",
    )
    assert any(
        e.kind == EdgeKind.CALLS and e.target_id == "Target:N.Target.Work" for e in graph.edges
    )


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    "caller",
    [
        "class Caller<Target> { void Run() { Target.Work(); } }",
        "class Caller { void Run<Target>() { Target.Work(); } }",
        "class Caller : Unknown { void Run() { Target.Work(); } }",
        "partial class Caller { dynamic Target; void Run() { Target.Work(); } }",
    ],
)
def test_generic_unknown_base_and_member_barriers(tmp_path: Path, caller: str) -> None:
    graph = _parse(
        tmp_path,
        "namespace N { " + caller + " }",
        "namespace N { static class Target { public static void Work() {} } }",
    )
    assert all(
        graph.nodes[e.target_id].origin == NodeOrigin.PROXY
        for e in graph.edges
        if e.kind == EdgeKind.CALLS
    )


def test_instance_and_overloaded_methods_remain_proxies(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        "namespace N { class Caller { void Run() { Target.Work(); } } }",
        "namespace N { class Target { public void Work() {} public static void Work(int x) {} } }",
    )
    assert all(
        graph.nodes[e.target_id].origin == NodeOrigin.PROXY
        for e in graph.edges
        if e.kind == EdgeKind.CALLS
    )


@pytest.mark.mutation_fast
def test_later_global_alias_retracts_per_file_binding(tmp_path: Path) -> None:
    (tmp_path / "Alias.cs").write_text("global using Target = Other;")
    graph = _parse(
        tmp_path,
        "namespace N { static class Target { public static void Work() {} } "
        "class Caller { void Run() { Target.Work(); } } }",
    )
    assert all(
        graph.nodes[e.target_id].origin == NodeOrigin.PROXY
        for e in graph.edges
        if e.kind == EdgeKind.CALLS
    )


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    "body",
    [
        "try {} catch (Other Target) { Target.Work(); }",
        "foreach (dynamic Target in items) { Target.Work(); }",
        "if (items is Other Target) { Target.Work(); }",
        "TryGet(out var Target); Target.Work();",
        "System.Action<dynamic> action = (Target) => Target.Work();",
    ],
)
def test_complex_value_bindings_remain_proxies(tmp_path: Path, body: str) -> None:
    graph = _parse(
        tmp_path,
        "namespace N { class Caller { void Run(dynamic items) { " + body + " } } }",
        "namespace N { static class Target { public static void Work() {} } }",
    )
    work = [
        e
        for e in graph.edges
        if e.kind == EdgeKind.CALLS
        and str(dict(e.attributes).get("raw_target") or e.target_id).endswith("Target.Work")
    ]
    assert work and all(graph.nodes[e.target_id].origin == NodeOrigin.PROXY for e in work)


def test_later_partial_value_member_retracts_per_file_type_guess(tmp_path: Path) -> None:
    (tmp_path / "OtherPart.cs").write_text(
        "namespace N { partial class Caller { dynamic Target; } }"
    )
    graph = _parse(
        tmp_path,
        "namespace N { partial class Caller { void Run() { Target.Work(); } } }",
        "namespace N { static class Target { public static void Work() {} } }",
    )
    assert all(
        graph.nodes[e.target_id].origin == NodeOrigin.PROXY
        for e in graph.edges
        if e.kind == EdgeKind.CALLS
    )


@pytest.mark.mutation_fast
def test_nearer_namespace_head_blocks_outer_type_fallback(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        "namespace N { namespace Helpers {} class Caller { void Run() { "
        "Helpers.Target.Work(); } } }",
        "namespace Helpers { static class Target { public static void Work() {} } }",
    )
    assert all(
        graph.nodes[e.target_id].origin == NodeOrigin.PROXY
        for e in graph.edges
        if e.kind == EdgeKind.CALLS
    )
