"""Deterministic Python work counts for parser scaling regressions."""

import sys
from collections.abc import Callable
from pathlib import Path
from types import FrameType

from trailmark.models import CodeGraph
from trailmark.parse import parse_file

type _Trace = Callable[[FrameType, str, object], _Trace | None]


def parse_work(path: Path) -> tuple[CodeGraph, int]:
    """Count parser line events, including code instrumented in temporary files.

    This measures Python traversal work; native grammar execution is excluded.
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
