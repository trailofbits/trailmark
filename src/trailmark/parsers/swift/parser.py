"""Swift language parser using tree-sitter."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from tree_sitter import Node, Parser
from tree_sitter_language_pack import get_language

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import (
    BranchInfo,
    CodeUnit,
    NodeKind,
    Parameter,
    TypeRef,
)
from trailmark.parsers._binding_index import binding_scope
from trailmark.parsers._bindings import guard_call
from trailmark.parsers._common import (
    add_contains_edge,
    add_module_node,
    collect_body_info,
    compute_complexity,
    extract_type_parameters,
    first_child_by_type,
    make_location,
    module_id_from_path,
    node_text,
    parse_directory,
)
from trailmark.parsers._declarations import (
    add_callable,
    add_type,
    bind_lexical_calls,
    callable_scope,
)
from trailmark.parsers._nesting import bounded_nesting
from trailmark.parsers._walk import walk_owned

_EXTENSIONS = (".swift",)

# Branch-like constructs that contribute to cyclomatic complexity.
_BRANCH_NODE_TYPES = frozenset(
    {
        "if_statement",
        "guard_statement",
        "for_statement",
        "while_statement",
        "repeat_while_statement",
        "switch_entry",
        "do_statement",
        "catch_block",
    }
)

# Swift uses `throw <expr>` to raise errors.
_THROW_TYPES = frozenset({"throw_statement"})

# Node types that identify a type at a call or parameter position.
_SWIFT_TYPE_NODES = frozenset(
    {
        "user_type",
        "type_identifier",
        "optional_type",
        "array_type",
        "dictionary_type",
        "tuple_type",
        "function_type",
    }
)

# Class-like declarations that can contain methods.
_CLASS_LIKE_KIND = {
    "class": NodeKind.CLASS,
    "struct": NodeKind.STRUCT,
    "enum": NodeKind.ENUM,
    "protocol": NodeKind.INTERFACE,
    "extension": NodeKind.CLASS,
}


class SwiftParser:
    """Parses Swift source files into CodeGraph using tree-sitter."""

    @property
    def language(self) -> str:
        return "swift"

    def __init__(self) -> None:
        self._parser = Parser(get_language("swift"))

    @binding_scope
    def parse_file(self, file_path: str) -> CodeGraph:
        """Parse a single Swift file into a CodeGraph."""
        source = Path(file_path).read_bytes()
        tree = self._parser.parse(source)
        graph = CodeGraph(language="swift", root_path=file_path)
        module_id = module_id_from_path(file_path)
        with callable_scope(graph):
            _visit_module(tree.root_node, file_path, module_id, graph)
        _merge_extensions(graph)
        bind_lexical_calls(graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        """Parse all .swift files under dir_path into a merged graph."""
        graph = parse_directory(self.parse_file, "swift", dir_path, _EXTENSIONS)
        _merge_extensions(graph)
        bind_lexical_calls(graph)
        return graph


def _visit_module(
    root: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    add_module_node(root, file_path, module_id, graph)
    for child in root.children:
        _visit_top_level_node(child, file_path, module_id, graph)


def _visit_top_level_node(
    child: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    if child.type == "function_declaration":
        _extract_function(child, file_path, module_id, module_id, graph)
    elif child.type == "class_declaration":
        _extract_class_like(child, file_path, module_id, graph)
    elif child.type == "protocol_declaration":
        _extract_protocol(child, file_path, module_id, graph)
    elif child.type == "import_declaration":
        _extract_import(child, graph)


def _extract_protocol(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a Swift `protocol X { ... }` as an INTERFACE node."""
    name_node = first_child_by_type(node, "type_identifier")
    if name_node is None:
        return
    proto_name = node_text(name_node)
    proto_id = f"{module_id}:{proto_name}"
    unit = CodeUnit(
        id=proto_id,
        name=proto_name,
        kind=NodeKind.INTERFACE,
        location=make_location(node, file_path),
        type_parameters=extract_type_parameters(node),
    )
    add_type(graph, unit)
    add_contains_edge(graph, module_id, proto_id)
    body = first_child_by_type(node, "protocol_body")
    if body is None:
        return
    for member in body.children:
        if member.type == "protocol_function_declaration":
            _extract_function(member, file_path, module_id, proto_id, graph)


@bounded_nesting
def _extract_class_like(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
    container_id: str | None = None,
) -> None:
    """Extract a class/struct/enum/protocol/extension and its methods."""
    kind = _class_kind(node)
    if kind is None:
        return
    name_node = node.child_by_field_name("name") or first_child_by_type(node, "type_identifier")
    if name_node is None:
        return
    class_name = node_text(name_node)
    while "<" in class_name and re.search(r"<[^<>]*>", class_name):
        class_name = re.sub(r"<[^<>]*>", "", class_name)
    class_id = f"{container_id}.{class_name}" if container_id else f"{module_id}:{class_name}"
    unit = CodeUnit(
        id=class_id,
        name=class_name,
        kind=kind,
        location=make_location(node, file_path),
        type_parameters=extract_type_parameters(node),
    )
    extension = any(c.type == "extension" for c in node.children)
    if not extension or class_id not in graph.nodes:
        if extension:
            unit = replace(
                unit, kind=NodeKind.NAMESPACE, attributes=(("swift_extension_target", class_name),)
            )
        add_type(graph, unit)
        add_contains_edge(graph, container_id or module_id, class_id)

    body = first_child_by_type(node, "class_body")
    if body is None:
        body = first_child_by_type(node, "enum_class_body")
    if body is None:
        return
    for member in body.children:
        if member.type == "function_declaration":
            _extract_function(member, file_path, module_id, class_id, graph)
        elif member.type == "class_declaration":
            _extract_class_like(member, file_path, module_id, graph, class_id)


def _class_kind(node: Node) -> NodeKind | None:
    """Determine which class-like kind a class_declaration represents."""
    for child in node.children:
        if child.type in _CLASS_LIKE_KIND:
            return _CLASS_LIKE_KIND[child.type]
    return None


def _extract_function(
    node: Node,
    file_path: str,
    module_id: str,
    container_id: str,
    graph: CodeGraph,
    *,
    name_override: str | None = None,
) -> None:
    """Extract a Swift function or method."""
    name_node = first_child_by_type(node, "simple_identifier")
    if name_node is None and name_override is None:
        return
    func_name = name_override or (node_text(name_node) if name_node is not None else "")
    nested = graph.nodes[container_id].kind in {NodeKind.FUNCTION, NodeKind.METHOD}
    is_method = container_id != module_id and not nested
    func_id = (
        f"{container_id}.{func_name}"
        if container_id != module_id
        else f"{container_id}:{func_name}"
    )

    params = _extract_parameters(node)
    return_type = _extract_return_type(node)
    body = first_child_by_type(node, "function_body")
    if node.type == "lambda_literal":
        body = first_child_by_type(node, "statements")

    branches, exception_types, calls = _collect_func_body(body, file_path)
    complexity = compute_complexity(branches)

    unit = CodeUnit(
        id=func_id,
        name=func_name,
        kind=NodeKind.METHOD if is_method else NodeKind.FUNCTION,
        location=make_location(node, file_path),
        parameters=tuple(params),
        return_type=return_type,
        exception_types=tuple(exception_types),
        type_parameters=extract_type_parameters(node),
        cyclomatic_complexity=complexity,
        branches=tuple(branches),
    )
    func_id = add_callable(graph, unit)
    add_contains_edge(graph, container_id, func_id)

    if body is not None:
        for deferred in walk_owned(
            body,
            boundaries=frozenset(
                {
                    "function_declaration",
                    "lambda_literal",
                    "class_declaration",
                    "object_declaration",
                }
            ),
        ):
            if deferred.type in {"function_declaration", "lambda_literal"}:
                name = first_child_by_type(deferred, "simple_identifier")
                nested_name = (
                    node_text(name)
                    if deferred.type == "function_declaration" and name is not None
                    else f"$lambda@{deferred.start_point.row + 1}_{deferred.start_point.column}"
                )
                _extract_function(
                    deferred, file_path, module_id, func_id, graph, name_override=nested_name
                )
    _add_call_edges(calls, func_id, file_path, graph)


def _extract_parameters(node: Node) -> list[Parameter]:
    """Extract parameters from a function_declaration."""
    params: list[Parameter] = []
    for child in node.children:
        if child.type == "parameter":
            _extract_single_param(child, params)
    return params


def _extract_single_param(decl: Node, params: list[Parameter]) -> None:
    """Extract one parameter's name and type."""
    name = ""
    type_ref: TypeRef | None = None
    for child in decl.children:
        if child.type == "simple_identifier":
            name = node_text(child)
        elif child.type in _SWIFT_TYPE_NODES and type_ref is None:
            type_ref = TypeRef(name=node_text(child))
    if name:
        params.append(Parameter(name=name, type_ref=type_ref))


def _extract_return_type(node: Node) -> TypeRef | None:
    """Find the return type that follows `->`."""
    saw_arrow = False
    for child in node.children:
        if child.type == "->":
            saw_arrow = True
            continue
        if saw_arrow and child.type in _SWIFT_TYPE_NODES:
            return TypeRef(name=node_text(child))
    return None


def _collect_func_body(
    body: Node | None,
    file_path: str,
) -> tuple[list[BranchInfo], list[TypeRef], list[tuple[str, Node]]]:
    branches: list[BranchInfo] = []
    exception_types: list[TypeRef] = []
    calls: list[tuple[str, Node]] = []
    if body is not None:
        collect_body_info(
            body,
            file_path,
            _BRANCH_NODE_TYPES,
            "call_expression",
            _THROW_TYPES,
            branches,
            exception_types,
            calls,
            skip_types=frozenset(
                {
                    "function_declaration",
                    "lambda_literal",
                    "class_declaration",
                    "object_declaration",
                }
            ),
        )
    return branches, exception_types, calls


def _add_call_edges(
    calls: list[tuple[str, Node]],
    source_id: str,
    file_path: str,
    graph: CodeGraph,
) -> None:
    for call_name, call_node in calls:
        confidence = EdgeConfidence.CERTAIN if "." not in call_name else EdgeConfidence.INFERRED
        graph.edges.append(
            guard_call(
                CodeEdge(
                    source_id=source_id,
                    target_id=call_name,
                    kind=EdgeKind.CALLS,
                    confidence=confidence,
                    location=make_location(call_node, file_path),
                    attributes=(("bare_name", call_name),) if call_name.isidentifier() else (),
                ),
                call_node,
                graph=graph,
            )
        )


def _extract_import(node: Node, graph: CodeGraph) -> None:
    """Extract `import Foo` declarations."""
    for child in node.children:
        if child.type == "identifier":
            dep = node_text(child)
            if dep and dep not in graph.dependencies:
                graph.dependencies.append(dep)


def _merge_extensions(graph: CodeGraph) -> None:
    """Move extension members using indexes built once for the merged graph."""
    types: dict[str, list[CodeUnit]] = {}
    children: dict[str, list[str]] = {}
    for unit in graph.nodes.values():
        if unit.kind in {NodeKind.CLASS, NodeKind.STRUCT, NodeKind.INTERFACE, NodeKind.ENUM}:
            for key in {unit.name, unit.id.split(":", 1)[-1]}:
                types.setdefault(key, []).append(unit)
    for edge in graph.edges:
        if edge.kind == EdgeKind.CONTAINS:
            children.setdefault(edge.source_id, []).append(edge.target_id)
    moved: dict[str, str] = {}
    removed = set()
    reserved = set(graph.nodes)
    for extension in tuple(graph.nodes.values()):
        target = dict(extension.attributes).get("swift_extension_target")
        if not isinstance(target, str):
            continue
        candidates = types.get(target, [])
        local = [
            unit for unit in candidates if unit.location.file_path == extension.location.file_path
        ]
        candidates = local or candidates
        if len(candidates) == 1:
            _extension_moves(extension.id, candidates[0].id, graph, children, reserved, moved)
            removed.add(extension.id)
    graph.nodes = {
        moved.get(ident, ident): replace(unit, id=moved[ident]) if ident in moved else unit
        for ident, unit in graph.nodes.items()
        if ident not in removed
    }
    graph.edges[:] = [
        replace(
            e,
            source_id=moved.get(e.source_id, e.source_id),
            target_id=moved.get(e.target_id, e.target_id),
        )
        for e in graph.edges
        if not (e.kind == EdgeKind.CONTAINS and e.target_id in removed)
    ]


def _extension_moves(
    extension: str,
    owner: str,
    graph: CodeGraph,
    children: dict[str, list[str]],
    reserved: set[str],
    moved: dict[str, str],
) -> None:
    pending = list(children.get(extension, ()))
    while pending:
        ident = pending.pop()
        if ident not in graph.nodes or ident in moved:
            continue
        unit = graph.nodes[ident]
        destination = owner + ident[len(extension) :]
        if destination in reserved:
            destination += f"@{unit.location.start_line}_{unit.location.start_col or 0}"
        reserved.add(destination)
        moved[ident] = destination
        pending.extend(children.get(ident, ()))
    moved[extension] = owner
