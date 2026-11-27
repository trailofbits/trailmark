"""Finalize owned source evidence before a frontend releases its syntax tree."""

from __future__ import annotations

from tree_sitter import Node

from trailmark.models.graph import CodeGraph
from trailmark.parsers._call_sites import collect_call_sites
from trailmark.parsers._exceptions import collect_exception_events


def collect_source_evidence(
    root: Node, file_path: str, graph: CodeGraph, *, source: bytes | None = None
) -> None:
    collect_exception_events(root, file_path, graph)
    collect_call_sites(root, file_path, graph, source=source)
