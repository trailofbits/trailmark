"""Thrift parser using tree-sitter."""

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
    make_location,
    module_id_from_path,
    node_text,
    parse_directory,
)
from trailmark.parsers._simple import (
    child_text,
    extract_parameters,
    first_child_type,
    named_children,
    return_type_after_params,
)

_EXTENSIONS = (".thrift",)


class ThriftParser:
    """Parses Thrift schemas into CodeGraph."""

    @property
    def language(self) -> str:
        return "thrift"

    def __init__(self) -> None:
        self._parser = Parser(get_language("thrift"))

    def parse_file(self, file_path: str) -> CodeGraph:
        source = Path(file_path).read_bytes()
        tree = self._parser.parse(source)
        graph = CodeGraph(language="thrift", root_path=file_path)
        module_id = _namespace_id(tree.root_node) or module_id_from_path(file_path)
        _visit_root(tree.root_node, file_path, module_id, graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        return parse_directory(self.parse_file, "thrift", dir_path, _EXTENSIONS)


def _visit_root(root: Node, file_path: str, module_id: str, graph: CodeGraph) -> None:
    add_module_node(root, file_path, module_id, graph)
    for child in named_children(root):
        if child.type == "include_statement":
            _extract_import(child, graph, module_id)
        elif child.type in {"struct_definition", "exception_definition", "union_definition"}:
            _extract_struct(child, file_path, module_id, graph)
        elif child.type == "service_definition":
            _extract_service(child, file_path, module_id, graph)
        elif child.type == "enum_definition":
            _extract_enum(child, file_path, module_id, graph)


def _namespace_id(root: Node) -> str:
    for child in named_children(root):
        if child.type == "namespace_declaration":
            parts = [
                node_text(part).lstrip(".")
                for part in named_children(child)
                if part.type == "namespace"
            ]
            return ".".join(parts)
    return ""


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
    for child in named_children(node):
        if child.type == "field":
            _extract_field(child, file_path, struct_id, graph)


def _extract_field(node: Node, file_path: str, struct_id: str, graph: CodeGraph) -> None:
    name = child_text(node, frozenset({"identifier"}))
    if not name:
        return
    field_id = f"{struct_id}.{name}"
    graph.nodes[field_id] = CodeUnit(
        id=field_id,
        name=name,
        kind=NodeKind.METHOD,
        location=make_location(node, file_path),
        return_type=return_type_after_params(node) or _field_type(node),
        attributes=(("schema_role", "field"),),
    )
    add_contains_edge(graph, struct_id, field_id)


def _field_type(node: Node) -> TypeRef | None:
    type_node = first_child_type(node, frozenset({"type"}))
    if type_node is None:
        return None
    return TypeRef(name=node_text(type_node))


def _extract_service(node: Node, file_path: str, module_id: str, graph: CodeGraph) -> None:
    name = child_text(node, frozenset({"identifier"}))
    if not name:
        return
    service_id = f"{module_id}:{name}"
    graph.nodes[service_id] = CodeUnit(
        id=service_id,
        name=name,
        kind=NodeKind.INTERFACE,
        location=make_location(node, file_path),
    )
    add_contains_edge(graph, module_id, service_id)
    for child in named_children(node):
        if child.type == "function_definition":
            _extract_function(child, file_path, service_id, graph)


def _extract_function(node: Node, file_path: str, service_id: str, graph: CodeGraph) -> None:
    name = child_text(node, frozenset({"identifier"}))
    if not name:
        return
    func_id = f"{service_id}.{name}"
    graph.nodes[func_id] = CodeUnit(
        id=func_id,
        name=name,
        kind=NodeKind.METHOD,
        location=make_location(node, file_path),
        parameters=extract_parameters(node),
        return_type=_field_type(node),
        attributes=(("schema_role", "service_function"),),
    )
    add_contains_edge(graph, service_id, func_id)


def _extract_enum(node: Node, file_path: str, module_id: str, graph: CodeGraph) -> None:
    name = child_text(node, frozenset({"identifier"}))
    if not name:
        return
    enum_id = f"{module_id}:{name}"
    graph.nodes[enum_id] = CodeUnit(
        id=enum_id,
        name=name,
        kind=NodeKind.ENUM,
        location=make_location(node, file_path),
    )
    add_contains_edge(graph, module_id, enum_id)


def _extract_import(node: Node, graph: CodeGraph, module_id: str) -> None:
    target = node_text(node).removeprefix("include").strip().strip("\"'")
    if target:
        graph.dependencies.append(target)
        graph.edges.append(CodeEdge(source_id=module_id, target_id=target, kind=EdgeKind.IMPORTS))
