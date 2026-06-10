"""Move language parser using tree-sitter."""

from __future__ import annotations

from pathlib import Path

from tree_sitter import Node, Parser
from tree_sitter_language_pack import get_language

from trailmark.models.edges import CodeEdge, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit, NodeKind
from trailmark.parsers._common import (
    add_contains_edge,
    add_module_node,
    compute_complexity,
    make_location,
    module_id_from_path,
    node_text,
    parse_directory,
)
from trailmark.parsers._simple import (
    add_call_edges,
    child_text,
    collect_branches_and_calls,
    extract_parameters,
    first_child_type,
    named_children,
    return_type_after_params,
)

_EXTENSIONS = (".move",)
_BRANCH_TYPES = frozenset({"if_expression"})
_CALL_TYPES = frozenset({"call_expression"})


class MoveParser:
    """Parses Move source files into CodeGraph using tree-sitter."""

    @property
    def language(self) -> str:
        return "move"

    def __init__(self) -> None:
        self._parser = Parser(get_language("move"))

    def parse_file(self, file_path: str) -> CodeGraph:
        source = Path(file_path).read_bytes()
        tree = self._parser.parse(source)
        graph = CodeGraph(language="move", root_path=file_path)
        module_id = module_id_from_path(file_path)
        _visit_module(tree.root_node, file_path, module_id, graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        return parse_directory(self.parse_file, "move", dir_path, _EXTENSIONS)


def _visit_module(root: Node, file_path: str, file_module_id: str, graph: CodeGraph) -> None:
    add_module_node(root, file_path, file_module_id, graph)
    move_module_id = _move_module_id(root, file_module_id)
    if move_module_id != file_module_id:
        graph.nodes[move_module_id] = CodeUnit(
            id=move_module_id,
            name=move_module_id.rsplit(":", 1)[-1],
            kind=NodeKind.MODULE,
            location=make_location(root, file_path),
        )
        add_contains_edge(graph, file_module_id, move_module_id)

    body = first_child_type(root, frozenset({"module_body"})) or root
    for child in named_children(body):
        if child.type == "function_item":
            _extract_function(child, file_path, move_module_id, graph)
        elif child.type == "use_declaration":
            _extract_import(child, graph, move_module_id)
        elif child.type in {"struct_item", "struct"}:
            _extract_struct(child, file_path, move_module_id, graph)


def _move_module_id(root: Node, fallback: str) -> str:
    parts = [
        node_text(child)
        for child in named_children(root)
        if child.type in {"hex_address", "identifier"}
    ]
    if not parts:
        return fallback
    return f"{fallback}:{'::'.join(parts)}"


def _extract_function(node: Node, file_path: str, module_id: str, graph: CodeGraph) -> None:
    name = child_text(node, frozenset({"identifier"}))
    if not name:
        return
    func_id = f"{module_id}:{name}"
    body = first_child_type(node, frozenset({"block"}))
    branches, calls = collect_branches_and_calls(
        body,
        file_path,
        branch_types=_BRANCH_TYPES,
        call_types=_CALL_TYPES,
    )
    graph.nodes[func_id] = CodeUnit(
        id=func_id,
        name=name,
        kind=NodeKind.FUNCTION,
        location=make_location(node, file_path),
        parameters=extract_parameters(node),
        return_type=return_type_after_params(node),
        cyclomatic_complexity=compute_complexity(branches),
        branches=tuple(branches),
    )
    add_contains_edge(graph, module_id, func_id)
    add_call_edges(graph, calls, func_id, module_id, file_path)


def _extract_struct(node: Node, file_path: str, module_id: str, graph: CodeGraph) -> None:
    name = child_text(node, frozenset({"identifier"}))
    if not name:
        return
    struct_id = f"{module_id}:{name}"
    graph.nodes[struct_id] = CodeUnit(
        id=struct_id,
        name=name,
        kind=NodeKind.STRUCT,
        location=make_location(node, file_path),
    )
    add_contains_edge(graph, module_id, struct_id)


def _extract_import(node: Node, graph: CodeGraph, module_id: str) -> None:
    target = node_text(node).removeprefix("use").strip().rstrip(";")
    if target:
        graph.dependencies.append(target)
        graph.edges.append(CodeEdge(source_id=module_id, target_id=target, kind=EdgeKind.IMPORTS))
