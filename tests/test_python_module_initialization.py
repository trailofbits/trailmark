from pathlib import Path

from trailmark.models.edges import EdgeKind
from trailmark.parse import parse_directory


def test_module_calls_include_decorators_and_exclude_function_bodies(tmp_path: Path) -> None:
    (tmp_path / "startup.py").write_text(
        "def init(): pass\n"
        "def hook(f): return f\n"
        "def later(): pass\n"
        "@hook\n"
        "@hook()\n"
        "def handler():\n"
        "    later()\n"
        "app = init()\n"
    )

    graph = parse_directory(str(tmp_path))
    module_calls = {
        edge.target_id
        for edge in graph.edges
        if edge.kind == EdgeKind.CALLS and edge.source_id == "startup"
    }
    assert module_calls == {"startup:init", "startup:hook"}


def test_defaults_and_class_bodies_execute_in_the_enclosing_scope(tmp_path: Path) -> None:
    (tmp_path / "startup.py").write_text(
        "def eager(): pass\n"
        "def deferred(): pass\n"
        "def action(value=eager(), *, keyword=eager()): deferred()\n"
        "class Holder:\n"
        "    token = eager()\n"
        "    def method(self, value=eager()): deferred()\n"
        "def outer():\n"
        "    def inner(value=eager()): deferred()\n"
        "    class Nested:\n"
        "        token = eager()\n"
        "value = lambda argument=eager(): deferred()\n"
    )
    graph = parse_directory(str(tmp_path))
    eager = [e for e in graph.edges if e.kind == EdgeKind.CALLS and e.target_id == "startup:eager"]
    assert [(e.source_id, e.location.start_line if e.location else None) for e in eager] == [
        ("startup:outer", 8),
        ("startup:outer", 10),
        ("startup", 3),
        ("startup", 3),
        ("startup", 5),
        ("startup", 6),
        ("startup", 11),
    ]
    assert all(dict(e.attributes)["python_execution"] == "definition_time" for e in eager)
    assert not any(
        e.kind == EdgeKind.CALLS and e.source_id == "startup" and e.target_id == "startup:deferred"
        for e in graph.edges
    )
    assert not graph.parse_diagnostics


def test_annotation_calls_have_a_coverage_diagnostic_without_assuming_a_python_version(
    tmp_path: Path,
) -> None:
    (tmp_path / "annotations.py").write_text("def helper(): pass\ndef action(x: helper()): pass\n")
    graph = parse_directory(str(tmp_path))
    assert not any(e.kind == EdgeKind.CALLS for e in graph.edges)
    assert [(d.kind, d.location.start_line) for d in graph.parse_diagnostics] == [
        ("unsupported_syntax", 2)
    ]


def test_explicitly_postponed_annotations_never_become_calls(tmp_path: Path) -> None:
    (tmp_path / "annotations.py").write_text(
        "from __future__ import annotations\ndef helper(): pass\ndef action(x: helper()): pass\n"
    )
    graph = parse_directory(str(tmp_path))
    assert not any(e.kind == EdgeKind.CALLS for e in graph.edges)
    assert not graph.parse_diagnostics


def test_default_names_bind_before_the_new_parameters_exist(tmp_path: Path) -> None:
    (tmp_path / "defaults.py").write_text("def helper(): pass\ndef action(helper=helper()): pass\n")
    graph = parse_directory(str(tmp_path))
    assert [(e.source_id, e.target_id) for e in graph.edges if e.kind == EdgeKind.CALLS] == [
        ("defaults", "defaults:helper")
    ]
