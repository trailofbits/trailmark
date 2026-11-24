"""Deterministic Python work counts for parser and analysis regressions."""

import sys
from collections.abc import Callable
from pathlib import Path
from types import FrameType

from trailmark.models import CodeGraph
from trailmark.parse import parse_file

type _Trace = Callable[[FrameType, str, object], _Trace | None]


def count_work[T](operation: Callable[[], T], *, max_work: int | None = None) -> tuple[T, int]:
    """Count application line events, including code instrumented in temporary files.

    This measures Python traversal work; native grammar execution is excluded.
    An optional work budget also bounds nonterminating Python traversals.
    """
    work = 0

    def count(frame: FrameType, event: str, argument: object) -> _Trace:
        nonlocal work
        if event == "line":
            work += 1
            if max_work is not None and work > max_work:
                raise AssertionError(f"Application exceeded its {max_work} line-event work budget")
        return count

    def select(frame: FrameType, event: str, argument: object) -> _Trace | None:
        return count if frame.f_globals.get("__name__", "").startswith("trailmark.") else None

    previous = sys.gettrace()
    try:
        sys.settrace(select)
        result = operation()
    finally:
        sys.settrace(previous)
    return result, work


def parse_work(
    path: Path, *, language: str | None = None, max_work: int | None = None
) -> tuple[CodeGraph, int]:
    """Count parser work and optionally bound nonterminating Python traversals."""
    return count_work(lambda: parse_file(str(path), language), max_work=max_work)
