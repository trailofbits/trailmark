"""C language parser using tree-sitter."""

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

_BRANCH_NODE_TYPES = frozenset(
    {
        "if_statement",
        "for_statement",
        "while_statement",
        "do_statement",
        "case_statement",
        "conditional_expression",
    }
)

_THROW_TYPES: frozenset[str] = frozenset()

_EXTENSIONS = (".c", ".h")

_C_TYPE_NODES = frozenset(
    {
        "type_identifier",
        "primitive_type",
        "sized_type_specifier",
    }
)


class CParser:
    """Parses C source files into CodeGraph using tree-sitter."""

    @property
    def language(self) -> str:
        return "c"

    def __init__(self) -> None:
        self._parser = get_parser("c")

    def parse_file(self, file_path: str) -> CodeGraph:
        """Parse a single C file into a CodeGraph."""
        source = Path(file_path).read_bytes()
        tree = self._parser.parse(source)
        graph = CodeGraph(language="c", root_path=file_path)
        module_id = module_id_from_path(file_path)
        _visit_module(tree.root_node, file_path, module_id, graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        """Parse all C files under dir_path into a merged graph."""
        return parse_directory(
            self.parse_file,
            "c",
            dir_path,
            _EXTENSIONS,
        )


def _visit_module(
    root: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Walk the top-level of a C module."""
    add_module_node(root, file_path, module_id, graph)
    for child in root.children:
        _visit_top_level_node(child, file_path, module_id, graph)


def _visit_top_level_node(
    child: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Dispatch a single top-level C node."""
    if child.type == "function_definition":
        _extract_function(child, file_path, module_id, graph)
    elif child.type == "preproc_include":
        _extract_include(child, graph)
    elif child.type in ("type_definition", "declaration"):
        _extract_type_def(child, file_path, module_id, graph)
    elif child.type == "struct_specifier":
        _extract_struct(child, file_path, module_id, graph)
    elif child.type == "enum_specifier":
        _extract_enum(child, file_path, module_id, graph)


def _extract_type_def(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract struct or enum from a type_definition or declaration."""
    typedef_name = _get_typedef_name(node)
    for child in node.children:
        if child.type == "struct_specifier":
            _extract_struct(
                child,
                file_path,
                module_id,
                graph,
                fallback_name=typedef_name,
            )
        elif child.type == "enum_specifier":
            _extract_enum(
                child,
                file_path,
                module_id,
                graph,
                fallback_name=typedef_name,
            )


def _get_typedef_name(node: Node) -> str | None:
    """Get the typedef alias name from a type_definition node."""
    for child in node.children:
        if child.type == "type_identifier":
            return node_text(child)
    return None


def _extract_struct(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
    fallback_name: str | None = None,
) -> None:
    """Extract a struct definition."""
    name_node = node.child_by_field_name("name")
    struct_name = node_text(name_node) if name_node else fallback_name
    if not struct_name:
        return
    struct_id = f"{module_id}:{struct_name}"
    location = make_location(node, file_path)

    unit = CodeUnit(
        id=struct_id,
        name=struct_name,
        kind=NodeKind.STRUCT,
        location=location,
    )
    graph.nodes[struct_id] = unit
    add_contains_edge(graph, module_id, struct_id)


def _extract_enum(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
    fallback_name: str | None = None,
) -> None:
    """Extract an enum definition."""
    name_node = node.child_by_field_name("name")
    enum_name = node_text(name_node) if name_node else fallback_name
    if not enum_name:
        return
    enum_id = f"{module_id}:{enum_name}"
    location = make_location(node, file_path)

    unit = CodeUnit(
        id=enum_id,
        name=enum_name,
        kind=NodeKind.ENUM,
        location=location,
    )
    graph.nodes[enum_id] = unit
    add_contains_edge(graph, module_id, enum_id)


def _extract_function(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a C function definition."""
    func_name = _get_function_name(node)
    if not func_name:
        return
    func_id = f"{module_id}:{func_name}"

    params = _extract_parameters(node)
    return_type = _extract_return_type(node)
    body = node.child_by_field_name("body")

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

    complexity = compute_complexity(branches)
    location = make_location(node, file_path)
    docstring = _extract_docstring(node)

    unit = CodeUnit(
        id=func_id,
        name=func_name,
        kind=NodeKind.FUNCTION,
        location=location,
        parameters=tuple(params),
        return_type=return_type,
        exception_types=tuple(exception_types),
        cyclomatic_complexity=complexity,
        branches=tuple(branches),
        docstring=docstring,
    )
    graph.nodes[func_id] = unit
    add_contains_edge(graph, module_id, func_id)

    _add_call_edges(calls, func_id, module_id, file_path, graph)


def _add_call_edges(
    calls: list[tuple[str, Node]],
    func_id: str,
    module_id: str,
    file_path: str,
    graph: CodeGraph,
) -> None:
    """Create CALLS edges from collected call expressions."""
    for call_name, call_node in calls:
        target_id = _resolve_call_target(call_name, module_id)
        confidence = _call_confidence(call_name)
        graph.edges.append(
            CodeEdge(
                source_id=func_id,
                target_id=target_id,
                kind=EdgeKind.CALLS,
                confidence=confidence,
                location=make_location(call_node, file_path),
            )
        )


def _call_confidence(call_name: str) -> EdgeConfidence:
    """Determine confidence for a C call expression."""
    if "." in call_name or "->" in call_name:
        return EdgeConfidence.INFERRED
    return EdgeConfidence.CERTAIN


def _resolve_call_target(
    call_name: str,
    module_id: str,
) -> str:
    """Resolve a call name to a target node ID."""
    if "." not in call_name and "->" not in call_name:
        return f"{module_id}:{call_name}"
    return call_name


def _get_function_name(node: Node) -> str:
    """Extract function name from a C function_definition.

    Handles nested declarator structures: the declarator may be
    a function_declarator, pointer_declarator, or nested
    combination thereof.
    """
    declarator = node.child_by_field_name("declarator")
    if declarator is None:
        return ""
    return _dig_identifier(declarator)


def _dig_identifier(node: Node) -> str:
    """Recursively dig through declarator nodes to find the name."""
    if node.type == "identifier":
        return node_text(node)
    if node.type == "field_identifier":
        return node_text(node)
    declarator = node.child_by_field_name("declarator")
    if declarator is not None:
        return _dig_identifier(declarator)
    for child in node.children:
        if child.type in ("identifier", "field_identifier"):
            return node_text(child)
    for child in node.children:
        result = _dig_identifier(child)
        if result:
            return result
    return ""


def _extract_parameters(node: Node) -> list[Parameter]:
    """Extract parameters from a C function definition."""
    declarator = node.child_by_field_name("declarator")
    func_decl = _find_func_declarator(declarator)
    if func_decl is None:
        return []

    params_node = func_decl.child_by_field_name("parameters")
    if params_node is None:
        return []

    params: list[Parameter] = []
    for child in params_node.children:
        if child.type == "parameter_declaration":
            param = _parse_param_declaration(child)
            if param is not None:
                params.append(param)
    return params


def _find_func_declarator(node: Node | None) -> Node | None:
    """Find the function_declarator inside a declarator chain."""
    if node is None:
        return None
    if node.type == "function_declarator":
        return node
    declarator = node.child_by_field_name("declarator")
    if declarator is not None:
        return _find_func_declarator(declarator)
    for child in node.children:
        if child.type == "function_declarator":
            return child
    return None


def _parse_param_declaration(node: Node) -> Parameter | None:
    """Parse a parameter_declaration into a Parameter."""
    type_node = node.child_by_field_name("type")
    type_name = node_text(type_node) if type_node else None
    type_ref = TypeRef(name=type_name) if type_name else None

    decl = node.child_by_field_name("declarator")
    if decl is not None:
        name = _dig_identifier(decl)
        if name:
            return Parameter(name=name, type_ref=type_ref)

    return None


def _extract_return_type(node: Node) -> TypeRef | None:
    """Extract return type from a C function definition."""
    type_node = node.child_by_field_name("type")
    if type_node is None:
        return None
    text = node_text(type_node)
    if text:
        return TypeRef(name=text)
    return None


def _extract_docstring(node: Node) -> str | None:
    """Extract a doc comment preceding a function definition.

    Looks for /** ... */ or /// style comments immediately
    before the function node.
    """
    prev = node.prev_named_sibling
    if prev is None or prev.type != "comment":
        return None
    text = node_text(prev)
    if text.startswith("/**") or text.startswith("///"):
        return _clean_c_docstring(text)
    return None


def _clean_c_docstring(text: str) -> str:
    """Strip C doc-comment delimiters."""
    if text.startswith("/**"):
        text = text[3:]
        if text.endswith("*/"):
            text = text[:-2]
    elif text.startswith("///"):
        text = text[3:]
    return text.strip()


def _extract_include(node: Node, graph: CodeGraph) -> None:
    """Extract #include directives as dependencies."""
    for child in node.children:
        if child.type in ("string_literal", "system_lib_string"):
            path = node_text(child)
            path = path.strip('"').strip("<").strip(">")
            if path and path not in graph.dependencies:
                graph.dependencies.append(path)
            return
