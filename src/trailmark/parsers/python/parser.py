"""Python language parser using tree-sitter."""

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
        "elif_clause",
        "for_statement",
        "while_statement",
        "except_clause",
        "with_statement",
        "boolean_operator",
        "conditional_expression",
        "case_clause",
    }
)

_THROW_TYPES = frozenset({"raise_statement"})

_EXTENSIONS = (".py",)


class PythonParser:
    """Parses Python source files into CodeGraph using tree-sitter."""

    @property
    def language(self) -> str:
        return "python"

    def __init__(self) -> None:
        self._parser = get_parser("python")

    def parse_file(self, file_path: str) -> CodeGraph:
        """Parse a single Python file into a CodeGraph."""
        source = Path(file_path).read_bytes()
        tree = self._parser.parse(source)
        graph = CodeGraph(language="python", root_path=file_path)
        module_id = module_id_from_path(file_path)
        _visit_module(tree.root_node, file_path, module_id, graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        """Parse all .py files under dir_path into a merged graph."""
        return parse_directory(
            self.parse_file,
            "python",
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
    if child.type == "decorated_definition":
        for sub in child.children:
            if sub.type in ("function_definition", "class_definition"):
                _visit_top_level_node(sub, file_path, module_id, graph)
    elif child.type == "function_definition":
        _extract_function(child, file_path, module_id, None, graph)
    elif child.type == "class_definition":
        _extract_class(child, file_path, module_id, graph)
    elif child.type in ("import_statement", "import_from_statement"):
        _extract_import(child, graph)


def _extract_class(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a class definition and its methods."""
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
        docstring=_extract_docstring(node),
    )
    graph.nodes[class_id] = class_unit
    add_contains_edge(graph, module_id, class_id)

    _extract_base_classes(node, class_id, module_id, graph)

    body = node.child_by_field_name("body")
    if body is None:
        return
    for child in body.children:
        actual = child
        if child.type == "decorated_definition":
            for sub in child.children:
                if sub.type == "function_definition":
                    actual = sub
                    break
            else:
                continue
        if actual.type == "function_definition":
            _extract_function(
                actual,
                file_path,
                module_id,
                class_id,
                graph,
            )


def _extract_base_classes(
    node: Node,
    class_id: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract inheritance edges from class base list."""
    arg_list = node.child_by_field_name("superclasses")
    if arg_list is None:
        return
    for child in arg_list.children:
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


def _extract_function(
    node: Node,
    file_path: str,
    module_id: str,
    class_id: str | None,
    graph: CodeGraph,
) -> None:
    """Extract a function or method definition."""
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
            "call",
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
        return_type=return_type,
        exception_types=tuple(exception_types),
        cyclomatic_complexity=complexity,
        branches=tuple(branches),
        docstring=_extract_docstring(node),
    )
    graph.nodes[func_id] = unit

    add_contains_edge(graph, container_id, func_id)

    for call_name, call_node in calls:
        target_id = _resolve_call_target(
            call_name,
            module_id,
            class_id,
        )
        confidence = (
            EdgeConfidence.CERTAIN
            if "." not in call_name or call_name.startswith("self.")
            else EdgeConfidence.INFERRED
        )
        graph.edges.append(
            CodeEdge(
                source_id=func_id,
                target_id=target_id,
                kind=EdgeKind.CALLS,
                confidence=confidence,
                location=make_location(call_node, file_path),
            )
        )


def _extract_parameters(node: Node) -> list[Parameter]:
    """Extract parameters from a function definition."""
    params_node = node.child_by_field_name("parameters")
    if params_node is None:
        return []

    params: list[Parameter] = []
    for child in params_node.children:
        if child.type == "identifier":
            name = node_text(child)
            if name != "self" and name != "cls":
                params.append(Parameter(name=name))
        elif child.type == "typed_parameter":
            name = _param_name_from_typed(child)
            type_ref = _type_from_annotation(child)
            params.append(Parameter(name=name, type_ref=type_ref))
        elif child.type == "default_parameter":
            name, default = _parse_default_param(child)
            params.append(Parameter(name=name, default=default))
        elif child.type == "typed_default_parameter":
            name, type_ref, default = _parse_typed_default(child)
            params.append(
                Parameter(
                    name=name,
                    type_ref=type_ref,
                    default=default,
                )
            )
    return params


def _param_name_from_typed(node: Node) -> str:
    """Get parameter name from a typed_parameter node."""
    for child in node.children:
        if child.type == "identifier":
            return node_text(child)
    return ""


def _type_from_annotation(node: Node) -> TypeRef | None:
    """Extract TypeRef from a typed_parameter node."""
    for child in node.children:
        if child.type == "type":
            return _parse_type_node(child)
    return None


def _parse_type_node(type_node: Node) -> TypeRef:
    """Parse a type annotation node into a TypeRef."""
    if len(type_node.children) == 1:
        inner = type_node.children[0]
        if inner.type == "identifier":
            return TypeRef(name=node_text(inner))
        if inner.type == "none":
            return TypeRef(name="None")
        if inner.type == "generic_type":
            return _parse_generic_type(inner)
        if inner.type == "union_type":
            return TypeRef(name=node_text(type_node))
    return TypeRef(name=node_text(type_node))


def _parse_generic_type(node: Node) -> TypeRef:
    """Parse a generic type like list[int] into a TypeRef."""
    name = ""
    args: list[TypeRef] = []
    for child in node.children:
        if child.type == "identifier":
            name = node_text(child)
        elif child.type == "type_parameter":
            for sub in child.children:
                if sub.type == "type":
                    args.append(_parse_type_node(sub))
    return TypeRef(name=name, generic_args=tuple(args))


def _parse_default_param(node: Node) -> tuple[str, str]:
    """Parse name and default from a default_parameter node."""
    name = ""
    default = ""
    for child in node.children:
        if child.type == "identifier" and not name:
            name = node_text(child)
        elif child.type == "=":
            continue
        elif name:
            default = node_text(child)
    return name, default


def _parse_typed_default(
    node: Node,
) -> tuple[str, TypeRef | None, str]:
    """Parse name, type, default from typed_default_parameter."""
    name = ""
    type_ref = None
    default = ""
    seen_eq = False
    for child in node.children:
        if child.type == "identifier" and not name:
            name = node_text(child)
        elif child.type == "type":
            type_ref = _parse_type_node(child)
        elif child.type == "=":
            seen_eq = True
        elif seen_eq:
            default = node_text(child)
    return name, type_ref, default


def _extract_return_type(node: Node) -> TypeRef | None:
    """Extract return type annotation from a function definition."""
    return_type = node.child_by_field_name("return_type")
    if return_type is None:
        return None
    return _parse_type_node(return_type)


def _extract_docstring(node: Node) -> str | None:
    """Extract docstring from a function or class definition."""
    body = node.child_by_field_name("body")
    if body is None or len(body.children) == 0:
        return None

    first = body.children[0]
    if first.type == "expression_statement":
        expr = first.children[0] if first.children else None
        if expr is not None and expr.type == "string":
            return _clean_docstring(node_text(expr))
    if first.type == "string":
        return _clean_docstring(node_text(first))
    return None


def _clean_docstring(text: str) -> str:
    """Strip quote delimiters from a docstring."""
    if text.startswith('"""') or text.startswith("'''"):
        text = text[3:-3]
    elif text.startswith('"') or text.startswith("'"):
        text = text[1:-1]
    return text.strip()


def _resolve_call_target(
    call_name: str,
    module_id: str,
    class_id: str | None,
) -> str:
    """Resolve a call name to a target node ID (best effort)."""
    if call_name.startswith("self.") and class_id is not None:
        method_name = call_name[5:]
        return f"{class_id}.{method_name}"
    if "." not in call_name:
        return f"{module_id}:{call_name}"
    return call_name


def _extract_import(node: Node, graph: CodeGraph) -> None:
    """Extract import statements as dependency info."""
    text = node_text(node)
    parts = text.split()
    if len(parts) >= 2:
        module_name = parts[1].split(".")[0]
        if module_name not in graph.dependencies:
            graph.dependencies.append(module_name)
