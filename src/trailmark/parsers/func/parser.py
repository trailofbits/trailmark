"""Func language parser using tree-sitter."""

from __future__ import annotations

from pathlib import Path

from tree_sitter import Node, Parser
from tree_sitter_language_pack import get_language

from trailmark.models.edges import CodeEdge, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit, NodeKind, TypeRef
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
    collect_branches_and_calls,
    extract_parameters,
    first_child_type,
    named_children,
)

_EXTENSIONS = (".fc", ".func")
_BRANCH_TYPES = frozenset({"if_statement"})
_CALL_TYPES = frozenset({"function_application", "method_call"})


class FuncParser:
    """Parses Func source files into CodeGraph using tree-sitter."""

    @property
    def language(self) -> str:
        return "func"

    def __init__(self) -> None:
        self._parser = Parser(get_language("func"))

    def parse_file(self, file_path: str) -> CodeGraph:
        source = Path(file_path).read_bytes()
        tree = self._parser.parse(source)
        graph = CodeGraph(language="func", root_path=file_path)
        module_id = module_id_from_path(file_path)
        _visit_root(tree.root_node, file_path, module_id, graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        return parse_directory(self.parse_file, "func", dir_path, _EXTENSIONS)


def _visit_root(root: Node, file_path: str, module_id: str, graph: CodeGraph) -> None:
    add_module_node(root, file_path, module_id, graph)
    for child in named_children(root):
        if child.type == "include_directive":
            _extract_import(child, graph, module_id)
        elif child.type == "function_definition":
            _extract_function(child, file_path, module_id, graph)


def _extract_function(node: Node, file_path: str, module_id: str, graph: CodeGraph) -> None:
    name_node = first_child_type(node, frozenset({"function_name"}))
    if name_node is None:
        return
    name = node_text(name_node)
    func_id = f"{module_id}:{name}"
    body = first_child_type(node, frozenset({"block_statement", "asm_function_body"}))
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
        return_type=_return_type(node),
        cyclomatic_complexity=compute_complexity(branches),
        branches=tuple(branches),
    )
    add_contains_edge(graph, module_id, func_id)
    add_call_edges(graph, calls, func_id, module_id, file_path)


def _return_type(node: Node) -> TypeRef | None:
    for child in named_children(node):
        if child.type in {"unit_type", "primitive_type", "function_type", "type_identifier"}:
            return TypeRef(name=node_text(child))
        if child.type == "function_name":
            return None
    return None


def _extract_import(node: Node, graph: CodeGraph, module_id: str) -> None:
    target = node_text(node).removeprefix("#include").strip().strip(";").strip('"<>')
    if target:
        graph.dependencies.append(target)
        graph.edges.append(CodeEdge(source_id=module_id, target_id=target, kind=EdgeKind.IMPORTS))
