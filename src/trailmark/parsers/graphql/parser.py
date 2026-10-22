"""GraphQL schema parser using tree-sitter."""

from __future__ import annotations

from dataclasses import replace
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
from trailmark.parsers._simple import descendants, first_child_type, named_children

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
        _assign_roots(graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        graph = parse_directory(self.parse_file, "graphql", dir_path, _EXTENSIONS)
        _merge_extensions(graph)
        _assign_roots(graph)
        return graph


def _visit_root(root: Node, file_path: str, module_id: str, graph: CodeGraph) -> None:
    add_module_node(root, file_path, module_id, graph)
    mappings = {}
    for node in descendants(root):
        if node.type == "root_operation_type_definition":
            operation = first_child_type(node, frozenset({"operation_type"}))
            names = [child for child in descendants(node) if child.type == "name"]
            if operation is not None and names:
                mappings[f"graphql_{node_text(operation)}_root"] = node_text(names[-1])
        if node.type in {
            "object_type_definition",
            "interface_type_definition",
            "input_object_type_definition",
            "object_type_extension",
            "interface_type_extension",
            "input_object_type_extension",
        }:
            _extract_object(node, file_path, module_id, graph)
        elif node.type == "enum_type_definition":
            _extract_enum(node, file_path, module_id, graph)
    if mappings:
        graph.nodes[module_id] = replace(graph.nodes[module_id], attributes=tuple(mappings.items()))


def _extract_object(node: Node, file_path: str, module_id: str, graph: CodeGraph) -> None:
    name_node = first_child_type(node, frozenset({"name"}))
    if name_node is None:
        return
    name = node_text(name_node)
    object_id = f"{module_id}:{name}"
    unit = CodeUnit(
        id=object_id,
        name=name,
        kind=NodeKind.INTERFACE,
        location=make_location(node, file_path),
        attributes=(("graphql_type", name), ("graphql_extension", node.type.endswith("extension"))),
    )
    if object_id not in graph.nodes or not node.type.endswith("extension"):
        graph.nodes[object_id] = unit
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
        default = first_child_type(arg, frozenset({"default_value"}))
        params.append(
            Parameter(
                name=node_text(name_node),
                type_ref=_type_ref(arg),
                default=node_text(default).lstrip("= ") if default else None,
            )
        )
    return tuple(params)


def _type_ref(node: Node) -> TypeRef | None:
    type_node = first_child_type(node, frozenset({"type"}))
    if type_node is None:
        return None
    return _wrapped_type(node_text(type_node))


def _wrapped_type(text: str) -> TypeRef:
    text = "".join(text.split())
    if text.endswith("!"):
        return TypeRef(name=text, generic_args=(_wrapped_type(text[:-1]),))
    if text.startswith("[") and text.endswith("]"):
        return TypeRef(name=text, generic_args=(_wrapped_type(text[1:-1]),))
    return TypeRef(name=text)


def _assign_roots(graph: CodeGraph) -> None:
    roots = {"Query", "Mutation", "Subscription"}
    explicit = {
        str(value)
        for unit in graph.nodes.values()
        for key, value in unit.attributes
        if key.startswith("graphql_") and key.endswith("_root")
    }
    if explicit:
        roots = explicit
    for node_id, unit in list(graph.nodes.items()):
        if dict(unit.attributes).get("schema_role") not in {"field", "root_operation"}:
            continue
        owner = graph.nodes.get(node_id.rsplit(".", 1)[0])
        role = "root_operation" if owner is not None and owner.name in roots else "field"
        graph.nodes[node_id] = replace(
            unit,
            attributes=tuple(
                (key, role if key == "schema_role" else value) for key, value in unit.attributes
            ),
        )


def _merge_extensions(graph: CodeGraph) -> None:
    owners: dict[str, list[str]] = {}
    for unit in graph.nodes.values():
        attrs = dict(unit.attributes)
        if "graphql_type" in attrs and not attrs.get("graphql_extension"):
            owners.setdefault(unit.name, []).append(unit.id)
    remap = {}
    for unit in graph.nodes.values():
        if not dict(unit.attributes).get("graphql_extension"):
            continue
        candidates = owners.get(unit.name, [])
        if len(candidates) == 1:
            remap[unit.id] = candidates[0]
    for node_id, unit in list(graph.nodes.items()):
        if node_id in remap:
            del graph.nodes[node_id]
        elif node_id.rsplit(".", 1)[0] in remap:
            target = remap[node_id.rsplit(".", 1)[0]] + "." + unit.name
            graph.nodes[target] = replace(unit, id=target)
            remap[node_id] = target
            del graph.nodes[node_id]
    graph.edges[:] = [
        replace(
            edge,
            source_id=remap.get(edge.source_id, edge.source_id),
            target_id=remap.get(edge.target_id, edge.target_id),
        )
        for edge in graph.edges
    ]


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
