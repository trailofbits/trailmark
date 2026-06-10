"""Protocol Buffers parser using tree-sitter."""

from __future__ import annotations

from pathlib import Path

from tree_sitter import Node, Parser
from tree_sitter_language_pack import get_language

from trailmark.models.edges import CodeEdge, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit, NodeKind, Parameter, TypeRef
from trailmark.parsers._common import (
    add_contains_edge,
    add_module_node,
    make_location,
    module_id_from_path,
    node_text,
    parse_directory,
)
from trailmark.parsers._simple import child_text, clean_type_name, first_child_type, named_children

_EXTENSIONS = (".proto",)


class ProtoParser:
    """Parses Protocol Buffers schemas into CodeGraph."""

    @property
    def language(self) -> str:
        return "proto"

    def __init__(self) -> None:
        self._parser = Parser(get_language("proto"))

    def parse_file(self, file_path: str) -> CodeGraph:
        source = Path(file_path).read_bytes()
        tree = self._parser.parse(source)
        graph = CodeGraph(language="proto", root_path=file_path)
        module_id = _package_id(tree.root_node) or module_id_from_path(file_path)
        _visit_root(tree.root_node, file_path, module_id, graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        return parse_directory(self.parse_file, "proto", dir_path, _EXTENSIONS)


def _visit_root(root: Node, file_path: str, module_id: str, graph: CodeGraph) -> None:
    add_module_node(root, file_path, module_id, graph)
    for child in named_children(root):
        if child.type == "import":
            _extract_import(child, graph, module_id)
        elif child.type == "service":
            _extract_service(child, file_path, module_id, graph)
        elif child.type == "message":
            _extract_message(child, file_path, module_id, module_id, graph)
        elif child.type == "enum":
            _extract_enum(child, file_path, module_id, module_id, graph)


def _package_id(root: Node) -> str:
    for child in named_children(root):
        if child.type == "package":
            name = child_text(child, frozenset({"full_ident"}))
            return name
    return ""


def _extract_service(node: Node, file_path: str, module_id: str, graph: CodeGraph) -> None:
    name = child_text(node, frozenset({"service_name"}))
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
        if child.type == "rpc":
            _extract_rpc(child, file_path, service_id, graph)


def _extract_rpc(node: Node, file_path: str, service_id: str, graph: CodeGraph) -> None:
    name = child_text(node, frozenset({"rpc_name"}))
    types = [
        node_text(child) for child in named_children(node) if child.type == "message_or_enum_type"
    ]
    params = (Parameter(name="request", type_ref=TypeRef(name=types[0])),) if types else ()
    return_type = TypeRef(name=types[1]) if len(types) > 1 else None
    rpc_id = f"{service_id}.{name}"
    graph.nodes[rpc_id] = CodeUnit(
        id=rpc_id,
        name=name,
        kind=NodeKind.METHOD,
        location=make_location(node, file_path),
        parameters=params,
        return_type=return_type,
        attributes=(("schema_role", "rpc"),),
    )
    add_contains_edge(graph, service_id, rpc_id)


def _extract_message(
    node: Node,
    file_path: str,
    module_id: str,
    container_id: str,
    graph: CodeGraph,
) -> None:
    name = child_text(node, frozenset({"message_name"}))
    if not name:
        return
    message_id = f"{module_id}:{name}"
    graph.nodes[message_id] = CodeUnit(
        id=message_id,
        name=name,
        kind=NodeKind.STRUCT,
        location=make_location(node, file_path),
    )
    add_contains_edge(graph, container_id, message_id)
    body = first_child_type(node, frozenset({"message_body"}))
    if body is None:
        return
    for child in named_children(body):
        if child.type == "field":
            _extract_field(child, file_path, message_id, graph)


def _extract_field(node: Node, file_path: str, message_id: str, graph: CodeGraph) -> None:
    name = child_text(node, frozenset({"identifier"}))
    type_node = first_child_type(node, frozenset({"type", "message_or_enum_type"}))
    field_id = f"{message_id}.{name}"
    graph.nodes[field_id] = CodeUnit(
        id=field_id,
        name=name,
        kind=NodeKind.METHOD,
        location=make_location(node, file_path),
        return_type=TypeRef(name=clean_type_name(node_text(type_node))) if type_node else None,
        attributes=(("schema_role", "field"),),
    )
    add_contains_edge(graph, message_id, field_id)


def _extract_enum(
    node: Node,
    file_path: str,
    module_id: str,
    container_id: str,
    graph: CodeGraph,
) -> None:
    name = child_text(node, frozenset({"enum_name", "identifier"}))
    if not name:
        return
    enum_id = f"{module_id}:{name}"
    graph.nodes[enum_id] = CodeUnit(
        id=enum_id,
        name=name,
        kind=NodeKind.ENUM,
        location=make_location(node, file_path),
    )
    add_contains_edge(graph, container_id, enum_id)


def _extract_import(node: Node, graph: CodeGraph, module_id: str) -> None:
    target = node_text(node).removeprefix("import").strip().strip(";").strip("\"'")
    if target:
        graph.dependencies.append(target)
        graph.edges.append(CodeEdge(source_id=module_id, target_id=target, kind=EdgeKind.IMPORTS))
