"""The supplied-parameter tie is useful without guessing argument types."""

from pathlib import Path

import pytest

from trailmark.models.edges import CodeEdge, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeOrigin
from trailmark.parse import parse_directory
from trailmark.query.api import QueryEngine


def _parse(
    tmp_path: Path, methods: str, call: str = 'Add("x");', prefix: str = "", base: str = ""
) -> tuple[CodeGraph, CodeEdge]:
    source = tmp_path / "Example.cs"
    source.write_text(
        prefix
        + " namespace N { class Bag "
        + base
        + " { "
        + methods
        + " void Run() { "
        + call
        + " } } }"
    )
    graph = parse_directory(str(tmp_path), "c_sharp", strict=True)
    assert not graph.parse_diagnostics
    calls = [
        e
        for e in graph.edges
        if e.kind == EdgeKind.CALLS and graph.nodes[e.source_id].name == "Run"
    ]
    assert len(calls) == 1
    return graph, calls[0]


@pytest.mark.mutation_fast
def test_exact_supplied_signature_wins_over_default_substitution(tmp_path: Path) -> None:
    graph, call = _parse(tmp_path, "void Add(string x) {} void Add(string x, int n = 0) {}")
    target = graph.nodes[call.target_id]
    assert target.origin != NodeOrigin.PROXY and len(target.parameters) == 1
    assert call.confidence.value == "inferred" and call.location is not None
    engine = QueryEngine.from_graph(graph)
    assert any(n["id"] == call.source_id for n in engine.callers_of(target.id))


def test_zero_arguments_selects_declared_zero_parameter_method(tmp_path: Path) -> None:
    graph, call = _parse(tmp_path, "void Add() {} void Add(int n = 0) {}", "Add();")
    assert graph.nodes[call.target_id].origin != NodeOrigin.PROXY
    assert not graph.nodes[call.target_id].parameters


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    "methods,call",
    [
        ("void Add(object x) {} void Add(string x, int n = 0) {}", 'Add("x");'),
        ("void Add(string x) {} void Add(int x) {}", 'Add("x");'),
        ("void Add(string x, int n = 0) {} void Add(string x, bool b = false) {}", 'Add("x");'),
        ("void Add(string x) {} void Add(string x, params int[] rest) {}", 'Add("x");'),
        ("void Add<T>(string x) {} void Add(string x, int n = 0) {}", 'Add("x");'),
        ("void Add(string x) {} void Add(string x, int n = 0) {}", 'Add(x: "x");'),
        ("void Add(string x) {} void Add(string x, int n = 0) {}", 'Add<int>("x");'),
        ("void Add(ref string x) {} void Add(string x, int n = 0) {}", 'Add("x");'),
        ("public virtual void Add(string x) {} void Add(string x, int n = 0) {}", 'Add("x");'),
        ("void Add(string x) {} static void Add(string x, int n = 0) {}", 'Add("x");'),
    ],
)
def test_unmodeled_overload_comparisons_stay_uncertain(
    tmp_path: Path, methods: str, call: str
) -> None:
    graph, edge = _parse(tmp_path, methods, call)
    assert graph.nodes[edge.target_id].origin == NodeOrigin.PROXY


@pytest.mark.mutation_fast
def test_unseen_or_class_base_can_supply_the_applicable_overload(tmp_path: Path) -> None:
    graph, call = _parse(
        tmp_path,
        "void Add(int x) {} void Add(int x, int n = 0) {}",
        base=": Base",
        prefix="class Base { public void Add(string x) {} }",
    )
    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY


@pytest.mark.mutation_fast
def test_inspected_interface_base_allows_own_method_family(tmp_path: Path) -> None:
    graph, call = _parse(
        tmp_path,
        "void Add(string x) {} void Add(string x, int n = 0) {}",
        prefix="namespace N { interface Marker {} }",
        base=": Marker",
    )
    assert graph.nodes[call.target_id].origin != NodeOrigin.PROXY
    assert len(graph.nodes[call.target_id].parameters) == 1


@pytest.mark.mutation_fast
def test_base_alias_cannot_masquerade_as_inspected_interface(tmp_path: Path) -> None:
    graph, call = _parse(
        tmp_path,
        "void Add(int x) {} void Add(int x, int n = 0) {}",
        prefix="using Marker = Other; class Other { public void Add(string x) {} } "
        "namespace N { interface Marker {} }",
        base=": Marker",
    )
    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY


def test_directory_merge_retracts_incomplete_partial_family(tmp_path: Path) -> None:
    (tmp_path / "A.cs").write_text(
        "namespace N { partial class Bag { void Add(string x) {} "
        'void Add(string x, int n = 0) {} void Run() { Add("x"); } } }'
    )
    (tmp_path / "B.cs").write_text("namespace N { partial class Bag { void Add(object x) {} } }")
    graph = parse_directory(str(tmp_path), "c_sharp", strict=True)
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)
    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY


def test_directory_merge_retracts_later_global_base_alias(tmp_path: Path) -> None:
    (tmp_path / "A.cs").write_text(
        "namespace N { interface Marker {} class Bag : Marker { "
        "void Add(string x) {} void Add(string x, int n = 0) {} "
        'void Run() { Add("x"); } } }'
    )
    (tmp_path / "B.cs").write_text("global using Marker = ExternalBase;")
    graph = parse_directory(str(tmp_path), "c_sharp", strict=True)
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)
    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY


def test_same_file_partial_fragments_with_different_type_alias_contexts_stay_uncertain(
    tmp_path: Path,
) -> None:
    (tmp_path / "A.cs").write_text(
        "namespace N { using T = System.Object; partial class Bag { void Add(T x) {} } } "
        "namespace N { using T = System.String; partial class Bag { "
        'void Add(T x, int n = 0) {} void Run() { Add("x"); } } }'
    )
    graph = parse_directory(str(tmp_path), "c_sharp", strict=True)
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)
    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY


@pytest.mark.mutation_fast
def test_nested_class_base_prevents_outer_family_fallback(tmp_path: Path) -> None:
    (tmp_path / "A.cs").write_text(
        "class Base { public void Add(string x) {} } "
        "class Outer { static void Add(string x) {} static void Add(string x, int n = 0) {} "
        'class Inner : Base { void Run() { Add("x"); } } }'
    )
    graph = parse_directory(str(tmp_path), "c_sharp", strict=True)
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)
    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY


@pytest.mark.mutation_fast
def test_explicit_interface_implementation_is_not_a_bare_name_candidate(tmp_path: Path) -> None:
    (tmp_path / "A.cs").write_text(
        "interface I { void M(int x); } class C : I { void I.M(int x) {} "
        "void M(int x,int y=0) {} void Run(){ M(1); } }"
    )
    graph = parse_directory(str(tmp_path), "c_sharp", strict=True)
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)
    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY


def test_overload_priority_attribute_blocks_the_optional_default_tie(tmp_path: Path) -> None:
    graph, call = _parse(
        tmp_path,
        "void Add(string x) {} [OverloadResolutionPriority(1)] void Add(string x, int n = 0) {}",
    )
    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY
    longer = next(u for u in graph.nodes.values() if u.name == "Add" and len(u.parameters) == 2)
    assert "csharp_optional_signature" not in dict(longer.attributes)
