"""C++ language parser using tree-sitter."""

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
from trailmark.parsers._binding_index import binding_scope, units_at
from trailmark.parsers._bindings import guard_call
from trailmark.parsers._common import (
    add_contains_edge,
    add_module_node,
    collect_body_info,
    compute_complexity,
    extract_type_parameters,
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
    resolve_local_method_calls,
)
from trailmark.parsers._walk import walk_owned

_BRANCH_NODE_TYPES = frozenset(
    {
        "if_statement",
        "for_statement",
        "while_statement",
        "do_statement",
        "case_statement",
        "conditional_expression",
        "catch_clause",
    }
)

_THROW_TYPES = frozenset({"throw_statement"})

_EXTENSIONS = (".cpp", ".hpp", ".cc", ".hh", ".cxx", ".hxx")

_CPP_TYPE_NODES = frozenset(
    {
        "type_identifier",
        "primitive_type",
        "sized_type_specifier",
        "template_type",
    }
)


class CppParser:
    """Parses C++ source files into CodeGraph using tree-sitter."""

    @property
    def language(self) -> str:
        return "cpp"

    def __init__(self) -> None:
        self._parser = Parser(get_language("cpp"))

    @binding_scope
    def parse_file(self, file_path: str) -> CodeGraph:
        """Parse a single C++ file into a CodeGraph."""
        source = Path(file_path).read_bytes()
        tree = self._parser.parse(source)
        graph = CodeGraph(language="cpp", root_path=file_path)
        module_id = module_id_from_path(file_path)
        with callable_scope(graph):
            _visit_module(tree.root_node, file_path, module_id, graph)
        resolve_local_method_calls(graph)
        bind_lexical_calls(graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        """Parse all C++ files under dir_path into a merged graph."""
        return parse_directory(
            self.parse_file,
            "cpp",
            dir_path,
            _EXTENSIONS,
        )


def _visit_module(
    root: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Walk the top-level of a C++ module."""
    add_module_node(root, file_path, module_id, graph)
    for child in root.children:
        _visit_top_level_node(
            child,
            file_path,
            module_id,
            None,
            graph,
        )


def _visit_top_level_node(
    child: Node,
    file_path: str,
    module_id: str,
    class_id: str | None,
    graph: CodeGraph,
) -> None:
    """Dispatch a single top-level C++ node."""
    if child.type == "function_definition":
        _extract_function(
            child,
            file_path,
            module_id,
            class_id,
            graph,
        )
    elif child.type == "preproc_include":
        _extract_include(child, graph)
    elif child.type in ("type_definition", "declaration"):
        _extract_type_def(
            child,
            file_path,
            module_id,
            graph,
        )
    elif child.type in (
        "linkage_specification",
        "declaration_list",
        "preproc_if",
        "preproc_ifdef",
        "preproc_else",
        "preproc_elif",
        "ERROR",
    ):
        for nested in child.children:
            _visit_top_level_node(nested, file_path, module_id, class_id, graph)
    elif child.type in _TYPE_EXTRACTORS:
        _TYPE_EXTRACTORS[child.type](child, file_path, module_id, graph)
    elif child.type == "template_declaration":
        _visit_template_body(
            child,
            file_path,
            module_id,
            class_id,
            graph,
        )


def _visit_template_body(
    node: Node,
    file_path: str,
    module_id: str,
    class_id: str | None,
    graph: CodeGraph,
) -> None:
    """Visit the body of a template_declaration."""
    for child in node.children:
        if child.type == "function_definition":
            _extract_function(child, file_path, module_id, class_id, graph, node)
        elif child.type == "class_specifier":
            _extract_class(child, file_path, module_id, graph, node)
        elif child.type == "struct_specifier":
            _extract_struct(child, file_path, module_id, graph, node)
        elif child.type in ("declaration", "type_definition"):
            _visit_top_level_node(
                child,
                file_path,
                module_id,
                class_id,
                graph,
            )


def _owned_id(owner: str, name: str) -> str:
    return f"{owner}.{name}" if ":" in owner else f"{owner}:{name}"


def _owner(node: Node, module: str, graph: CodeGraph) -> str:
    ancestor = node.parent
    while ancestor is not None:
        if ancestor.type in {
            "namespace_definition",
            "class_specifier",
            "struct_specifier",
            "function_definition",
            "lambda_expression",
        }:
            candidates = [
                unit.id
                for unit in units_at(ancestor, graph.root_path)
                if unit.kind
                in {
                    NodeKind.NAMESPACE,
                    NodeKind.CLASS,
                    NodeKind.STRUCT,
                    NodeKind.METHOD,
                    NodeKind.FUNCTION,
                }
            ]
            if len(candidates) == 1:
                return candidates[0]
            if ancestor.type == "namespace_definition":
                name = ancestor.child_by_field_name("name")
                return _owned_id(
                    _owner(ancestor, module, graph),
                    node_text(name).replace("::", ".") if name else "$anonymous",
                )
        ancestor = ancestor.parent
    return module


def _extract_namespace(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a namespace definition and its contents."""
    name_node = node.child_by_field_name("name")
    ns_name = node_text(name_node).replace("::", ".") if name_node is not None else "$anonymous"
    owner = _owner(node, module_id, graph)
    ns_id = _owned_id(owner, ns_name)
    location = make_location(node, file_path)

    unit = CodeUnit(
        id=ns_id,
        name=ns_name,
        kind=NodeKind.NAMESPACE,
        location=location,
    )
    if ns_id not in graph.nodes:
        add_type(graph, unit)
        add_contains_edge(graph, owner, ns_id)

    body = node.child_by_field_name("body")
    if body is None:
        return
    for child in body.children:
        _visit_top_level_node(
            child,
            file_path,
            module_id,
            None,
            graph,
        )


def _extract_type_def(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract class, struct, or enum from a declaration."""
    for child in node.children:
        if child.type == "class_specifier":
            _extract_class(child, file_path, module_id, graph)
        elif child.type == "struct_specifier":
            _extract_struct(child, file_path, module_id, graph)
        elif child.type == "enum_specifier":
            _extract_enum(child, file_path, module_id, graph)


def _extract_class(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
    generic_node: Node | None = None,
) -> None:
    """Extract a class definition and its methods."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    class_name = node_text(name_node)
    owner = _owner(node, module_id, graph)
    class_id = _owned_id(owner, class_name)
    location = make_location(node, file_path)

    unit = CodeUnit(
        id=class_id,
        name=class_name,
        kind=NodeKind.CLASS,
        location=location,
        type_parameters=extract_type_parameters(generic_node or node),
        docstring=_extract_docstring(node),
    )
    add_type(graph, unit)
    add_contains_edge(graph, owner, class_id)

    _extract_base_classes(node, class_id, module_id, graph)
    _visit_class_body(node, file_path, module_id, class_id, graph)


def _extract_base_classes(
    node: Node,
    class_id: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract INHERITS edges from base_class_clause."""
    for child in node.children:
        if child.type == "base_class_clause":
            _process_base_clause(
                child,
                class_id,
                module_id,
                graph,
            )
            return


def _process_base_clause(
    clause: Node,
    class_id: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Process base_class_clause children for base names."""
    for child in clause.children:
        if child.type == "type_identifier":
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


def _visit_class_body(
    node: Node,
    file_path: str,
    module_id: str,
    class_id: str,
    graph: CodeGraph,
) -> None:
    """Visit children of a class body for methods."""
    body = node.child_by_field_name("body")
    if body is None:
        return
    for child in body.children:
        if child.type == "function_definition":
            _extract_function(
                child,
                file_path,
                module_id,
                class_id,
                graph,
            )
        elif child.type == "declaration":
            _check_inline_method(
                child,
                file_path,
                module_id,
                class_id,
                graph,
            )
        elif child.type in {
            "class_specifier",
            "struct_specifier",
            "enum_specifier",
            "field_declaration",
        }:
            _visit_top_level_node(child, file_path, module_id, class_id, graph)
            if child.type == "field_declaration":
                _extract_type_def(child, file_path, module_id, graph)
        elif child.type == "access_specifier":
            continue


def _check_inline_method(
    node: Node,
    file_path: str,
    module_id: str,
    class_id: str,
    graph: CodeGraph,
) -> None:
    """Check if a declaration contains an inline method."""
    for child in node.children:
        if child.type == "function_definition":
            _extract_function(
                child,
                file_path,
                module_id,
                class_id,
                graph,
            )


def _extract_struct(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
    generic_node: Node | None = None,
) -> None:
    """Extract a struct definition."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    struct_name = node_text(name_node)
    owner = _owner(node, module_id, graph)
    struct_id = _owned_id(owner, struct_name)
    location = make_location(node, file_path)

    unit = CodeUnit(
        id=struct_id,
        name=struct_name,
        kind=NodeKind.STRUCT,
        location=location,
        type_parameters=extract_type_parameters(generic_node or node),
    )
    add_type(graph, unit)
    add_contains_edge(graph, owner, struct_id)
    _visit_class_body(node, file_path, module_id, struct_id, graph)


def _extract_enum(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract an enum definition."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    enum_name = node_text(name_node)
    owner = _owner(node, module_id, graph)
    enum_id = _owned_id(owner, enum_name)
    location = make_location(node, file_path)

    unit = CodeUnit(
        id=enum_id,
        name=enum_name,
        kind=NodeKind.ENUM,
        location=location,
    )
    add_type(graph, unit)
    add_contains_edge(graph, owner, enum_id)


def _extract_function(
    node: Node,
    file_path: str,
    module_id: str,
    class_id: str | None,
    graph: CodeGraph,
    generic_node: Node | None = None,
    *,
    name_override: str | None = None,
) -> None:
    """Extract a C++ function or method definition."""
    func_name = name_override or _get_function_name(node)
    if not func_name:
        return

    if class_id is not None:
        func_id = f"{class_id}.{func_name}"
        kind = NodeKind.FUNCTION if node.type == "lambda_expression" else NodeKind.METHOD
        container_id = class_id
    else:
        container_id = _owner(node, module_id, graph)
        func_id = _owned_id(container_id, func_name)
        kind = NodeKind.FUNCTION

    unit, calls = _function_unit(
        node, file_path, func_id=func_id, func_name=func_name, kind=kind, generic_node=generic_node
    )
    body = node.child_by_field_name("body")
    branches, exception_types = list(unit.branches), list(unit.exception_types)
    func_id = add_callable(graph, unit)
    add_contains_edge(graph, container_id, func_id)

    _extract_deferred_callables(
        body,
        file_path,
        module_id,
        func_id,
        graph,
        branches=branches,
        exception_types=exception_types,
        calls=calls,
    )
    graph.nodes[func_id] = replace(
        graph.nodes[func_id],
        branches=tuple(branches),
        exception_types=tuple(exception_types),
        cyclomatic_complexity=compute_complexity(branches),
    )
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
        target_id = _resolve_call_target(
            call_name,
            module_id,
        )
        confidence = _call_confidence(call_name)
        graph.edges.append(
            guard_call(
                CodeEdge(
                    source_id=func_id,
                    target_id=target_id,
                    kind=EdgeKind.CALLS,
                    confidence=confidence,
                    location=make_location(call_node, file_path),
                    attributes=(("bare_name", call_name),)
                    if re.fullmatch(r"[A-Za-z_]\w*", call_name)
                    else (),
                ),
                call_node,
                graph=graph,
            )
        )


def _call_confidence(call_name: str) -> EdgeConfidence:
    """Determine confidence for a C++ call expression."""
    if "." in call_name or "->" in call_name or "::" in call_name:
        return EdgeConfidence.INFERRED
    return EdgeConfidence.CERTAIN


def _resolve_call_target(
    call_name: str,
    module_id: str,
) -> str:
    """Construct an initial target; container binding follows the complete file walk."""
    if "::" in call_name or "." in call_name or "->" in call_name:
        return call_name
    return f"{module_id}:{call_name}"


def _get_function_name(node: Node) -> str:
    """Extract function name from a C++ function_definition.

    Handles nested declarator structures including
    function_declarator, pointer_declarator, and
    reference_declarator.
    """
    declarator = node.child_by_field_name("declarator")
    if declarator is None:
        return ""
    return _dig_identifier(declarator)


def _dig_identifier(node: Node) -> str:
    """Recursively dig through declarator nodes for the name."""
    if node.type == "identifier":
        return node_text(node)
    if node.type == "field_identifier":
        return node_text(node)
    if node.type in ("qualified_identifier", "scoped_identifier"):
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
    """Extract parameters from a C++ function definition."""
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
    if node.type in {"function_declarator", "lambda_declarator"}:
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
    """Extract return type from a C++ function definition."""
    type_node = node.child_by_field_name("type")
    if type_node is None:
        return None
    text = node_text(type_node)
    if text:
        return TypeRef(name=text)
    return None


def _extract_docstring(node: Node) -> str | None:
    """Extract a doc comment preceding a function or class.

    Looks for /** ... */ or /// style comments immediately
    before the node.
    """
    prev = node.prev_named_sibling
    if prev is None or prev.type != "comment":
        return None
    text = node_text(prev)
    if text.startswith("/**") or text.startswith("///"):
        return _clean_cpp_docstring(text)
    return None


def _clean_cpp_docstring(text: str) -> str:
    """Strip C++ doc-comment delimiters."""
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


def _extract_deferred_callables(
    body: Node | None,
    file_path: str,
    module_id: str,
    func_id: str,
    graph: CodeGraph,
    *,
    branches: list[BranchInfo],
    exception_types: list[TypeRef],
    calls: list[tuple[str, Node]],
) -> None:
    if body is not None:
        for deferred in walk_owned(
            body,
            boundaries=frozenset(
                {"lambda_expression", "class_specifier", "struct_specifier", "function_definition"}
            ),
        ):
            if deferred.type == "lambda_expression":
                _extract_function(
                    deferred,
                    file_path,
                    module_id,
                    func_id,
                    graph,
                    name_override=(
                        f"$lambda@{deferred.start_point.row + 1}_{deferred.start_point.column}"
                    ),
                )
                _collect_captures(deferred, file_path, branches, exception_types, calls)
                if (
                    deferred.parent is not None
                    and deferred.parent.type == "call_expression"
                    and deferred.parent.child_by_field_name("function") == deferred
                ):
                    calls.append(
                        (
                            f"{func_id}.$lambda@{deferred.start_point.row + 1}_"
                            f"{deferred.start_point.column}",
                            deferred.parent,
                        )
                    )
            elif deferred.type in {"class_specifier", "struct_specifier"}:
                _visit_top_level_node(deferred, file_path, module_id, None, graph)


def _function_unit(
    node: Node,
    file_path: str,
    *,
    func_id: str,
    func_name: str,
    kind: NodeKind,
    generic_node: Node | None,
) -> tuple[CodeUnit, list[tuple[str, Node]]]:
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
            skip_types=frozenset(
                {"lambda_expression", "class_specifier", "struct_specifier", "function_definition"}
            ),
        )

    complexity = compute_complexity(branches)
    location = make_location(node, file_path)
    docstring = _extract_docstring(node)

    unit = CodeUnit(
        id=func_id,
        name=func_name,
        kind=kind,
        location=location,
        parameters=tuple(_extract_parameters(node)),
        return_type=_extract_return_type(node),
        exception_types=tuple(exception_types),
        type_parameters=extract_type_parameters(generic_node or node),
        cyclomatic_complexity=complexity,
        branches=tuple(branches),
        docstring=docstring,
        attributes=(("internal_linkage", _internal_linkage(node, func_id)),),
    )
    return unit, calls


_TYPE_EXTRACTORS = {
    "namespace_definition": _extract_namespace,
    "class_specifier": _extract_class,
    "struct_specifier": _extract_struct,
    "enum_specifier": _extract_enum,
}


def _internal_linkage(node: Node, func_id: str) -> bool:
    return (
        ".$anonymous" in func_id
        or ":$anonymous" in func_id
        or any(
            c.type == "storage_class_specifier" and node_text(c) == "static"
            for c in node.named_children
        )
    )


def _collect_captures(
    deferred: Node,
    file_path: str,
    branches: list[BranchInfo],
    exception_types: list[TypeRef],
    calls: list[tuple[str, Node]],
) -> None:
    captures = deferred.child_by_field_name("captures")
    if captures is not None:
        collect_body_info(
            captures,
            file_path,
            _BRANCH_NODE_TYPES,
            "call_expression",
            _THROW_TYPES,
            branches,
            exception_types,
            calls,
        )
