"""Anonymous Java subclasses keep inspected inherited helper navigation."""

from pathlib import Path

import pytest

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeOrigin
from trailmark.parse import parse_directory, parse_file


def _anonymous_call(
    tmp_path: Path, base: str, body: str = "helper();", outer: str = ""
) -> tuple[CodeGraph, CodeEdge]:
    source = tmp_path / "sample.java"
    source.write_text(
        "class Outer {\n"
        + base
        + "\n"
        + outer
        + "\nBase make() { return new Base<String>() { void run() { "
        + body
        + " } }; }\n}\n"
    )
    graph = parse_file(str(source), "java")
    call = next(
        edge
        for edge in graph.edges
        if edge.kind == EdgeKind.CALLS
        and "$anonymous@" in edge.source_id
        and edge.source_id.endswith(".run")
    )
    return graph, call


def test_anonymous_subclass_binds_generic_base_helper_before_enclosing_method(
    tmp_path: Path,
) -> None:
    graph, call = _anonymous_call(
        tmp_path, "class Base<T> { void helper() {} }", outer="void helper() {}"
    )
    assert call.target_id == "sample:Outer.Base.helper"
    assert call.confidence == EdgeConfidence.INFERRED
    assert call.location is not None and call.location.start_line == 4
    inheritance = [e for e in graph.edges if dict(e.attributes).get("java_anonymous_base")]
    assert len(inheritance) == 1
    assert inheritance[0].kind == EdgeKind.INHERITS
    assert inheritance[0].target_id == "sample:Outer.Base"
    assert inheritance[0].location is not None
    assert inheritance[0].location.start_line == 4


def test_anonymous_own_override_wins(tmp_path: Path) -> None:
    graph, call = _anonymous_call(
        tmp_path, "class Base<T> { void helper() {} }", body="helper(); } void helper() {"
    )
    assert call.target_id == call.source_id.removesuffix(".run") + ".helper"
    assert graph.nodes[call.target_id].origin == NodeOrigin.SOURCE


@pytest.mark.parametrize(
    "base",
    [
        "class Base<T> { void helper() {} void helper(int value) {} }",
        "class Base<T> { private void helper() {} }",
        "class Parent extends Base {} class Base<T> extends Parent {}",
    ],
)
def test_ambiguous_private_or_cyclic_inheritance_does_not_guess(tmp_path: Path, base: str) -> None:
    graph, call = _anonymous_call(tmp_path, base, outer="void helper() {}")
    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY
    assert call.confidence == EdgeConfidence.UNCERTAIN


def test_anonymous_subclass_can_reach_inspected_grandparent(tmp_path: Path) -> None:
    _, call = _anonymous_call(
        tmp_path, "class Parent { void helper() {} } class Base<T> extends Parent {}"
    )
    assert call.target_id == "sample:Outer.Parent.helper"


@pytest.mark.parametrize(
    "base,confidence",
    [
        ("class Base<T> {}", EdgeConfidence.INFERRED),
        ("", EdgeConfidence.UNCERTAIN),
        ("interface Base<T> {}", EdgeConfidence.UNCERTAIN),
    ],
)
@pytest.mark.parametrize("directory", [False, True])
def test_missing_inherited_method_preserves_enclosing_helper(
    tmp_path: Path, base: str, confidence: EdgeConfidence, directory: bool
) -> None:
    graph, call = _anonymous_call(tmp_path, base, outer="void helper() {}")
    if directory:
        graph = parse_directory(str(tmp_path), "java")
        call = next(e for e in graph.edges if e.source_id == call.source_id)
    assert call.target_id == "sample:Outer.helper"
    assert graph.nodes[call.target_id].origin == NodeOrigin.SOURCE
    assert call.confidence == confidence


def test_external_interface_callback_preserves_enclosing_helper(tmp_path: Path) -> None:
    source = tmp_path / "Outer.java"
    source.write_text(
        "class Outer { void transferContext(String s) {}\n"
        "void run() { Runnable r = new Runnable() { public void run() { "
        'transferContext("x"); } }; }\n'
        "}\n"
    )
    graph = parse_directory(str(tmp_path), "java")
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS and "$anonymous@" in e.source_id)
    assert call.target_id == "Outer:Outer.transferContext"
    assert call.confidence == EdgeConfidence.UNCERTAIN


@pytest.mark.parametrize(
    "base,blocked",
    [
        ("interface Base<T> { default void helper() {} }", True),
        (
            "interface Mixin { default void helper() {} } class Base<T> implements Mixin {}",
            True,
        ),
        ("class Base<T> implements External {}", False),
        ("interface Mixin {} class Base<T> implements Mixin {}", False),
    ],
)
def test_interface_dispatch_never_makes_enclosing_fallback_inferred(
    tmp_path: Path, base: str, blocked: bool
) -> None:
    graph, call = _anonymous_call(tmp_path, base, outer="void helper() {}")
    if blocked:
        assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY
    else:
        assert call.target_id == "sample:Outer.helper"
    assert call.confidence == EdgeConfidence.UNCERTAIN


def test_inherited_overloads_across_generations_are_not_guessed(tmp_path: Path) -> None:
    graph, call = _anonymous_call(
        tmp_path,
        "class Parent { void helper(int x) {} } class Base<T> extends Parent { void helper() {} }",
    )
    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY


def test_nearest_override_of_same_signature_wins(tmp_path: Path) -> None:
    _, call = _anonymous_call(
        tmp_path,
        "class Parent { void helper() {} } class Base<T> extends Parent { void helper() {} }",
    )
    assert call.target_id == "sample:Outer.Base.helper"


def test_anonymous_interface_uses_implements_and_own_method(tmp_path: Path) -> None:
    graph, call = _anonymous_call(
        tmp_path,
        "interface Base<T> { void helper(); }",
        body="helper(); } public void helper() {",
    )
    relation = next(e for e in graph.edges if dict(e.attributes).get("java_anonymous_base"))
    assert relation.kind == EdgeKind.IMPLEMENTS
    assert relation.target_id == "sample:Outer.Base"
    assert call.target_id == call.source_id.removesuffix(".run") + ".helper"


def test_anonymous_base_is_revisited_after_directory_merge(tmp_path: Path) -> None:
    (tmp_path / "base.java").write_text("class Base { void helper() {} }\n")
    (tmp_path / "entry.java").write_text(
        "class Entry { Base make() { return new Base() { void run() { helper(); } }; } }\n"
    )
    graph = parse_directory(str(tmp_path), "java")
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS and "$anonymous@" in e.source_id)
    assert call.target_id == "base:Base.helper"
    assert graph.nodes[call.target_id].origin == NodeOrigin.SOURCE


def test_ambiguous_remote_base_does_not_select_unrelated_helper(tmp_path: Path) -> None:
    for name in ("first", "second"):
        (tmp_path / f"{name}.java").write_text("class Base { void helper() {} }\n")
    (tmp_path / "entry.java").write_text(
        "class Entry { Base make() { return new Base() { void run() { helper(); } }; } }\n"
    )
    graph = parse_directory(str(tmp_path), "java")
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS and "$anonymous@" in e.source_id)
    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY
    relation = next(e for e in graph.edges if dict(e.attributes).get("java_anonymous_base"))
    assert relation.kind == EdgeKind.TYPE_USES


@pytest.mark.parametrize("visibility", ["", "protected", "public"])
def test_inherited_access_respects_packages(tmp_path: Path, visibility: str) -> None:
    (tmp_path / "base.java").write_text(
        f"package a; public class Base {{ {visibility} void helper() {{}} }}\n"
    )
    (tmp_path / "entry.java").write_text(
        "package b; class Entry { void helper() {} "
        "a.Base make() { return new a.Base() { void run() { helper(); } }; } }\n"
    )
    graph = parse_directory(str(tmp_path), "java")
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS and "$anonymous@" in e.source_id)
    # Package-qualified base resolution is deliberately verified below rather
    # than allowing an unrelated unique Base to satisfy a different qualifier.
    if visibility:
        assert call.target_id == "base:Base.helper"
    else:
        assert call.target_id != "base:Base.helper"
        assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY
