"""Literal self calls require one compatible inspected impl method family."""

from pathlib import Path

import pytest

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.nodes import NodeOrigin
from trailmark.parse import parse_file
from trailmark.query.api import QueryEngine


@pytest.mark.mutation_fast
def test_component_self_calls_link_across_matching_generic_impls(tmp_path: Path) -> None:
    path = tmp_path / "Component.cairo"
    path.write_text(
        "mod Component {\n"
        "impl Public<T, +HasComponent<T>> of Operations<State<T>> {\n"
        " fn run(ref self: State<T>) { self.check(); self.check(); }\n}\n"
        "impl Internal<T, +HasComponent<T>,> of InternalTrait<T> {\n"
        " fn check(self: @State<T>) {}\n}\n}\n"
    )
    graph = parse_file(str(path), "cairo", strict=True)
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]

    assert len(calls) == 2
    assert {e.target_id for e in calls} == {"Component:Component.Internal.check"}
    assert all(e.confidence == EdgeConfidence.INFERRED for e in calls)
    assert len({e.location.start_col for e in calls if e.location}) == 2
    assert QueryEngine.from_graph(graph).paths_between(calls[0].source_id, calls[0].target_id)


@pytest.mark.mutation_fast
def test_self_family_keeps_nongeneric_helpers(tmp_path: Path) -> None:
    path = tmp_path / "Types.cairo"
    path.write_text(
        "impl Caller of CallerTrait { fn run(ref self: State) { self.check(); } }\n"
        "impl Right of RightTrait { fn check(self: @State) {} }\n"
    )
    graph = parse_file(str(path), "cairo", strict=True)
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)

    assert call.target_id == "Types:Right.check"


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    "declarations",
    [
        "impl Other<T, +OtherBound<T>> of Trait<T> { fn check(self: @State<T>) {} }",
        "impl Other<T, +HasComponent<T>> of Trait<T> { fn check(self: @OtherState<T>) {} }",
        "impl Other<T, +HasComponent<T>> of Trait<T> { fn check(ref self: State<T>) {} }",
        "impl A<T, +HasComponent<T>> of A<T> { fn check(self: @State<T>) {} } "
        "impl B<T, +HasComponent<T>> of B<T> { fn check(self: @State<T>) {} }",
        "impl A<T, +HasComponent<T>> of A<T> { fn check(self: @State<T>) {} } "
        "impl B<T, +OtherBound<T>> of B<T> { fn check(self: @State<T>) {} }",
        "impl A<T, +HasComponent<T>> of A<T> { fn check(self: @State<T>) {} } "
        "impl B<T, +HasComponent<T>> of B<T> { fn check<U>(self: @State<T>) {} }",
        "impl A<T, +HasComponent<T>> of A<T> { fn check(self: @State<T>) {} } "
        "impl B<T, +HasComponent<T>> of B<T> { type Alias = State<T>; fn check(self: @Alias) {} }",
        "type Alias<T> = State<T>; "
        "impl A<T, +HasComponent<T>> of A<T> { fn check(self: @State<T>) {} } "
        "impl B<T, +HasComponent<T>> of B<T> { fn check(self: @Alias<T>) {} }",
        "mod Child { impl Other<T, +HasComponent<T>> of Trait<T> { "
        "fn check(self: @State<T>) {} } }",
        "impl Other<T, +HasComponent<T>> of Trait<T> { "
        "type Alias = State<T>; fn check(self: @State<T>) {} }",
        "impl Other<T, +HasComponent<T>> of Trait<T> { "
        "fn check(self: @State<T>) {} fn check(self: @State<T>) {} }",
        "impl Other<T, +HasComponent<T>> of Trait<T> { fn check(self: @State<T>) {} } "
        "impl Other<T, +HasComponent<T>> of Trait<T> { fn check(self: @State<T>) {} }",
    ],
)
def test_unproven_or_competing_self_families_remain_proxies(
    tmp_path: Path, declarations: str
) -> None:
    path = tmp_path / "Conservative.cairo"
    path.write_text(
        "impl Caller<T, +HasComponent<T>> of CallerTrait<T> { "
        "fn run(self: @State<T>) { self.check(); } }\n" + declarations + "\n"
    )
    graph = parse_file(str(path), "cairo", strict=True)
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)

    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY
    assert call.confidence == EdgeConfidence.UNCERTAIN


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    "method, invocation",
    [
        ("fn run<U>(self: @State<T>)", "self.check()"),
        ("fn run(value: @State<T>)", "value.check()"),
    ],
)
def test_generic_methods_and_other_receivers_do_not_gain_self_bindings(
    tmp_path: Path, method: str, invocation: str
) -> None:
    path = tmp_path / "Unknown.cairo"
    path.write_text(
        f"impl Caller<T, +HasComponent<T>> of CallerTrait<T> {{ {method} {{ {invocation}; }} }}\n"
        "impl Target<T, +HasComponent<T>> of TargetTrait<T> { fn check(self: @State<T>) {} }\n"
    )
    graph = parse_file(str(path), "cairo", strict=True)
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)

    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY


def test_incompatible_argument_count_does_not_supply_a_self_target(tmp_path: Path) -> None:
    path = tmp_path / "Arity.cairo"
    path.write_text(
        "impl Caller of CallerTrait { fn run(self: @State) { self.check(1); } }\n"
        "impl Target of TargetTrait { fn check(self: @State) {} }\n"
    )
    graph = parse_file(str(path), "cairo", strict=True)
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)

    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY
