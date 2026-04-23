"""Objective-C language parser using tree-sitter.

Handles both pure Objective-C (``.m``) and Objective-C++ (``.mm``) files.
Extracts:

- Top-level C functions (``function_definition``).
- Class interfaces (``@interface ... @end``) and their method signatures.
- Class implementations (``@implementation ... @end``) and their bodies.
- Category interfaces/implementations (treated as extensions of the base
  class).
- Imports (``#import``/``#include``).

Objective-C method names are selectors — the compiler-visible name for
``- (BOOL)login:(NSString *)user password:(NSString *)pw`` is
``login:password:``. We use that full selector as the ``name`` so that
overloads with the same first keyword but different argument labels
don't collide in node IDs.
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

_EXTENSIONS = (".m", ".mm", ".h")

_BRANCH_NODE_TYPES = frozenset(
    {
        "if_statement",
        "for_statement",
        "while_statement",
        "do_statement",
        "switch_statement",
        "case_statement",
    }
)

_THROW_TYPES = frozenset({"throw_statement"})


class ObjCParser:
    """Parses Objective-C source files into CodeGraph using tree-sitter."""

    @property
    def language(self) -> str:
        return "objc"

    def __init__(self) -> None:
        self._parser = get_parser("objc")

    def parse_file(self, file_path: str) -> CodeGraph:
        """Parse a single .m / .mm / .h file into a CodeGraph."""
        source = Path(file_path).read_bytes()
        tree = self._parser.parse(source)
        graph = CodeGraph(language="objc", root_path=file_path)
        module_id = module_id_from_path(file_path)
        _visit_module(tree.root_node, file_path, module_id, graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        """Parse all Objective-C source files under dir_path."""
        return parse_directory(
            self.parse_file,
            "objc",
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
    if child.type == "function_definition":
        _extract_c_function(child, file_path, module_id, graph)
    elif child.type == "class_interface":
        _extract_class_interface(child, file_path, module_id, graph)
    elif child.type == "class_implementation":
        _extract_class_implementation(child, file_path, module_id, graph)
    elif child.type == "category_interface":
        _extract_class_interface(child, file_path, module_id, graph)
    elif child.type == "category_implementation":
        _extract_class_implementation(child, file_path, module_id, graph)
    elif child.type == "preproc_include":
        _extract_import(child, graph)


def _extract_c_function(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a top-level C function such as `int main(...)`."""
    func_name = _c_function_name(node)
    if not func_name:
        return
    func_id = f"{module_id}:{func_name}"
    params = _extract_c_parameters(node)
    return_type = _extract_c_return_type(node)
    body = node.child_by_field_name("body")

    branches, exception_types, calls = _collect_func_body(body, file_path)
    complexity = compute_complexity(branches)

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
    )
    graph.nodes[func_id] = unit
    add_contains_edge(graph, module_id, func_id)
    _add_call_edges(calls, func_id, file_path, graph)


def _c_function_name(node: Node) -> str:
    """Extract the function name from a function_definition."""
    declarator = node.child_by_field_name("declarator")
    if declarator is None:
        return ""
    return _find_function_identifier(declarator)


def _find_function_identifier(node: Node) -> str:
    """Recursively find the first identifier inside a declarator chain."""
    if node.type == "identifier":
        return node_text(node)
    for child in node.children:
        name = _find_function_identifier(child)
        if name:
            return name
    return ""


def _extract_class_interface(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Create a class node and attach its method declarations."""
    class_name = _first_identifier_text(node)
    if not class_name:
        return
    class_id = f"{module_id}:{class_name}"
    if class_id not in graph.nodes:
        graph.nodes[class_id] = CodeUnit(
            id=class_id,
            name=class_name,
            kind=NodeKind.CLASS,
            location=make_location(node, file_path),
        )
        add_contains_edge(graph, module_id, class_id)

    for child in node.children:
        if child.type == "method_declaration":
            _extract_method_signature(child, file_path, class_id, graph)


def _extract_class_implementation(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Create/reuse a class node and attach its method definitions."""
    class_name = _first_identifier_text(node)
    if not class_name:
        return
    class_id = f"{module_id}:{class_name}"
    if class_id not in graph.nodes:
        graph.nodes[class_id] = CodeUnit(
            id=class_id,
            name=class_name,
            kind=NodeKind.CLASS,
            location=make_location(node, file_path),
        )
        add_contains_edge(graph, module_id, class_id)

    for impl_def in node.children:
        if impl_def.type != "implementation_definition":
            continue
        for method in impl_def.children:
            if method.type == "method_definition":
                _extract_method_definition(method, file_path, class_id, graph)


def _extract_method_signature(
    node: Node,
    file_path: str,
    class_id: str,
    graph: CodeGraph,
) -> None:
    """Add a method node for an `@interface` method_declaration (no body)."""
    selector = _objc_selector(node)
    if not selector:
        return
    method_id = f"{class_id}.{selector}"
    if method_id in graph.nodes:
        return
    params = _extract_objc_parameters(node)
    return_type = _extract_objc_return_type(node)
    graph.nodes[method_id] = CodeUnit(
        id=method_id,
        name=selector,
        kind=NodeKind.METHOD,
        location=make_location(node, file_path),
        parameters=tuple(params),
        return_type=return_type,
        cyclomatic_complexity=1,
    )
    add_contains_edge(graph, class_id, method_id)


def _extract_method_definition(
    node: Node,
    file_path: str,
    class_id: str,
    graph: CodeGraph,
) -> None:
    """Replace or add a method node with its implementation body."""
    selector = _objc_selector(node)
    if not selector:
        return
    method_id = f"{class_id}.{selector}"
    params = _extract_objc_parameters(node)
    return_type = _extract_objc_return_type(node)
    body = _first_child_by_type(node, "compound_statement")

    branches, exception_types, calls = _collect_func_body(body, file_path)
    complexity = compute_complexity(branches)

    # Replace any prior interface-only entry so the definition with a body wins.
    unit = CodeUnit(
        id=method_id,
        name=selector,
        kind=NodeKind.METHOD,
        location=make_location(node, file_path),
        parameters=tuple(params),
        return_type=return_type,
        exception_types=tuple(exception_types),
        cyclomatic_complexity=complexity,
        branches=tuple(branches),
    )
    if method_id not in graph.nodes:
        add_contains_edge(graph, class_id, method_id)
    graph.nodes[method_id] = unit
    _add_call_edges(calls, method_id, file_path, graph)


def _objc_selector(node: Node) -> str:
    """Reconstruct the Objective-C selector for a method node.

    Walks the direct children collecting identifiers and method_parameters
    in order. With K parameters, a selector looks like
    ``key1:key2:...:keyK:``; a zero-parameter selector is just ``key``.
    """
    keywords: list[str] = []
    param_count = 0
    for child in node.children:
        if child.type == "identifier":
            keywords.append(node_text(child))
        elif child.type == "method_parameter":
            param_count += 1
    if not keywords:
        return ""
    if param_count == 0:
        return keywords[0]
    if len(keywords) < param_count:
        return ""
    return "".join(f"{k}:" for k in keywords[:param_count])


def _extract_objc_parameters(node: Node) -> list[Parameter]:
    """Extract method parameters from a method_declaration/method_definition."""
    params: list[Parameter] = []
    for child in node.children:
        if child.type == "method_parameter":
            _extract_objc_param(child, params)
    return params


def _extract_objc_param(decl: Node, params: list[Parameter]) -> None:
    """Extract one Objective-C method parameter."""
    name = ""
    type_ref: TypeRef | None = None
    for child in decl.children:
        if child.type == "method_type":
            type_ref = _type_from_method_type(child)
        elif child.type == "identifier":
            name = node_text(child)
    if name:
        params.append(Parameter(name=name, type_ref=type_ref))


def _extract_objc_return_type(node: Node) -> TypeRef | None:
    """The first method_type child is the return type (wraps the result type)."""
    for child in node.children:
        if child.type == "method_type":
            return _type_from_method_type(child)
    return None


def _type_from_method_type(node: Node) -> TypeRef | None:
    """Extract a TypeRef from a (type_name) wrapper."""
    for child in node.children:
        if child.type == "type_name":
            return TypeRef(name=node_text(child).strip())
    return None


def _extract_c_parameters(node: Node) -> list[Parameter]:
    """Extract parameters from a C-style function_definition."""
    declarator = node.child_by_field_name("declarator")
    if declarator is None:
        return []
    plist = _first_child_by_type(declarator, "parameter_list")
    if plist is None:
        # Could be wrapped one level deeper (function_declarator).
        for child in declarator.children:
            nested = _first_child_by_type(child, "parameter_list")
            if nested is not None:
                plist = nested
                break
    if plist is None:
        return []
    params: list[Parameter] = []
    for child in plist.children:
        if child.type == "parameter_declaration":
            _extract_c_param(child, params)
    return params


def _extract_c_param(decl: Node, params: list[Parameter]) -> None:
    """Extract one C-style parameter."""
    type_ref: TypeRef | None = None
    name = ""
    for child in decl.children:
        if child.type in {"primitive_type", "type_identifier", "sized_type_specifier"}:
            type_ref = TypeRef(name=node_text(child))
        elif child.type == "identifier":
            name = node_text(child)
        elif child.type == "pointer_declarator":
            inner = _find_function_identifier(child)
            if inner:
                name = inner
    if name:
        params.append(Parameter(name=name, type_ref=type_ref))


def _extract_c_return_type(node: Node) -> TypeRef | None:
    """The type siblings before the declarator form the return type."""
    for child in node.children:
        if child.type in {"primitive_type", "type_identifier", "sized_type_specifier"}:
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
    """Extract `#import <...>` / `#include "..."` dependencies."""
    for child in node.children:
        if child.type in {"system_lib_string", "string_literal"}:
            raw = node_text(child).strip('<>"')
            dep = raw.rsplit("/", 1)[-1].removesuffix(".h")
            if dep and dep not in graph.dependencies:
                graph.dependencies.append(dep)


def _first_child_by_type(node: Node, type_name: str) -> Node | None:
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _first_identifier_text(node: Node) -> str:
    """Return the text of the first `identifier` child."""
    for child in node.children:
        if child.type == "identifier":
            return node_text(child)
    return ""
