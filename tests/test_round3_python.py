"""Python runtime definitions retain their bodies and execution order."""

from pathlib import Path
from time import perf_counter

import pytest

from trailmark.models import EdgeKind, NodeKind
from trailmark.parse import parse_file


def test_redefined_bodies_keep_their_own_nested_calls(tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    path.write_text(
        "def f():\n"
        " def inner(): return a()\n"
        " return inner()\n"
        "def f():\n"
        " def inner(): return b()\n"
        " return inner()\n"
        ""
    )
    graph = parse_file(str(path))
    calls = {
        e.source_id: e.target_id
        for e in graph.edges
        if e.kind == EdgeKind.CALLS and e.location and e.location.start_line in {3, 6}
    }
    assert calls == {"a:f@1_0": "a:f@1_0.inner", "a:f": "a:f.inner"}


def test_calls_between_definitions_use_the_preceding_definition(tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    path.write_text(
        "def runner():\n"
        " def step(): return one()\n"
        " a = step()\n"
        " def step(): return two()\n"
        " return step()\n"
        ""
    )
    graph = parse_file(str(path))
    targets = {
        e.location.start_line: e.target_id
        for e in graph.edges
        if e.kind == EdgeKind.CALLS
        and e.confidence.value == "certain"
        and e.source_id == "a:runner"
        and e.location
    }
    assert targets == {3: "a:runner.step@2_1", 5: "a:runner.step"}


@pytest.mark.parametrize(
    "source", ["class Token: pass\ndef Token(): pass\n", "def Token(): pass\nclass Token: pass\n"]
)
def test_type_retains_base_id(tmp_path: Path, source: str) -> None:
    path = tmp_path / "a.py"
    path.write_text(source)
    graph = parse_file(str(path))
    assert graph.nodes["a:Token"].kind == NodeKind.CLASS
    assert len([u for u in graph.nodes.values() if u.name == "Token"]) == 2


@pytest.mark.parametrize(
    "decorator", ["overload", "typing.overload", "typing_extensions.overload", "custom.overload"]
)
def test_only_known_overload_decorators_are_stubs(tmp_path: Path, decorator: str) -> None:
    path = tmp_path / "a.py"
    path.write_text(f"@{decorator}\ndef f(x): return x\n")
    graph = parse_file(str(path))
    assert ("a:f" in graph.nodes) == (decorator == "custom.overload")


@pytest.mark.parametrize("body", ["def f(): pass\n", "@show.register\ndef _(x: T): pass\n"])
def test_many_runtime_definitions_are_linear(tmp_path: Path, body: str) -> None:
    path = tmp_path / "a.py"
    path.write_text(body * 4000)
    start = perf_counter()
    graph = parse_file(str(path))
    assert perf_counter() - start < 1
    assert len([u for u in graph.nodes.values() if u.kind == NodeKind.FUNCTION]) == 4000


def test_python_call_before_local_definition_is_unbound(tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    path.write_text("def step(): pass\ndef run():\n step()\n def step(): pass\n")
    graph = parse_file(str(path))
    edge = next(
        e
        for e in graph.edges
        if e.source_id == "a:run"
        and e.kind == EdgeKind.CALLS
        and e.location
        and e.location.start_line == 3
    )
    assert dict(edge.attributes)["binding_blocked"]


def test_python_assignment_and_definition_order(tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    path.write_text("def run():\n step = None\n def step(): pass\n step()\n step = None\n step()\n")
    graph = parse_file(str(path))
    calls = {
        e.location.start_line: e
        for e in graph.edges
        if e.source_id == "a:run"
        and e.kind == EdgeKind.CALLS
        and e.location
        and e.location.start_line in {4, 6}
    }
    assert calls[4].target_id == "a:run.step"
    assert dict(calls[6].attributes)["binding_blocked"]


def test_same_named_type_does_not_capture_function_call(tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    path.write_text("class Token: pass\ndef Token(): pass\ndef run(): return Token()\n")
    graph = parse_file(str(path))
    edge = next(e for e in graph.edges if e.source_id == "a:run" and e.kind == EdgeKind.CALLS)
    assert edge.target_id == "a:Token@2_0"
    assert graph.nodes[edge.target_id].kind == NodeKind.FUNCTION


def test_blocked_proxy_uses_the_final_callable_owner(tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    path.write_text("def run(cb): return cb()\n")
    graph = parse_file(str(path))
    edge = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)
    assert edge.source_id == "a:run"
    assert dict(edge.attributes)["raw_target"] == "a:run.$binding.cb"
