"""Sway language parser using tree-sitter."""

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

_EXTENSIONS = (".sw",)
_BRANCH_TYPES = frozenset({"if_expression", "match_expression", "match_branch"})
_CALL_TYPES = frozenset({"call_expression", "abi_call_expression"})


class SwayParser:
    """Parses Sway source files into CodeGraph using tree-sitter."""

    @property
    def language(self) -> str:
        return "sway"

    def __init__(self) -> None:
        self._parser = Parser(get_language("sway"))

    def parse_file(self, file_path: str) -> CodeGraph:
        source = Path(file_path).read_bytes()
        tree = self._parser.parse(source)
        graph = CodeGraph(language="sway", root_path=file_path)
        module_id = module_id_from_path(file_path)
        _visit_root(tree.root_node, file_path, module_id, graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        return parse_directory(self.parse_file, "sway", dir_path, _EXTENSIONS)


def _visit_root(root: Node, file_path: str, module_id: str, graph: CodeGraph) -> None:
    add_module_node(root, file_path, module_id, graph)
    for child in named_children(root):
        if child.type in {"use_statement", "use_item"}:
            _extract_import(child, graph, module_id)
        elif child.type == "abi_item":
            _extract_abi(child, file_path, module_id, graph)
        elif child.type == "struct_item":
            _extract_struct(child, file_path, module_id, module_id, graph)
        elif child.type == "impl_item":
            _extract_impl(child, file_path, module_id, graph)
        elif child.type == "function_item":
            _extract_function(child, file_path, module_id, module_id, graph)


def _extract_abi(node: Node, file_path: str, module_id: str, graph: CodeGraph) -> None:
    name = child_text(node, frozenset({"type_identifier", "identifier"}))
    if not name:
        return
    abi_id = f"{module_id}:{name}"
    graph.nodes[abi_id] = CodeUnit(
        id=abi_id,
        name=name,
        kind=NodeKind.INTERFACE,
        location=make_location(node, file_path),
    )
    add_contains_edge(graph, module_id, abi_id)
    body = first_child_type(node, frozenset({"declaration_list"}))
    if body is None:
        return
    for child in named_children(body):
        if child.type == "function_signature_item":
            _extract_signature(child, file_path, module_id, abi_id, graph)


def _extract_impl(node: Node, file_path: str, module_id: str, graph: CodeGraph) -> None:
    names = [node_text(child) for child in named_children(node) if child.type == "type_identifier"]
    container_id = f"{module_id}:{names[0]}" if names else module_id
    body = first_child_type(node, frozenset({"declaration_list"}))
    if body is None:
        return
    for child in named_children(body):
        if child.type == "function_item":
            _extract_function(child, file_path, module_id, container_id, graph)


def _extract_struct(
    node: Node,
    file_path: str,
    module_id: str,
    container_id: str,
    graph: CodeGraph,
) -> None:
    name = child_text(node, frozenset({"type_identifier", "identifier"}))
    if not name:
        return
    struct_id = f"{module_id}:{name}"
    graph.nodes[struct_id] = CodeUnit(
        id=struct_id,
        name=name,
        kind=NodeKind.STRUCT,
        location=make_location(node, file_path),
    )
    add_contains_edge(graph, container_id, struct_id)


def _extract_signature(
    node: Node,
    file_path: str,
    module_id: str,
    container_id: str,
    graph: CodeGraph,
) -> None:
    name = child_text(node, frozenset({"identifier"}))
    if not name:
        return
    method_id = f"{container_id}.{name}"
    graph.nodes[method_id] = CodeUnit(
        id=method_id,
        name=name,
        kind=NodeKind.METHOD,
        location=make_location(node, file_path),
        parameters=extract_parameters(node),
        return_type=return_type_after_params(node),
    )
    add_contains_edge(graph, container_id, method_id)


def _extract_function(
    node: Node,
    file_path: str,
    module_id: str,
    container_id: str,
    graph: CodeGraph,
) -> None:
    name = child_text(node, frozenset({"identifier"}))
    if not name:
        return
    func_id = f"{container_id}.{name}" if container_id != module_id else f"{module_id}:{name}"
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
        kind=NodeKind.METHOD if container_id != module_id else NodeKind.FUNCTION,
        location=make_location(node, file_path),
        parameters=extract_parameters(node),
        return_type=return_type_after_params(node),
        cyclomatic_complexity=compute_complexity(branches),
        branches=tuple(branches),
    )
    add_contains_edge(graph, container_id, func_id)
    for call in calls:
        call_container = container_id if node_text(call[1]).strip().startswith("self.") else None
        add_call_edges(graph, [call], func_id, module_id, file_path, call_container)


def _extract_import(node: Node, graph: CodeGraph, module_id: str) -> None:
    target = node_text(node).strip().strip(";")
    if target:
        graph.dependencies.append(target)
        graph.edges.append(CodeEdge(source_id=module_id, target_id=target, kind=EdgeKind.IMPORTS))
