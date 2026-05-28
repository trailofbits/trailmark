"""Shared utilities for tree-sitter language parsers."""

from __future__ import annotations

import dataclasses
import os
import re
from collections.abc import Callable
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING

from trailmark.analysis.proxies import ensure_proxy_nodes
from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import (
    BranchInfo,
    CodeUnit,
    NodeKind,
    SourceLocation,
    TypeParameter,
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


_DIRECTORY_PARSE_ROOT: ContextVar[Path | None] = ContextVar(
    "_DIRECTORY_PARSE_ROOT",
    default=None,
)


def module_id_from_path(file_path: str) -> str:
    """Derive a module ID from a file path.

    Single-file parsing preserves the historical filename-stem ID. Directory
    parsing uses a root-relative dotted path so same-named files in different
    directories do not overwrite each other when their graphs are merged.
    """
    p = Path(file_path)
    root = _DIRECTORY_PARSE_ROOT.get()
    if root is not None:
        try:
            rel_path = _absolute_lexical_path(p).relative_to(root)
        except ValueError:
            pass
        else:
            if rel_path.stem == "__init__":
                module_path = rel_path.parent
                if module_path == Path("."):
                    return _escape_module_path_part(root.name)
            else:
                module_path = rel_path.with_suffix("")
            return ".".join(_escape_module_path_part(part) for part in module_path.parts)

    stem = p.stem if p.stem != "__init__" else p.parent.name
    return stem


def _absolute_lexical_path(path: Path) -> Path:
    """Return an absolute path without resolving symlink targets."""
    return Path(os.path.abspath(path))


def _escape_module_path_part(part: str) -> str:
    """Escape separators used in dotted module IDs."""
    return part.replace("\\", "\\\\").replace(".", "\\.")


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


def should_skip_dir(dirname: str) -> bool:
    """Return whether a directory should be skipped during source walks."""
    return dirname in _EXCLUDED_DIRS or dirname.startswith(".")


def walk_source_files(
    dir_path: str,
    extensions: tuple[str, ...],
) -> Iterator[str]:
    """Yield absolute paths matching the given extensions under dir_path.

    Skips common non-source directories (VCS, caches, vendored deps)
    and does not follow symlinks.
    """
    for root, dirs, files in os.walk(dir_path, followlinks=False):
        dirs[:] = [d for d in dirs if not should_skip_dir(d)]
        dirs.sort()
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
    token = _DIRECTORY_PARSE_ROOT.set(_absolute_lexical_path(Path(dir_path)))
    try:
        for fpath in walk_source_files(dir_path, extensions):
            file_graph = parse_file_fn(fpath)
            merged.merge(file_graph)
    finally:
        _DIRECTORY_PARSE_ROOT.reset(token)
    _link_cross_file_calls(merged)
    return ensure_proxy_nodes(merged)


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


def extract_type_parameters(node: Node) -> tuple[TypeParameter, ...]:
    """Best-effort extraction of generic parameter declarations."""
    text = node_text(node)
    raw = _generic_parameter_block(text)
    if raw is None:
        return ()
    params = [_parse_type_parameter(part) for part in _split_top_level(raw)]
    return tuple(param for param in params if param is not None)


def _generic_parameter_block(text: str) -> str | None:
    """Return the first likely generic parameter block from a declaration."""
    template = re.search(r"\btemplate\s*<(?P<params>[^<>]*)>", text)
    if template is not None:
        return template.group("params")

    header = text.split("{", 1)[0].split("=>", 1)[0]
    leading = re.search(r"^\s*(?:[\w@[\]().,\"']+\s+)*<", header)
    if leading is not None:
        result = _delimited_block_at(header, leading.end() - 1, "<", ">")
        if result is not None:
            return result

    declaration = re.search(
        r"\b(?:class|interface|struct|enum|trait|protocol|extension|type)"
        r"\s+[A-Za-z_$][\w$]*\s*(?P<open><|\[)",
        header,
    )
    if declaration is not None:
        return _block_from_match(header, declaration)

    function = re.search(
        r"\b(?:def|fn|func|function|fun)\s+[A-Za-z_$][\w$]*\s*(?P<open><|\[)",
        header,
    )
    if function is not None:
        return _block_from_match(header, function)

    method = re.search(r"\b[A-Za-z_$][\w$]*\s*(?P<open><|\[)\s*[^\]>\n]*[\]>]\s*\(", header)
    if method is not None:
        return _block_from_match(header, method)

    arrow = re.search(r"^\s*(?P<open><|\[)", header)
    if arrow is not None:
        result = _block_from_match(header, arrow)
        if result is not None:
            return result
    return None


def _block_from_match(text: str, match: re.Match[str]) -> str | None:
    opener = match.group("open")
    closer = ">" if opener == "<" else "]"
    return _delimited_block_at(text, match.end("open") - 1, opener, closer)


def _delimited_block_at(
    text: str,
    start: int,
    opener: str,
    closer: str,
) -> str | None:
    depth = 0
    for idx in range(start, len(text)):
        char = text[idx]
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start + 1 : idx]
    return None


def _split_top_level(text: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    pairs = {"<": ">", "[": "]", "(": ")"}
    closers = set(pairs.values())
    for idx, char in enumerate(text):
        if char in pairs:
            depth += 1
        elif char in closers and depth > 0:
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:idx].strip())
            start = idx + 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _parse_type_parameter(text: str) -> TypeParameter | None:
    item = text.strip()
    if not item:
        return None

    item = re.sub(r"^(typename|class|const|in|out)\s+", "", item)
    item = re.sub(r"^\?\s*(extends|super)\s+", "", item)

    default_ref: TypeRef | None = None
    if "=" in item:
        item, default = item.split("=", 1)
        default_ref = TypeRef(name=default.strip())

    constraints: list[TypeRef] = []
    for marker in (" extends ", " super ", " implements ", ":"):
        if marker in item:
            name, raw_constraints = item.split(marker, 1)
            constraints = [
                TypeRef(name=constraint.strip())
                for constraint in re.split(r"[&|+]", raw_constraints)
                if constraint.strip()
            ]
            item = name
            break

    tokens = item.strip().split()
    if not tokens:
        return None
    name = tokens[0]
    if len(tokens) > 1 and not constraints:
        constraints = [TypeRef(name=" ".join(tokens[1:]))]
    return TypeParameter(
        name=name,
        constraints=tuple(constraints),
        default=default_ref,
    )


def first_child_by_type(node: Node, type_name: str) -> Node | None:
    """Return the first direct child of ``node`` whose type matches.

    Shared helper used by parsers that hand-walk AST children by type.
    """
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _link_cross_file_calls(graph: CodeGraph) -> None:
    """Rewrite dangling call edges to point at definitions in other modules.

    Per-file parsers resolve bare calls like ``foo()`` as
    ``current_module:foo``.  After merging, if ``foo`` is defined in
    another module, that edge target doesn't exist.  This pass builds a
    name index and rewrites those edges to the actual definition site.
    """
    defined_nodes = graph.nodes

    name_to_ids: dict[str, list[str]] = {}
    for node_id, unit in defined_nodes.items():
        if unit.kind.value in {"function", "method"}:
            name_to_ids.setdefault(unit.name, []).append(node_id)

    new_edges: list[CodeEdge] = []
    for edge in graph.edges:
        if edge.kind != EdgeKind.CALLS or edge.target_id in defined_nodes:
            new_edges.append(edge)
            continue

        target = edge.target_id
        if "::" in target or "->" in target:
            new_edges.append(edge)
            continue

        bare_name = target.rsplit(":", 1)[-1] if ":" in target else target
        if "." in bare_name or "->" in bare_name:
            new_edges.append(edge)
            continue
        candidates = name_to_ids.get(bare_name)

        if not candidates:
            new_edges.append(edge)
            continue

        if len(candidates) == 1:
            new_edges.append(dataclasses.replace(edge, target_id=candidates[0]))
        else:
            # Ambiguous: prefer a single cross-file candidate when a same-file
            # declaration is also present, but do not invent an arbitrary call
            # target when multiple cross-file definitions share the same name.
            src_module = edge.source_id.rsplit(":", 1)[0] if ":" in edge.source_id else ""
            cross = [c for c in candidates if not c.startswith(src_module + ":")]
            if len(cross) == 1:
                new_edges.append(dataclasses.replace(edge, target_id=cross[0]))
            else:
                new_edges.append(
                    dataclasses.replace(
                        edge,
                        confidence=EdgeConfidence.UNCERTAIN,
                    )
                )

    graph.edges = new_edges
