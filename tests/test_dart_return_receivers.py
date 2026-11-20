"""Bounded final-local receiver inference uses declared factory return types."""

from pathlib import Path

import pytest

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.parse import parse_directory
from trailmark.query.api import QueryEngine


def _calls(tmp_path: Path, source: str) -> tuple[QueryEngine, list[CodeEdge]]:
    (tmp_path / "app.dart").write_text(source)
    graph = parse_directory(str(tmp_path), "dart")
    return QueryEngine.from_graph(graph), [
        e for e in graph.edges if e.kind == EdgeKind.CALLS and e.source_id == "app:Context.run"
    ]


def test_final_local_uses_same_file_declared_factory_return_type(tmp_path: Path) -> None:
    engine, calls = _calls(
        tmp_path,
        "class Value { void work() {} }\n"
        "class Context { Value make() => Value();\n"
        " void run() { final value = make(); value.work(); } }\n",
    )
    work = [e for e in calls if e.target_id == "app:Value.work"]
    assert len(work) == 1 and work[0].confidence == EdgeConfidence.INFERRED
    assert work[0].location is not None and work[0].location.start_line == 3
    assert {n["id"] for n in engine.callers_of("app:Value.work")} == {"app:Context.run"}


def test_final_local_uses_direct_relative_import(tmp_path: Path) -> None:
    (tmp_path / "value.dart").write_text("class Value { void work() {} }\n")
    _, calls = _calls(
        tmp_path,
        "import 'value.dart';\nclass Context { Value make() => throw 0;\n"
        " void run() { final value = make(); value.work(); } }\n",
    )
    assert any(e.target_id == "value:Value.work" for e in calls)


@pytest.mark.parametrize("declaration", ["var value = make()", "final value = unknown()"])
def test_mutable_or_unknown_factory_stays_unresolved(tmp_path: Path, declaration: str) -> None:
    _, calls = _calls(
        tmp_path,
        "class Value { void work() {} }\nclass Context { Value make() => Value();\n"
        f" void run() {{ {declaration}; value.work(); }} }}\n",
    )
    assert not any(e.target_id == "app:Value.work" for e in calls)


def test_nested_and_before_declaration_sites_stay_unresolved(tmp_path: Path) -> None:
    _, calls = _calls(
        tmp_path,
        "class Value { void work() {} }\nclass Context { Value make() => Value();\n"
        " void run() { value.work(); final value = make(); { value.work(); } } }\n",
    )
    assert not any(e.target_id == "app:Value.work" for e in calls)


@pytest.mark.parametrize(
    "barrier", ["show Value", "as prefix", "if (dart.library.io) 'other.dart'"]
)
def test_import_visibility_barriers_stay_unresolved(tmp_path: Path, barrier: str) -> None:
    (tmp_path / "value.dart").write_text("class Value { void work() {} }\n")
    (tmp_path / "other.dart").write_text("class Other {}\n")
    _, calls = _calls(
        tmp_path,
        f"import 'value.dart' {barrier};\nclass Context {{ Value make() => throw 0;\n"
        " void run() { final value = make(); value.work(); } }\n",
    )
    assert not any(e.target_id == "value:Value.work" for e in calls)


def test_generic_return_type_shadow_stays_unresolved(tmp_path: Path) -> None:
    _, calls = _calls(
        tmp_path,
        "class Value { void work() {} }\nclass Context<Value> { Value make() => throw 0;\n"
        " void run() { final value = make(); value.work(); } }\n",
    )
    assert not any(e.target_id == "app:Value.work" for e in calls)


def test_callable_getter_result_does_not_use_getter_return_as_call_return(tmp_path: Path) -> None:
    _, calls = _calls(
        tmp_path,
        "class Other { void work() {} }\n"
        "class Value { Other call() => Other(); void work() {} }\n"
        "class Context { Value get make => Value();\n"
        " void run() { final value = make(); value.work(); } }\n",
    )
    assert not any(e.target_id == "app:Value.work" for e in calls)


def test_shadowed_factory_stays_unresolved(tmp_path: Path) -> None:
    _, calls = _calls(
        tmp_path,
        "class Value { void work() {} }\nclass Context { Value make() => Value();\n"
        " void run(dynamic make) { final value = make(); value.work(); } }\n",
    )
    assert not any(e.target_id == "app:Value.work" for e in calls)


def test_reexport_and_missing_import_stay_unresolved(tmp_path: Path) -> None:
    (tmp_path / "value.dart").write_text("export 'missing.dart';\nclass Value { void work() {} }\n")
    _, calls = _calls(
        tmp_path,
        "import 'value.dart';\nclass Context { Value make() => throw 0;\n"
        " void run() { final value = make(); value.work(); } }\n",
    )
    assert not any(e.target_id == "value:Value.work" for e in calls)


@pytest.mark.parametrize("return_type", ["Value?", "List<Value>"])
def test_non_simple_return_types_stay_unresolved(tmp_path: Path, return_type: str) -> None:
    _, calls = _calls(
        tmp_path,
        "class Value { void work() {} }\n"
        f"class Context {{ {return_type} make() => throw 0;\n"
        " void run() { final value = make(); value.work(); } }\n",
    )
    assert not any(e.target_id == "app:Value.work" for e in calls)


def test_typedef_return_does_not_resolve_an_unrelated_imported_class(tmp_path: Path) -> None:
    (tmp_path / "value.dart").write_text("class Value { void work() {} }\n")
    _, calls = _calls(
        tmp_path,
        "import 'value.dart';\ntypedef Value = Other;\nclass Other {}\n"
        "class Context { Value make() => throw 0;\n"
        " void run() { final value = make(); value.work(); } }\n",
    )
    assert not any(e.target_id == "value:Value.work" for e in calls)


def test_uninspected_import_keeps_type_scope_incomplete(tmp_path: Path) -> None:
    (tmp_path / "value.dart").write_text("class Value { void work() {} }\n")
    _, calls = _calls(
        tmp_path,
        "import 'value.dart';\nimport 'missing.dart';\nclass Context { Value make() => throw 0;\n"
        " void run() { final value = make(); value.work(); } }\n",
    )
    assert not any(e.target_id == "value:Value.work" for e in calls)


def test_legacy_function_typedef_shadows_imported_class(tmp_path: Path) -> None:
    (tmp_path / "value.dart").write_text("class Value { void work() {} }\n")
    _, calls = _calls(
        tmp_path,
        "import 'value.dart';\ntypedef int Value();\n"
        "extension Work on Function { void work() {} }\n"
        "class Context { Value make() => () => 1;\n"
        " void run() { final value = make(); value.work(); } }\n",
    )
    assert not any(e.target_id == "value:Value.work" for e in calls)
