"""Kotlin language parser using tree-sitter.

Targets Kotlin source used in Android apps, Spring backends, and Ktor
services. Extracts:

- Top-level functions (``fun main()``, ``suspend fun fetch(...)``).
- Classes, interfaces, data classes, objects, enum classes.
- Methods declared inside ``class_body``.
- Parameters (``function_value_parameters`` → ``parameter``).
- Return types from the type node that follows ``:`` on the signature.
- Imports (``import_header``).

Known gap (deferred, parallel to Swift): ``throw`` statements are wrapped
in ``jump_expression`` nodes that also cover ``return``/``break``/
``continue``. Capturing exception types requires a Kotlin-specific walk
that filters by the leading ``throw`` token. Not implemented in this pass.
"""

from __future__ import annotations

from pathlib import Path

from tree_sitter import Node
from tree_sitter_language_pack import get_parser

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import (
    BranchInfo,
    CodeUnit,
    NodeKind,
    Parameter,
    TypeRef,
)
from trailmark.parsers._common import (
    add_contains_edge,
    add_module_node,
    collect_body_info,
    compute_complexity,
    make_location,
    module_id_from_path,
    node_text,
    parse_directory,
)

_EXTENSIONS = (".kt", ".kts")

_BRANCH_NODE_TYPES = frozenset(
    {
        "if_expression",
        "when_expression",
        "when_entry",
        "for_statement",
        "while_statement",
        "do_while_statement",
        "catch_block",
    }
)

# See parser docstring — throw capture deferred.
_THROW_TYPES: frozenset[str] = frozenset()

# Kotlin types that can appear at parameter/return position.
_KOTLIN_TYPE_NODES = frozenset(
    {
        "user_type",
        "type_identifier",
        "nullable_type",
        "function_type",
        "parenthesized_type",
    }
)


class KotlinParser:
    """Parses Kotlin source files into CodeGraph using tree-sitter."""

    @property
    def language(self) -> str:
        return "kotlin"

    def __init__(self) -> None:
        self._parser = get_parser("kotlin")

    def parse_file(self, file_path: str) -> CodeGraph:
        """Parse a single Kotlin file into a CodeGraph."""
        source = Path(file_path).read_bytes()
        tree = self._parser.parse(source)
        graph = CodeGraph(language="kotlin", root_path=file_path)
        module_id = module_id_from_path(file_path)
        _visit_module(tree.root_node, file_path, module_id, graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        """Parse all Kotlin source files under dir_path."""
        return parse_directory(
            self.parse_file,
            "kotlin",
            dir_path,
            _EXTENSIONS,
        )


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
    elif child.type == "class_declaration" or child.type == "object_declaration":
        _extract_class_like(child, file_path, module_id, graph)
    elif child.type == "import_list":
        for header in child.children:
            if header.type == "import_header":
                _extract_import(header, graph)


def _extract_class_like(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a Kotlin class / interface / data class / object."""
    name_node = _first_child_by_type(node, "type_identifier")
    if name_node is None:
        return
    class_name = node_text(name_node)
    class_id = f"{module_id}:{class_name}"
    kind = _kotlin_class_kind(node)
    graph.nodes[class_id] = CodeUnit(
        id=class_id,
        name=class_name,
        kind=kind,
        location=make_location(node, file_path),
    )
    add_contains_edge(graph, module_id, class_id)

    body = _first_child_by_type(node, "class_body")
    if body is None:
        body = _first_child_by_type(node, "enum_class_body")
    if body is None:
        return
    for member in body.children:
        if member.type == "function_declaration":
            _extract_function(member, file_path, module_id, class_id, graph)


def _kotlin_class_kind(node: Node) -> NodeKind:
    """Determine whether a declaration is a class/interface/enum/object."""
    has_data = False
    has_enum = False
    for child in node.children:
        if child.type == "interface":
            return NodeKind.INTERFACE
        if child.type == "object":
            return NodeKind.CLASS
        if child.type == "modifiers":
            for mod in child.children:
                if mod.type == "class_modifier":
                    modifier_text = node_text(mod)
                    if modifier_text == "data":
                        has_data = True
                    elif modifier_text == "enum":
                        has_enum = True
    if has_enum:
        return NodeKind.ENUM
    if has_data:
        # Data classes are still classes semantically; no separate kind.
        return NodeKind.CLASS
    return NodeKind.CLASS


def _extract_function(
    node: Node,
    file_path: str,
    module_id: str,
    container_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a Kotlin function (top-level or method)."""
    name_node = _first_child_by_type(node, "simple_identifier")
    if name_node is None:
        return
    func_name = node_text(name_node)
    is_method = container_id != module_id
    func_id = f"{container_id}.{func_name}" if is_method else f"{container_id}:{func_name}"

    params = _extract_parameters(node)
    return_type = _extract_return_type(node)
    body = _first_child_by_type(node, "function_body")

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
        cyclomatic_complexity=complexity,
        branches=tuple(branches),
    )
    graph.nodes[func_id] = unit
    add_contains_edge(graph, container_id, func_id)

    _add_call_edges(calls, func_id, file_path, graph)


def _extract_parameters(node: Node) -> list[Parameter]:
    """Extract parameters from a function_declaration."""
    plist = _first_child_by_type(node, "function_value_parameters")
    if plist is None:
        return []
    params: list[Parameter] = []
    for child in plist.children:
        if child.type == "parameter":
            _extract_single_param(child, params)
    return params


def _extract_single_param(decl: Node, params: list[Parameter]) -> None:
    """Extract one parameter's name and type."""
    name = ""
    type_ref: TypeRef | None = None
    for child in decl.children:
        if child.type == "simple_identifier" and not name:
            name = node_text(child)
        elif child.type in _KOTLIN_TYPE_NODES and type_ref is None:
            type_ref = TypeRef(name=node_text(child))
    if name:
        params.append(Parameter(name=name, type_ref=type_ref))


def _extract_return_type(node: Node) -> TypeRef | None:
    """Return type is the type node after the first `:` on the signature."""
    saw_colon = False
    for child in node.children:
        if child.type == ":":
            saw_colon = True
            continue
        if saw_colon and child.type in _KOTLIN_TYPE_NODES:
            return TypeRef(name=node_text(child))
        if child.type == "function_body":
            # Hit the body before any colon — no explicit return type.
            return None
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
            CodeEdge(
                source_id=source_id,
                target_id=call_name,
                kind=EdgeKind.CALLS,
                confidence=confidence,
                location=make_location(call_node, file_path),
            )
        )


def _extract_import(node: Node, graph: CodeGraph) -> None:
    """Extract `import foo.bar.Baz` declarations as dependencies."""
    for child in node.children:
        if child.type == "identifier":
            raw = node_text(child)
            # Use the last segment for consistency with other parsers.
            dep = raw.rsplit(".", 1)[-1]
            if dep and dep not in graph.dependencies:
                graph.dependencies.append(dep)


def _first_child_by_type(node: Node, type_name: str) -> Node | None:
    for child in node.children:
        if child.type == type_name:
            return child
    return None
