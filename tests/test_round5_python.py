"""Python generic declarations, aliases, and long receiver chains stay bounded."""

import sys
from collections.abc import Callable
from pathlib import Path
from types import FrameType

import pytest

from trailmark.models import CodeGraph, EdgeKind
from trailmark.parse import parse_file
from trailmark.parsers import _common

type _Trace = Callable[[FrameType, str, object], _Trace | None]


def _parse_work(path: Path) -> tuple[CodeGraph, int]:
    """Count Python traversal work independently of CPU speed and instrumentation.

    Native grammar time is not measured. Line events detect repeated Python
    ancestor walks, including when instrumented code lives in temporary files.
    """
    work = 0

    def count(frame: FrameType, event: str, argument: object) -> _Trace:
        nonlocal work
        if event == "line":
            work += 1
        return count

    def select(frame: FrameType, event: str, argument: object) -> _Trace | None:
        return count if frame.f_globals.get("__name__", "").startswith("trailmark.") else None

    previous = sys.gettrace()
    try:
        sys.settrace(select)
        graph = parse_file(str(path))
    finally:
        sys.settrace(previous)
    return graph, work


def test_long_receiver_chain_is_linear(tmp_path: Path) -> None:
    path = tmp_path / "app.py"
    work = []
    for length in (750, 3000):
        path.write_text("def run():\n return a()" + ".b()" * length + "\n")
        graph, visits = _parse_work(path)
        assert visits > 0
        work.append(visits)
        assert len([e for e in graph.edges if e.kind == EdgeKind.CALLS]) == length + 1
    # Four times the input may use up to five times the work, but a per-call
    # walk through the receiver chain grows quadratically (roughly 16 times).
    assert work[1] <= 5 * work[0], work


def test_deep_python_declarations_use_parameter_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_body_scan(text: str) -> None:
        raise AssertionError("Python type parameters must not rescan entire declaration bodies")

    monkeypatch.setattr(_common, "_generic_parameter_block", reject_body_scan)
    path = tmp_path / "app.py"
    path.write_text("".join(" " * i + "def f():\n" for i in range(3000)) + " " * 3000 + "pass\n")
    graph = parse_file(str(path))
    assert graph.parse_diagnostics and len(graph.nodes) < 100


def test_python_generic_declaration_parameters(tmp_path: Path) -> None:
    path = tmp_path / "app.py"
    path.write_text("def f[T](x: T) -> T: return x\nclass C[T]: pass\n")
    graph = parse_file(str(path))
    assert [p.name for p in graph.nodes["app:f"].type_parameters] == ["T"]
    assert [p.name for p in graph.nodes["app:C"].type_parameters] == ["T"]


@pytest.mark.parametrize("module", ["typing", "typing_extensions"])
def test_overload_from_import_alias(tmp_path: Path, module: str) -> None:
    path = tmp_path / "app.py"
    path.write_text(
        f"from {module} import overload as ov\n"
        "@ov\ndef get(x: int) -> int: ...\n"
        "@ov\ndef get(x: str) -> str: ...\n"
        "def get(x): return x\n"
    )
    graph = parse_file(str(path))
    assert [u.id for u in graph.nodes.values() if u.name == "get"] == ["app:get"]
