"""Shared utilities for tree-sitter language parsers."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from trailmark.models.edges import CodeEdge, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import (
    BranchInfo,
    CodeUnit,
    NodeKind,
    SourceLocation,
    TypeRef,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tree_sitter import Node


def node_text(node: Node) -> str:
    """Get the UTF-8 text content of a tree-sitter node."""
    return node.text.decode("utf-8") if node.text else ""


def make_location(node: Node, file_path: str) -> SourceLocation:
    """Create a SourceLocation from a tree-sitter node."""
    return SourceLocation(
        file_path=file_path,
        start_line=node.start_point.row + 1,
        end_line=node.end_point.row + 1,
        start_col=node.start_point.column,
        end_col=node.end_point.column,
    )


def module_id_from_path(file_path: str) -> str:
    """Derive a module ID from a file path."""
    p = Path(file_path)
    stem = p.stem if p.stem != "__init__" else p.parent.name
    return stem


_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "build",
        "dist",
        ".eggs",
        "vendor",
    }
)


def walk_source_files(
    dir_path: str,
    extensions: tuple[str, ...],
) -> Iterator[str]:
    """Yield absolute paths matching the given extensions under dir_path.

    Skips common non-source directories (VCS, caches, vendored deps)
    and does not follow symlinks.
    """
    for root, dirs, files in os.walk(dir_path, followlinks=False):
        dirs[:] = [d for d in dirs if d not in _EXCLUDED_DIRS and not d.startswith(".")]
        for fname in sorted(files):
            if any(fname.endswith(ext) for ext in extensions):
                yield os.path.join(root, fname)


def parse_directory(
    parse_file_fn: Callable[[str], CodeGraph],
    language: str,
    dir_path: str,
    extensions: tuple[str, ...],
) -> CodeGraph:
    """Parse all matching files under dir_path into a merged graph.

    Args:
        parse_file_fn: A callable(file_path: str) -> CodeGraph.
        language: Language name for the merged graph.
        dir_path: Directory to walk.
        extensions: File extensions to include.
    """
    merged = CodeGraph(language=language, root_path=dir_path)
    for fpath in walk_source_files(dir_path, extensions):
        file_graph = parse_file_fn(fpath)
        merged.merge(file_graph)
    return merged


def collect_body_info(
    body: Node,
    file_path: str,
    branch_types: frozenset[str],
    call_type: str,
    throw_types: frozenset[str],
    branches: list[BranchInfo],
    exception_types: list[TypeRef],
    calls: list[tuple[str, Node]],
) -> None:
    """Collect branches, exceptions, and calls from a function body.

    Uses an explicit stack instead of recursion to avoid stack overflow
    on deeply nested ASTs from untrusted source files.
    """
    stack: list[Node] = list(reversed(body.children))
    while stack:
        node = stack.pop()
        if node.type in branch_types:
            condition = _extract_condition_text(node)
            branches.append(
                BranchInfo(
                    location=make_location(node, file_path),
                    condition=condition,
                )
            )
        if node.type in throw_types:
            _collect_throw_type(node, exception_types)
        if node.type == call_type:
            call_name = extract_call_name(node)
            if call_name:
                calls.append((call_name, node))
        stack.extend(reversed(node.children))


def _extract_condition_text(node: Node) -> str:
    """Extract the condition expression from a branch node."""
    if node.type == "boolean_operator":
        return "boolean_operator"
    condition = node.child_by_field_name("condition")
    if condition is not None:
        return node_text(condition)
    return node.type


def _collect_throw_type(
    node: Node,
    exception_types: list[TypeRef],
) -> None:
    """Extract the exception type from a raise/throw statement."""
    for child in node.children:
        if child.type in ("call", "call_expression"):
            func = child.child_by_field_name("function")
            if func is not None:
                name = node_text(func)
                exception_types.append(TypeRef(name=name))
            return
        if child.type == "new_expression":
            ctor = child.child_by_field_name("constructor")
            if ctor is not None:
                exception_types.append(TypeRef(name=node_text(ctor)))
            return
        if child.type == "identifier":
            exception_types.append(TypeRef(name=node_text(child)))
            return


_CALL_NAME_TYPES = frozenset(
    {
        "identifier",
        "attribute",
        "field_expression",
        "member_expression",
        "scoped_identifier",
        "selector_expression",
        "simple_identifier",  # Swift
        "navigation_expression",  # Swift dot access (e.g. self.method)
    }
)


def extract_call_name(node: Node) -> str:
    """Extract the function/method name from a call node."""
    func = node.child_by_field_name("function")
    if func is None:
        # Fallback for grammars that don't label the callable (e.g. Swift):
        # the callable is conventionally the first named child.
        for child in node.children:
            if child.type in _CALL_NAME_TYPES:
                return node_text(child)
        return ""
    if func.type in _CALL_NAME_TYPES:
        return node_text(func)
    # Unwrap wrapper nodes (e.g., Solidity wraps in "expression").
    for child in func.children:
        if child.type in _CALL_NAME_TYPES:
            return node_text(child)
    return ""


def compute_complexity(branches: list[BranchInfo]) -> int:
    """Compute cyclomatic complexity from collected branches."""
    return 1 + len(branches)


def add_module_node(
    root: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Create a MODULE node and add it to the graph."""
    module_loc = make_location(root, file_path)
    module_unit = CodeUnit(
        id=module_id,
        name=module_id,
        kind=NodeKind.MODULE,
        location=module_loc,
    )
    graph.nodes[module_id] = module_unit


def add_contains_edge(
    graph: CodeGraph,
    container_id: str,
    child_id: str,
) -> None:
    """Add a CONTAINS edge from container to child."""
    graph.edges.append(
        CodeEdge(
            source_id=container_id,
            target_id=child_id,
            kind=EdgeKind.CONTAINS,
        )
    )
