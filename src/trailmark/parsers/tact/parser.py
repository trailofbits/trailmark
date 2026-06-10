"""Tact language parser using tree-sitter."""

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

_EXTENSIONS = (".tact",)
_BRANCH_TYPES = frozenset({"if_statement"})
_CALL_TYPES = frozenset({"method_call_expression", "static_call_expression"})
_FUNCTION_TYPES = frozenset(
    {
        "global_function",
        "storage_function",
        "native_function",
        "asm_function",
        "init_function",
        "receive_function",
        "bounced_function",
        "external_function",
    }
)


class TactParser:
    """Parses Tact source files into CodeGraph using tree-sitter."""

    @property
    def language(self) -> str:
        return "tact"

    def __init__(self) -> None:
        self._parser = Parser(get_language("tact"))

    def parse_file(self, file_path: str) -> CodeGraph:
        source = Path(file_path).read_bytes()
        tree = self._parser.parse(source)
        graph = CodeGraph(language="tact", root_path=file_path)
        module_id = module_id_from_path(file_path)
        _visit_root(tree.root_node, file_path, module_id, graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        return parse_directory(self.parse_file, "tact", dir_path, _EXTENSIONS)


def _visit_root(root: Node, file_path: str, module_id: str, graph: CodeGraph) -> None:
    add_module_node(root, file_path, module_id, graph)
    for child in named_children(root):
        if child.type == "import":
            _extract_import(child, graph, module_id)
        elif child.type == "struct":
            _extract_struct(child, file_path, module_id, module_id, graph)
        elif child.type == "contract":
            _extract_contract(child, file_path, module_id, graph)
        elif child.type in _FUNCTION_TYPES:
            _extract_function(child, file_path, module_id, module_id, graph)


def _extract_contract(node: Node, file_path: str, module_id: str, graph: CodeGraph) -> None:
    name = child_text(node, frozenset({"identifier"}))
    if not name:
        return
    contract_id = f"{module_id}:{name}"
    graph.nodes[contract_id] = CodeUnit(
        id=contract_id,
        name=name,
        kind=NodeKind.CONTRACT,
        location=make_location(node, file_path),
    )
    add_contains_edge(graph, module_id, contract_id)
    body = first_child_type(node, frozenset({"contract_body"}))
    if body is None:
        return
    for child in named_children(body):
        if child.type == "struct":
            _extract_struct(child, file_path, module_id, contract_id, graph)
        elif child.type in _FUNCTION_TYPES:
            _extract_function(child, file_path, module_id, contract_id, graph)


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


def _extract_function(
    node: Node,
    file_path: str,
    module_id: str,
    container_id: str,
    graph: CodeGraph,
) -> None:
    base_name = _function_name(node)
    func_id, name = _unique_function_id(base_name, module_id, container_id, graph)
    body = first_child_type(node, frozenset({"function_body", "asm_function_body"}))
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
        attributes=(("tact_role", base_name),),
    )
    add_contains_edge(graph, container_id, func_id)
    add_call_edges(graph, calls, func_id, module_id, file_path, container_id)


def _function_name(node: Node) -> str:
    if node.type == "init_function":
        return "init"
    if node.type == "receive_function":
        return "receive"
    if node.type == "bounced_function":
        return "bounced"
    if node.type == "external_function":
        return "external"
    return child_text(node, frozenset({"identifier", "func_identifier"})) or node.type


def _unique_function_id(
    base_name: str,
    module_id: str,
    container_id: str,
    graph: CodeGraph,
) -> tuple[str, str]:
    prefix = f"{container_id}." if container_id != module_id else f"{module_id}:"
    candidate = f"{prefix}{base_name}"
    if candidate not in graph.nodes:
        return candidate, base_name

    index = 2
    while True:
        name = f"{base_name}_{index}"
        candidate = f"{prefix}{name}"
        if candidate not in graph.nodes:
            return candidate, name
        index += 1


def _extract_import(node: Node, graph: CodeGraph, module_id: str) -> None:
    target = node_text(node).removeprefix("import").strip().strip(";").strip("\"'")
    if target:
        graph.dependencies.append(target)
        graph.edges.append(CodeEdge(source_id=module_id, target_id=target, kind=EdgeKind.IMPORTS))
