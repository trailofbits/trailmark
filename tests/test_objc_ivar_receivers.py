"""Declared ivar types support bounded instance-selector navigation."""

from pathlib import Path

import pytest

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.nodes import NodeOrigin
from trailmark.parse import parse_directory, parse_file
from trailmark.query.api import QueryEngine


@pytest.mark.mutation_fast
def test_header_property_backing_ivars_prefer_implementation_methods(tmp_path: Path) -> None:
    (tmp_path / "Owner.h").write_text(
        "@interface Worker\n- (void)reset;\n@end\n"
        "@interface Owner\n@property Worker *worker;\n@end\n"
    )
    (tmp_path / "Worker.m").write_text("@implementation Worker\n- (void)reset {}\n@end\n")
    (tmp_path / "Owner.m").write_text(
        "@implementation Owner\n- (void)close { [_worker reset]; [_worker reset]; }\n@end\n"
    )
    graph = parse_directory(str(tmp_path), "objc", strict=True)
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]

    assert len(calls) == 2
    assert {e.target_id for e in calls} == {"Worker:Worker.reset"}
    assert all(e.confidence == EdgeConfidence.INFERRED for e in calls)
    assert len({e.location.start_col for e in calls if e.location}) == 2
    assert QueryEngine.from_graph(graph).paths_between(calls[0].source_id, "Worker:Worker.reset")


def test_explicit_typed_ivar_and_selector_labels_are_retained(tmp_path: Path) -> None:
    path = tmp_path / "Declared.m"
    path.write_text(
        "@interface Worker\n- (void)send:(id)value next:(id)other;\n@end\n"
        "@interface Owner { Worker *_worker; }\n@end\n"
        "@implementation Worker\n- (void)send:(id)value next:(id)other {}\n@end\n"
        "@implementation Owner\n- (void)run { [_worker send:nil next:nil]; }\n@end\n"
    )
    graph = parse_file(str(path), "objc", strict=True)
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)

    assert call.target_id == "Declared:Worker.send:next:"


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    "implementation",
    [
        "- (void)run:(id)_worker { [_worker reset]; }",
        "- (void)run { id _worker = nil; [_worker reset]; }",
        "- (void)run:(id)_worker { void (^block)(void) = ^{ [_worker reset]; }; }",
        "+ (void)run { [_worker reset]; }",
        "- (void)run:(id)items { for (id _worker in items) { [_worker reset]; } }",
        "- (void)run { @try {} @catch (id _worker) { [_worker reset]; } }",
    ],
)
def test_shadowed_or_class_context_receivers_remain_proxies(
    tmp_path: Path, implementation: str
) -> None:
    path = tmp_path / "Shadow.m"
    path.write_text(
        "@interface Worker\n- (void)reset;\n@end\n"
        "@interface Owner { Worker *_worker; }\n@end\n"
        f"@implementation Owner\n{implementation}\n@end\n"
    )
    graph = parse_file(str(path), "objc", strict=True)
    calls = [e for e in graph.edges if dict(e.attributes).get("objc_selector") == "reset"]

    assert calls
    assert all(graph.nodes[e.target_id].origin == NodeOrigin.PROXY for e in calls)


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    "customization",
    [
        "@dynamic worker;",
        "@synthesize worker = chosen;",
        "- (Worker *)worker { return nil; }",
        "- (void)setWorker:(Worker *)value {}",
    ],
)
def test_custom_property_implementations_do_not_invent_backing_ivars(
    tmp_path: Path, customization: str
) -> None:
    path = tmp_path / "Custom.m"
    path.write_text(
        "@interface Worker\n- (void)reset;\n@end\n"
        "@interface Owner\n@property Worker *worker;\n@end\n"
        f"@implementation Owner\n{customization}\n"
        "- (void)run { [_worker reset]; }\n@end\n"
    )
    graph = parse_file(str(path), "objc", strict=True)
    call = next(e for e in graph.edges if dict(e.attributes).get("objc_selector") == "reset")

    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY


@pytest.mark.mutation_fast
def test_conflicting_class_implementations_do_not_supply_arbitrary_targets(tmp_path: Path) -> None:
    (tmp_path / "Owner.m").write_text(
        "@interface Owner { Worker *_worker; }\n@end\n"
        "@implementation Owner\n- (void)run { [_worker reset]; }\n@end\n"
    )
    for name in ["First", "Second"]:
        (tmp_path / f"{name}.m").write_text("@implementation Worker\n- (void)reset {}\n@end\n")
    graph = parse_directory(str(tmp_path), "objc", strict=True)
    call = next(e for e in graph.edges if dict(e.attributes).get("objc_selector") == "reset")

    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY


@pytest.mark.mutation_fast
def test_class_property_does_not_create_an_instance_backing_ivar(tmp_path: Path) -> None:
    path = tmp_path / "ClassProperty.m"
    path.write_text(
        "@interface Worker\n- (void)reset;\n@end\n"
        "@interface Owner\n@property (class) Worker *worker;\n@end\n"
        "@implementation Owner\n- (void)run { [_worker reset]; }\n@end\n"
    )
    graph = parse_file(str(path), "objc", strict=True)
    call = next(e for e in graph.edges if dict(e.attributes).get("objc_selector") == "reset")

    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY


def test_unshadowed_backing_ivar_is_available_inside_a_block(tmp_path: Path) -> None:
    path = tmp_path / "Capture.m"
    path.write_text(
        "@interface Worker\n- (void)reset;\n@end\n"
        "@interface Owner { Worker *_worker; }\n@end\n"
        "@implementation Owner\n"
        "- (void)run { void (^block)(void) = ^{ [_worker reset]; }; }\n@end\n"
    )
    graph = parse_file(str(path), "objc", strict=True)
    call = next(e for e in graph.edges if dict(e.attributes).get("objc_selector") == "reset")

    assert call.target_id == "Capture:Worker.reset"


@pytest.mark.mutation_fast
@pytest.mark.parametrize("parent_property", ["Worker *worker", "id worker", "int worker"])
def test_inherited_property_redeclaration_does_not_synthesize_a_new_ivar(
    tmp_path: Path, parent_property: str
) -> None:
    path = tmp_path / "Inherited.m"
    path.write_text(
        "@interface Worker\n- (void)reset;\n@end\n"
        f"@interface Parent\n@property (readonly) {parent_property};\n@end\n"
        "@interface Child : Parent\n@property (readonly) Worker *worker;\n@end\n"
        "@implementation Child\n- (void)run { [_worker reset]; }\n@end\n"
    )
    graph = parse_file(str(path), "objc", strict=True)
    call = next(e for e in graph.edges if dict(e.attributes).get("objc_selector") == "reset")

    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY
    assert not graph.parse_diagnostics


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    ("base", "property_name", "expected_source"),
    [("Missing", "worker", False), ("NSObject", "worker", True), ("NSObject", "hash", False)],
)
def test_unknown_ancestor_property_surface_is_conservative(
    tmp_path: Path, base: str, property_name: str, expected_source: bool
) -> None:
    path = tmp_path / "Ancestor.m"
    path.write_text(
        "@interface Worker\n- (void)reset;\n@end\n"
        f"@interface Owner : {base}\n@property Worker *{property_name};\n@end\n"
        f"@implementation Owner\n- (void)run {{ [_{property_name} reset]; }}\n@end\n"
    )
    graph = parse_file(str(path), "objc", strict=True)
    call = next(e for e in graph.edges if dict(e.attributes).get("objc_selector") == "reset")

    assert (graph.nodes[call.target_id].origin == NodeOrigin.SOURCE) == expected_source


def test_same_file_duplicate_implementations_are_not_collapsed_for_type_binding(
    tmp_path: Path,
) -> None:
    path = tmp_path / "Duplicate.m"
    path.write_text(
        "@interface Owner { Worker *_worker; }\n@end\n"
        "@implementation Owner\n- (void)run { [_worker reset]; }\n@end\n"
        "@implementation Worker\n- (void)reset {}\n@end\n"
        "@implementation Worker\n- (void)reset {}\n@end\n"
    )
    graph = parse_file(str(path), "objc", strict=True)
    call = next(e for e in graph.edges if dict(e.attributes).get("objc_selector") == "reset")

    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY


def test_duplicate_method_definitions_do_not_supply_a_unique_target(tmp_path: Path) -> None:
    path = tmp_path / "DuplicateMethod.m"
    path.write_text(
        "@interface Owner { Worker *_worker; }\n@end\n"
        "@implementation Owner\n- (void)run { [_worker reset]; }\n@end\n"
        "@implementation Worker\n- (void)reset {}\n- (void)reset {}\n@end\n"
    )
    graph = parse_file(str(path), "objc", strict=True)
    call = next(e for e in graph.edges if dict(e.attributes).get("objc_selector") == "reset")

    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY


def test_directory_conflict_retracts_an_earlier_file_local_type_binding(tmp_path: Path) -> None:
    (tmp_path / "Local.m").write_text(
        "@interface Owner { Worker *_worker; }\n@end\n"
        "@implementation Worker\n- (void)reset {}\n@end\n"
        "@implementation Owner\n- (void)run { [_worker reset]; }\n@end\n"
    )
    (tmp_path / "Other.m").write_text("@implementation Worker\n- (void)reset {}\n@end\n")
    graph = parse_directory(str(tmp_path), "objc", strict=True)
    call = next(e for e in graph.edges if dict(e.attributes).get("objc_selector") == "reset")

    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY


def test_loop_and_catch_bindings_do_not_shadow_outside_their_own_bodies(tmp_path: Path) -> None:
    path = tmp_path / "Scopes.m"
    path.write_text(
        "@interface Worker\n- (void)reset;\n@end\n"
        "@interface Owner { Worker *_worker; }\n@end\n"
        "@implementation Owner\n- (void)run:(id)items { "
        "for (id _worker in items) {} @try {} @catch (id _worker) {} [_worker reset]; }\n@end\n"
    )
    graph = parse_file(str(path), "objc", strict=True)
    call = next(e for e in graph.edges if dict(e.attributes).get("objc_selector") == "reset")

    assert call.target_id == "Scopes:Worker.reset"


@pytest.mark.parametrize("category", ["(Extra)", "()"])
def test_category_properties_do_not_invent_default_backing_ivars(
    tmp_path: Path, category: str
) -> None:
    path = tmp_path / "Category.m"
    path.write_text(
        "@interface Worker\n- (void)reset;\n@end\n"
        "@interface Owner\n@end\n"
        f"@interface Owner {category}\n@property Worker *worker;\n@end\n"
        "@implementation Owner\n- (void)run { [_worker reset]; }\n@end\n"
    )
    graph = parse_file(str(path), "objc", strict=True)
    call = next(e for e in graph.edges if dict(e.attributes).get("objc_selector") == "reset")

    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY
