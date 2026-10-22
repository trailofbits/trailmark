"""Bound recursive declaration extraction and report skipped nested syntax."""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from functools import wraps
from typing import Concatenate

from tree_sitter import Node

from trailmark.models.diagnostics import ParseDiagnostic
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import SourceLocation

_DEPTH: ContextVar[int] = ContextVar("_DECLARATION_DEPTH", default=0)


def bounded_nesting[**P](
    function: Callable[Concatenate[Node, str, str, CodeGraph, P], None],
) -> Callable[Concatenate[Node, str, str, CodeGraph, P], None]:
    """Keep deeply nested containers from exhausting Python's call stack."""

    @wraps(function)
    def extract(
        node: Node, path: str, module: str, graph: CodeGraph, /, *args: P.args, **kwargs: P.kwargs
    ) -> None:
        depth = _DEPTH.get()
        if depth >= 48:
            graph.parse_diagnostics.append(
                ParseDiagnostic(
                    graph.language,
                    graph.language,
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
            )
            return
        token = _DEPTH.set(depth + 1)
        try:
            function(node, path, module, graph, *args, **kwargs)
        finally:
            _DEPTH.reset(token)

    return extract
