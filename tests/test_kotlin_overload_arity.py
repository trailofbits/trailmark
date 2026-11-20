"""Only fixed positional shape can disambiguate these local Kotlin calls."""

from pathlib import Path

import pytest

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.nodes import NodeOrigin
from trailmark.parse import parse_file
from trailmark.query.api import QueryEngine


def test_fixed_arity_wrappers_keep_overload_identity_and_each_site(tmp_path: Path) -> None:
    path = tmp_path / "Logging.kt"
    path.write_text(
        "class Tree {\n"
        " fun enabled(priority: Int): Boolean = true\n"
        " fun enabled(tag: String, priority: Int): Boolean = enabled(priority)\n"
        ' fun run() { enabled(1); enabled("tag", 2); enabled(3) }\n'
        "}\n"
    )
    graph = parse_file(str(path), "kotlin", strict=True)
    overloads = {len(n.parameters): n.id for n in graph.nodes.values() if n.name == "enabled"}
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]

    assert len(calls) == 4
    assert all(e.target_id in overloads.values() for e in calls)
    assert all(e.confidence == EdgeConfidence.INFERRED for e in calls)
    assert sum(e.target_id == overloads[1] for e in calls) == 3
    assert sum(e.target_id == overloads[2] for e in calls) == 1
    assert (
        len(
            {
                e.location.start_col
                for e in calls
                if e.source_id == "Logging:Tree.run" and e.location
            }
        )
        == 3
    )
    engine = QueryEngine.from_graph(graph)
    assert engine.paths_between("Logging:Tree.run", overloads[1])
    assert engine.paths_between(overloads[2], overloads[1])


def test_module_overloads_and_nearest_class_family_remain_separate(tmp_path: Path) -> None:
    path = tmp_path / "Scope.kt"
    path.write_text(
        "fun choose() {}\nfun choose(x: Int) {}\n"
        "class C {\n fun choose(x: Int, y: Int) {}\n"
        " fun choose(x: Int, y: Int, z: Int) {}\n"
        " fun run() { choose(1, 2) }\n}\n"
        "fun run() { choose(); choose(1) }\n"
    )
    graph = parse_file(str(path), "kotlin", strict=True)
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]

    assert len(calls) == 3
    assert all(graph.nodes[e.target_id].origin == NodeOrigin.SOURCE for e in calls)
    assert {len(graph.nodes[e.target_id].parameters) for e in calls} == {0, 1, 2}
    class_call = next(e for e in calls if e.source_id == "Scope:C.run")
    assert class_call.target_id.startswith("Scope:C.choose@")


@pytest.mark.parametrize(
    "declarations",
    [
        "fun choose(x: Int) {}\n fun choose(x: String) {}",
        "fun choose(x: Int) {}\n fun choose(x: Int, y: Int = 0) {}",
        "fun choose(x: Int) {}\n fun choose(vararg x: Int) {}",
        "fun choose(x: Int) {}\n fun String.choose(x: Int, y: Int) {}",
    ],
)
def test_competing_shapes_remain_uncertain(tmp_path: Path, declarations: str) -> None:
    path = tmp_path / "Ambiguous.kt"
    path.write_text(f"class C {{ {declarations}\n fun run() {{ choose(1) }}\n}}\n")
    graph = parse_file(str(path), "kotlin", strict=True)
    call = next(
        e for e in graph.edges if e.source_id == "Ambiguous:C.run" and e.kind == EdgeKind.CALLS
    )

    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY
    assert call.confidence == EdgeConfidence.UNCERTAIN


@pytest.mark.parametrize("application", ["choose(x = 1)", "choose(1) {}"])
def test_named_and_trailing_lambda_arguments_are_not_counted_as_plain_slots(
    tmp_path: Path, application: str
) -> None:
    path = tmp_path / "Arguments.kt"
    path.write_text(
        "class C {\n fun choose(x: Int) {}\n"
        " fun choose(x: Int, action: () -> Unit) {}\n"
        f" fun run() {{ {application} }}\n}}\n"
    )
    graph = parse_file(str(path), "kotlin", strict=True)
    call = next(
        e for e in graph.edges if e.source_id == "Arguments:C.run" and e.kind == EdgeKind.CALLS
    )

    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY


def test_callable_parameter_still_blocks_overload_binding(tmp_path: Path) -> None:
    path = tmp_path / "Shadow.kt"
    path.write_text(
        "fun choose(x: Int) {}\n fun choose(x: Int, y: Int) {}\n"
        "fun run(choose: (Int) -> Unit) { choose(1) }\n"
    )
    graph = parse_file(str(path), "kotlin", strict=True)
    call = next(e for e in graph.edges if e.source_id == "Shadow:run" and e.kind == EdgeKind.CALLS)

    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY
    assert dict(call.attributes)["binding_blocked"] is True


def test_inherited_candidates_prevent_member_family_arity_selection(tmp_path: Path) -> None:
    path = tmp_path / "Inherited.kt"
    path.write_text(
        "open class Base { fun log(x: String) {} }\n"
        "class Child : Base() {\n fun log(x: Int) {}\n"
        ' fun log(x: Int, y: Int) {}\n fun run() { log("text") }\n}\n'
    )
    graph = parse_file(str(path), "kotlin", strict=True)
    call = next(
        e for e in graph.edges if e.source_id == "Inherited:Child.run" and e.kind == EdgeKind.CALLS
    )

    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY


def test_implicit_any_candidates_are_not_assumed_absent(tmp_path: Path) -> None:
    path = tmp_path / "Implicit.kt"
    path.write_text(
        "class C {\n fun equals(x: Int): Boolean = true\n"
        " fun equals(x: Int, y: Int): Boolean = true\n"
        ' fun run() { equals("text") }\n}\n'
    )
    graph = parse_file(str(path), "kotlin", strict=True)
    call = next(
        e for e in graph.edges if e.source_id == "Implicit:C.run" and e.kind == EdgeKind.CALLS
    )

    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY


def test_data_class_generated_candidates_are_not_assumed_absent(tmp_path: Path) -> None:
    path = tmp_path / "Generated.kt"
    path.write_text(
        "data class C(val value: Int) {\n fun copy(x: String) {}\n"
        " fun copy(x: String, y: String) {}\n fun run() { copy(1) }\n}\n"
    )
    graph = parse_file(str(path), "kotlin", strict=True)
    call = next(
        e for e in graph.edges if e.source_id == "Generated:C.run" and e.kind == EdgeKind.CALLS
    )

    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY
