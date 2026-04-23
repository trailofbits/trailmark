"""JavaScript language parser using tree-sitter."""

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
        "for_in_statement",
        "while_statement",
        "do_statement",
        "switch_case",
        "catch_clause",
        "ternary_expression",
    }
)

_THROW_TYPES = frozenset({"throw_statement"})

_FUNCTION_DECL_TYPES = frozenset({"function_declaration", "generator_function_declaration"})

_FUNCTION_EXPR_TYPES = frozenset({"arrow_function", "function", "function_expression"})

_EXTENSIONS = (".js", ".jsx", ".mjs", ".cjs")


class JavaScriptParser:
    """Parses JavaScript source files into CodeGraph."""

    @property
    def language(self) -> str:
        return "javascript"

    def __init__(self) -> None:
        self._parser = get_parser("javascript")

    def parse_file(self, file_path: str) -> CodeGraph:
        """Parse a single JavaScript file into a CodeGraph."""
        source = Path(file_path).read_bytes()
        tree = self._parser.parse(source)
        graph = CodeGraph(language="javascript", root_path=file_path)
        module_id = module_id_from_path(file_path)
        _visit_module(tree.root_node, file_path, module_id, graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        """Parse all supported JavaScript files under ``dir_path``."""
        return parse_directory(
            self.parse_file,
            "javascript",
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
    if child.type in _FUNCTION_DECL_TYPES:
        _extract_function(child, file_path, module_id, None, graph)
    elif child.type == "class_declaration":
        _extract_class(child, file_path, module_id, graph)
    elif child.type == "import_statement":
        _extract_import(child, graph)
    elif child.type == "export_statement":
        _visit_export(child, file_path, module_id, graph)
    elif child.type in (
        "lexical_declaration",
        "variable_declaration",
    ):
        _visit_lexical_decl(child, file_path, module_id, graph)
    elif child.type == "expression_statement":
        _visit_expression_stmt(child, file_path, module_id, graph)


def _visit_export(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Handle export statements that wrap declarations."""
    for child in node.children:
        _visit_top_level_node(child, file_path, module_id, graph)


def _visit_lexical_decl(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Handle const/let/var declarations that may assign functions."""
    for child in node.children:
        if child.type == "variable_declarator":
            _try_extract_func_from_declarator(
                child,
                file_path,
                module_id,
                graph,
            )


def _visit_expression_stmt(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Handle expression statements containing assignments."""
    for child in node.children:
        if child.type == "assignment_expression":
            _try_extract_func_from_assignment(
                child,
                file_path,
                module_id,
                graph,
            )


def _try_extract_func_from_declarator(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a function from `const name = () => ...`."""
    name_node = node.child_by_field_name("name")
    value_node = node.child_by_field_name("value")
    if name_node is None or value_node is None:
        return
    if value_node.type in _FUNCTION_EXPR_TYPES:
        _extract_function_expr(
            value_node,
            name_node,
            file_path,
            module_id,
            graph,
        )


def _try_extract_func_from_assignment(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a function from `name = function() {...}`."""
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    if left is None or right is None:
        return
    if left.type == "identifier" and right.type in _FUNCTION_EXPR_TYPES:
        _extract_function_expr(
            right,
            left,
            file_path,
            module_id,
            graph,
        )


def _extract_function_expr(
    func_node: Node,
    name_node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a function expression assigned to a named variable."""
    func_name = node_text(name_node)
    func_id = f"{module_id}:{func_name}"
    params = _extract_parameters(func_node)
    body = func_node.child_by_field_name("body")

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
    location = make_location(func_node, file_path)

    unit = CodeUnit(
        id=func_id,
        name=func_name,
        kind=NodeKind.FUNCTION,
        location=location,
        parameters=tuple(params),
        cyclomatic_complexity=complexity,
        branches=tuple(branches),
        exception_types=tuple(exception_types),
        docstring=_extract_jsdoc(func_node),
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


def _extract_class(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a class declaration and its methods."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    class_name = node_text(name_node)
    class_id = f"{module_id}:{class_name}"
    location = make_location(node, file_path)

    class_unit = CodeUnit(
        id=class_id,
        name=class_name,
        kind=NodeKind.CLASS,
        location=location,
        docstring=_extract_jsdoc(node),
    )
    graph.nodes[class_id] = class_unit
    add_contains_edge(graph, module_id, class_id)

    _extract_heritage(node, class_id, module_id, graph)

    body = node.child_by_field_name("body")
    if body is None:
        return
    for child in body.children:
        if child.type == "method_definition":
            _extract_method(
                child,
                file_path,
                module_id,
                class_id,
                graph,
            )


def _extract_heritage(
    node: Node,
    class_id: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract inheritance from class_heritage."""
    for child in node.children:
        if child.type == "class_heritage":
            _add_inherits_from_heritage(
                child,
                class_id,
                module_id,
                graph,
            )


def _add_inherits_from_heritage(
    heritage: Node,
    class_id: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Add INHERITS edges from a class_heritage node."""
    for child in heritage.children:
        if child.type == "identifier":
            base_name = node_text(child)
            base_id = f"{module_id}:{base_name}"
            graph.edges.append(
                CodeEdge(
                    source_id=class_id,
                    target_id=base_id,
                    kind=EdgeKind.INHERITS,
                    confidence=EdgeConfidence.INFERRED,
                )
            )


def _extract_method(
    node: Node,
    file_path: str,
    module_id: str,
    class_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a method definition from a class body."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    method_name = node_text(name_node)
    method_id = f"{class_id}.{method_name}"

    params = _extract_parameters(node)
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

    unit = CodeUnit(
        id=method_id,
        name=method_name,
        kind=NodeKind.METHOD,
        location=location,
        parameters=tuple(params),
        cyclomatic_complexity=complexity,
        branches=tuple(branches),
        exception_types=tuple(exception_types),
        docstring=_extract_jsdoc(node),
    )
    graph.nodes[method_id] = unit
    add_contains_edge(graph, class_id, method_id)

    _add_call_edges(
        calls,
        method_id,
        module_id,
        class_id,
        file_path,
        graph,
    )


def _extract_function(
    node: Node,
    file_path: str,
    module_id: str,
    class_id: str | None,
    graph: CodeGraph,
) -> None:
    """Extract a function or generator function declaration."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    func_name = node_text(name_node)

    if class_id is not None:
        func_id = f"{class_id}.{func_name}"
        kind = NodeKind.METHOD
        container_id = class_id
    else:
        func_id = f"{module_id}:{func_name}"
        kind = NodeKind.FUNCTION
        container_id = module_id

    params = _extract_parameters(node)
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

    unit = CodeUnit(
        id=func_id,
        name=func_name,
        kind=kind,
        location=location,
        parameters=tuple(params),
        cyclomatic_complexity=complexity,
        branches=tuple(branches),
        exception_types=tuple(exception_types),
        docstring=_extract_jsdoc(node),
    )
    graph.nodes[func_id] = unit
    add_contains_edge(graph, container_id, func_id)

    _add_call_edges(
        calls,
        func_id,
        module_id,
        class_id,
        file_path,
        graph,
    )


def _extract_parameters(node: Node) -> list[Parameter]:
    """Extract parameters from a function or method."""
    params_node = node.child_by_field_name("parameters")
    if params_node is None:
        return []

    params: list[Parameter] = []
    for child in params_node.children:
        if child.type == "identifier":
            params.append(Parameter(name=node_text(child)))
        elif child.type == "assignment_pattern":
            name, default = _parse_assignment_pattern(child)
            params.append(Parameter(name=name, default=default))
        elif child.type == "rest_pattern":
            _append_rest_param(child, params)
    return params


def _parse_assignment_pattern(
    node: Node,
) -> tuple[str, str]:
    """Parse name and default from an assignment_pattern node."""
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    name = node_text(left) if left else ""
    default = node_text(right) if right else ""
    return name, default


def _append_rest_param(
    node: Node,
    params: list[Parameter],
) -> None:
    """Append a rest parameter (...args) to the params list."""
    for child in node.children:
        if child.type == "identifier":
            params.append(Parameter(name=f"...{node_text(child)}"))


def _extract_jsdoc(node: Node) -> str | None:
    """Extract JSDoc comment preceding a definition node."""
    prev = node.prev_named_sibling
    if prev is not None and prev.type == "comment":
        text = node_text(prev)
        if text.startswith("/**"):
            return _clean_jsdoc(text)
    return None


def _clean_jsdoc(text: str) -> str:
    """Strip JSDoc delimiters and leading asterisks."""
    if text.startswith("/**"):
        text = text[3:]
    if text.endswith("*/"):
        text = text[:-2]
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("* "):
            stripped = stripped[2:]
        elif stripped == "*":
            stripped = ""
        cleaned.append(stripped)
    return "\n".join(cleaned).strip()


def _add_call_edges(
    calls: list[tuple[str, Node]],
    source_id: str,
    module_id: str,
    class_id: str | None,
    file_path: str,
    graph: CodeGraph,
) -> None:
    """Add CALLS edges from collected call information."""
    for call_name, call_node in calls:
        target_id = _resolve_call_target(
            call_name,
            module_id,
            class_id,
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
    """Determine confidence level for a call."""
    if "." not in call_name:
        return EdgeConfidence.CERTAIN
    if call_name.startswith("this."):
        return EdgeConfidence.CERTAIN
    return EdgeConfidence.INFERRED


def _resolve_call_target(
    call_name: str,
    module_id: str,
    class_id: str | None,
) -> str:
    """Resolve a call name to a target node ID (best effort)."""
    if call_name.startswith("this.") and class_id is not None:
        method_name = call_name[5:]
        return f"{class_id}.{method_name}"
    if "." not in call_name:
        return f"{module_id}:{call_name}"
    return call_name


def _extract_import(node: Node, graph: CodeGraph) -> None:
    """Extract import statements as dependency info."""
    for child in node.children:
        if child.type == "string":
            module_name = _strip_quotes(node_text(child))
            _add_dependency(module_name, graph)
            return


def _strip_quotes(text: str) -> str:
    """Remove surrounding quotes from a string literal."""
    if len(text) >= 2 and text[0] in ('"', "'", "`"):
        return text[1:-1]
    return text


def _add_dependency(module_name: str, graph: CodeGraph) -> None:
    """Add a module dependency if not already present."""
    base = module_name.split("/")[0]
    if base and base not in graph.dependencies:
        graph.dependencies.append(base)
