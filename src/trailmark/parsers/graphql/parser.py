"""GraphQL schema parser using tree-sitter."""

from __future__ import annotations

from pathlib import Path

from tree_sitter import Node, Parser
from tree_sitter_language_pack import get_language

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
from trailmark.parsers._simple import clean_type_name, descendants, first_child_type, named_children

_EXTENSIONS = (".graphql", ".gql")


class GraphQLParser:
    """Parses GraphQL schemas into CodeGraph."""

    @property
    def language(self) -> str:
        return "graphql"

    def __init__(self) -> None:
        self._parser = Parser(get_language("graphql"))

    def parse_file(self, file_path: str) -> CodeGraph:
        source = Path(file_path).read_bytes()
        tree = self._parser.parse(source)
        graph = CodeGraph(language="graphql", root_path=file_path)
        module_id = module_id_from_path(file_path)
        _visit_root(tree.root_node, file_path, module_id, graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        return parse_directory(self.parse_file, "graphql", dir_path, _EXTENSIONS)


def _visit_root(root: Node, file_path: str, module_id: str, graph: CodeGraph) -> None:
    add_module_node(root, file_path, module_id, graph)
    for node in descendants(root):
        if node.type in {
            "object_type_definition",
            "interface_type_definition",
            "input_object_type_definition",
        }:
            _extract_object(node, file_path, module_id, graph)
        elif node.type == "enum_type_definition":
            _extract_enum(node, file_path, module_id, graph)


def _extract_object(node: Node, file_path: str, module_id: str, graph: CodeGraph) -> None:
    name_node = first_child_type(node, frozenset({"name"}))
    if name_node is None:
        return
    name = node_text(name_node)
    object_id = f"{module_id}:{name}"
    graph.nodes[object_id] = CodeUnit(
        id=object_id,
        name=name,
        kind=NodeKind.INTERFACE,
        location=make_location(node, file_path),
    )
    add_contains_edge(graph, module_id, object_id)
    fields = first_child_type(node, frozenset({"fields_definition", "input_fields_definition"}))
    if fields is None:
        return
    for child in named_children(fields):
        if child.type in {"field_definition", "input_value_definition"}:
            _extract_field(child, file_path, object_id, graph)


def _extract_field(node: Node, file_path: str, object_id: str, graph: CodeGraph) -> None:
    name_node = first_child_type(node, frozenset({"name"}))
    if name_node is None:
        return
    name = node_text(name_node)
    field_id = f"{object_id}.{name}"
    graph.nodes[field_id] = CodeUnit(
        id=field_id,
        name=name,
        kind=NodeKind.METHOD,
        location=make_location(node, file_path),
        parameters=_arguments(node),
        return_type=_type_ref(node),
        attributes=(("schema_role", _field_role(object_id)),),
    )
    add_contains_edge(graph, object_id, field_id)


def _field_role(object_id: str) -> str:
    parent = object_id.rsplit(":", 1)[-1]
    if parent in {"Query", "Mutation", "Subscription"}:
        return "root_operation"
    return "field"


def _arguments(node: Node) -> tuple[Parameter, ...]:
    args = first_child_type(node, frozenset({"arguments_definition"}))
    if args is None:
        return ()
    params: list[Parameter] = []
    for arg in named_children(args):
        if arg.type != "input_value_definition":
            continue
        name_node = first_child_type(arg, frozenset({"name"}))
        if name_node is None:
            continue
        params.append(Parameter(name=node_text(name_node), type_ref=_type_ref(arg)))
    return tuple(params)


def _type_ref(node: Node) -> TypeRef | None:
    type_node = first_child_type(node, frozenset({"type"}))
    if type_node is None:
        return None
    return TypeRef(name=clean_type_name(node_text(type_node)))


def _extract_enum(node: Node, file_path: str, module_id: str, graph: CodeGraph) -> None:
    name_node = first_child_type(node, frozenset({"name"}))
    if name_node is None:
        return
    name = node_text(name_node)
    enum_id = f"{module_id}:{name}"
    graph.nodes[enum_id] = CodeUnit(
        id=enum_id,
        name=name,
        kind=NodeKind.ENUM,
        location=make_location(node, file_path),
    )
    add_contains_edge(graph, module_id, enum_id)
