"""Bound recursive declaration extraction and report skipped nested syntax."""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from functools import wraps
from inspect import signature
from typing import cast

from tree_sitter import Node

from trailmark.models.diagnostics import ParseDiagnostic
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import SourceLocation

_DEPTH: ContextVar[int] = ContextVar("_DECLARATION_DEPTH", default=0)


def bounded_nesting[**P, T](function: Callable[P, T]) -> Callable[P, T]:
    """Limit recursive extractors, preserving their individual call signatures."""
    parameters = list(signature(function).parameters)
    graph_position = parameters.index("graph")
    path_position = parameters.index("file_path")

    @wraps(function)
    def extract(*args: P.args, **kwargs: P.kwargs) -> T:
        depth = _DEPTH.get()
        if depth >= 48:
            node = cast(Node, args[0])
            path = str(kwargs["file_path"] if "file_path" in kwargs else args[path_position])
            graph = cast(CodeGraph, kwargs["graph"] if "graph" in kwargs else args[graph_position])
            graph.parse_diagnostics.append(nesting_diagnostic(node, path, graph.language))
            return cast(T, None)
        token = _DEPTH.set(depth + 1)
        try:
            return function(*args, **kwargs)
        finally:
            _DEPTH.reset(token)

    return extract


def nesting_diagnostic(node: Node, path: str, language: str) -> ParseDiagnostic:
    """Locate the declaration or block omitted by the extraction depth limit."""
    return ParseDiagnostic(
        language,
        language,
        SourceLocation(
            path,
            node.start_point.row + 1,
            node.end_point.row + 1,
            node.start_point.column,
            node.end_point.column,
        ),
        "unsupported_syntax",
        "Declaration nesting exceeds extraction limit (48)",
    )
