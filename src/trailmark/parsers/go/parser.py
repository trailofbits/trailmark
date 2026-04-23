"""Go language parser using tree-sitter."""

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
        "expression_switch_statement",
        "type_switch_statement",
        "select_statement",
        "expression_case",
        "type_case",
        "default_case",
        "communication_case",
    }
)

_THROW_TYPES: frozenset[str] = frozenset()

_EXTENSIONS = (".go",)

_GO_TYPE_NODES = frozenset(
    {
        "type_identifier",
        "pointer_type",
        "slice_type",
        "map_type",
        "array_type",
        "qualified_type",
    }
)


class GoParser:
    """Parses Go source files into CodeGraph using tree-sitter."""

    @property
    def language(self) -> str:
        return "go"

    def __init__(self) -> None:
        self._parser = get_parser("go")

    def parse_file(self, file_path: str) -> CodeGraph:
        """Parse a single Go file into a CodeGraph."""
        source = Path(file_path).read_bytes()
        tree = self._parser.parse(source)
        graph = CodeGraph(language="go", root_path=file_path)
        module_id = module_id_from_path(file_path)
        _visit_module(tree.root_node, file_path, module_id, graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        """Parse all .go files under dir_path into a merged graph."""
        return parse_directory(
            self.parse_file,
            "go",
            dir_path,
            _EXTENSIONS,
        )


def _visit_module(
    root: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Walk the top-level of a module, extracting nodes and edges."""
    add_module_node(root, file_path, module_id, graph)
    for child in root.children:
        _visit_top_level_node(child, file_path, module_id, graph)


def _visit_top_level_node(
    child: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Dispatch a single top-level node."""
    if child.type == "function_declaration":
        _extract_function(child, file_path, module_id, graph)
    elif child.type == "method_declaration":
        _extract_method(child, file_path, module_id, graph)
    elif child.type == "type_declaration":
        _extract_type_declaration(child, file_path, module_id, graph)
    elif child.type == "import_declaration":
        _extract_import(child, graph)


def _extract_type_declaration(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract struct or interface from a type_declaration."""
    for child in node.children:
        if child.type == "type_spec":
            _extract_type_spec(
                child,
                node,
                file_path,
                module_id,
                graph,
            )


def _extract_type_spec(
    spec: Node,
    decl: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a single type_spec (struct or interface)."""
    name_node = spec.child_by_field_name("name")
    type_node = spec.child_by_field_name("type")
    if name_node is None or type_node is None:
        return

    type_name = node_text(name_node)
    type_id = f"{module_id}:{type_name}"
    kind = _kind_from_type_node(type_node)
    if kind is None:
        return

    docstring = _extract_docstring(decl)
    unit = CodeUnit(
        id=type_id,
        name=type_name,
        kind=kind,
        location=make_location(spec, file_path),
        docstring=docstring,
    )
    graph.nodes[type_id] = unit
    add_contains_edge(graph, module_id, type_id)


def _kind_from_type_node(type_node: Node) -> NodeKind | None:
    """Map a Go type node to a NodeKind."""
    if type_node.type == "struct_type":
        return NodeKind.STRUCT
    if type_node.type == "interface_type":
        return NodeKind.INTERFACE
    return None


def _extract_function(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a standalone function declaration."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    func_name = node_text(name_node)
    func_id = f"{module_id}:{func_name}"

    params = _extract_parameters(node)
    return_type = _extract_return_type(node)
    body = node.child_by_field_name("body")

    branches, exception_types, calls = _collect_func_body(
        body,
        file_path,
    )
    complexity = compute_complexity(branches)
    docstring = _extract_docstring(node)

    unit = CodeUnit(
        id=func_id,
        name=func_name,
        kind=NodeKind.FUNCTION,
        location=make_location(node, file_path),
        parameters=tuple(params),
        return_type=return_type,
        exception_types=tuple(exception_types),
        cyclomatic_complexity=complexity,
        branches=tuple(branches),
        docstring=docstring,
    )
    graph.nodes[func_id] = unit
    add_contains_edge(graph, module_id, func_id)

    _add_call_edges(
        calls,
        func_id,
        module_id,
        None,
        file_path,
        graph,
    )


def _extract_method(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a method declaration (has a receiver)."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    method_name = node_text(name_node)
    receiver_type = _extract_receiver_type(node)

    type_id = f"{module_id}:{receiver_type}" if receiver_type else module_id
    method_id = f"{type_id}.{method_name}"

    params = _extract_parameters(node)
    return_type = _extract_return_type(node)
    body = node.child_by_field_name("body")

    branches, exception_types, calls = _collect_func_body(
        body,
        file_path,
    )
    complexity = compute_complexity(branches)
    docstring = _extract_docstring(node)

    unit = CodeUnit(
        id=method_id,
        name=method_name,
        kind=NodeKind.METHOD,
        location=make_location(node, file_path),
        parameters=tuple(params),
        return_type=return_type,
        exception_types=tuple(exception_types),
        cyclomatic_complexity=complexity,
        branches=tuple(branches),
        docstring=docstring,
    )
    graph.nodes[method_id] = unit
    add_contains_edge(graph, type_id, method_id)

    _add_call_edges(
        calls,
        method_id,
        module_id,
        type_id,
        file_path,
        graph,
    )


def _extract_receiver_type(node: Node) -> str:
    """Extract the receiver type name from a method declaration."""
    receiver = node.child_by_field_name("receiver")
    if receiver is None:
        return ""
    return _find_type_identifier(receiver)


def _find_type_identifier(node: Node) -> str:
    """Recursively find a type_identifier in a receiver node."""
    if node.type == "type_identifier":
        return node_text(node)
    for child in node.children:
        result = _find_type_identifier(child)
        if result:
            return result
    return ""


def _collect_func_body(
    body: Node | None,
    file_path: str,
) -> tuple[list[BranchInfo], list[TypeRef], list[tuple[str, Node]]]:
    """Collect branches, exceptions, and calls from a function body."""
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


def _extract_parameters(node: Node) -> list[Parameter]:
    """Extract parameters from a function/method declaration."""
    params_node = node.child_by_field_name("parameters")
    if params_node is None:
        return []
    params: list[Parameter] = []
    for child in params_node.children:
        if child.type == "parameter_declaration":
            _extract_single_param(child, params)
    return params


def _extract_single_param(
    decl: Node,
    params: list[Parameter],
) -> None:
    """Extract name and type from a parameter_declaration."""
    name = ""
    type_ref = None
    for child in decl.children:
        if child.type == "identifier" and not name:
            name = node_text(child)
        elif child.type in _GO_TYPE_NODES:
            type_ref = TypeRef(name=node_text(child))
    if name:
        params.append(Parameter(name=name, type_ref=type_ref))


def _extract_return_type(node: Node) -> TypeRef | None:
    """Extract the return type from a function declaration."""
    result = node.child_by_field_name("result")
    if result is None:
        return None
    if result.type == "parameter_list":
        return TypeRef(name=node_text(result))
    return TypeRef(name=node_text(result))


def _extract_docstring(node: Node) -> str | None:
    """Extract a doc comment immediately preceding the node."""
    prev = node.prev_named_sibling
    if prev is not None and prev.type == "comment":
        text = node_text(prev)
        return _clean_go_comment(text)
    return None


def _clean_go_comment(text: str) -> str:
    """Strip comment markers from a Go comment."""
    if text.startswith("//"):
        return text[2:].strip()
    if text.startswith("/*") and text.endswith("*/"):
        return text[2:-2].strip()
    return text.strip()


def _add_call_edges(
    calls: list[tuple[str, Node]],
    source_id: str,
    module_id: str,
    type_id: str | None,
    file_path: str,
    graph: CodeGraph,
) -> None:
    """Add CALLS edges for all collected call expressions."""
    for call_name, call_node in calls:
        target_id = _resolve_call_target(
            call_name,
            module_id,
            type_id,
        )
        confidence = _call_confidence(call_name)
        graph.edges.append(
            CodeEdge(
                source_id=source_id,
                target_id=target_id,
                kind=EdgeKind.CALLS,
                confidence=confidence,
                location=make_location(call_node, file_path),
            )
        )


def _call_confidence(call_name: str) -> EdgeConfidence:
    """Determine confidence for a Go call expression."""
    if "." in call_name:
        return EdgeConfidence.INFERRED
    return EdgeConfidence.CERTAIN


def _resolve_call_target(
    call_name: str,
    module_id: str,
    type_id: str | None,
) -> str:
    """Resolve a call name to a target node ID."""
    if "." not in call_name:
        return f"{module_id}:{call_name}"
    return call_name


def _extract_import(node: Node, graph: CodeGraph) -> None:
    """Extract import declarations as dependency info."""
    for child in node.children:
        if child.type == "import_spec":
            _extract_import_spec(child, graph)
        elif child.type == "import_spec_list":
            for spec in child.children:
                if spec.type == "import_spec":
                    _extract_import_spec(spec, graph)


def _extract_import_spec(spec: Node, graph: CodeGraph) -> None:
    """Extract a single import spec path."""
    path_node = spec.child_by_field_name("path")
    if path_node is None:
        return
    raw = node_text(path_node).strip('"')
    dep = raw.split("/")[-1]
    if dep and dep not in graph.dependencies:
        graph.dependencies.append(dep)
