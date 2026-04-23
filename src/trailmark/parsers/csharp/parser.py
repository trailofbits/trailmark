"""C# language parser using tree-sitter."""

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
        "for_each_statement",
        "while_statement",
        "do_statement",
        "switch_section",
        "catch_clause",
        "conditional_expression",
    }
)

_THROW_TYPES = frozenset({"throw_statement", "throw_expression"})

_EXTENSIONS = (".cs",)

_CLASS_NODE_KINDS: dict[str, NodeKind] = {
    "class_declaration": NodeKind.CLASS,
    "interface_declaration": NodeKind.INTERFACE,
    "struct_declaration": NodeKind.STRUCT,
    "enum_declaration": NodeKind.ENUM,
}

_FUNCTION_TYPES = frozenset(
    {"method_declaration", "constructor_declaration"},
)


class CSharpParser:
    """Parses C# source files into CodeGraph using tree-sitter."""

    @property
    def language(self) -> str:
        return "csharp"

    def __init__(self) -> None:
        self._parser = get_parser("csharp")

    def parse_file(self, file_path: str) -> CodeGraph:
        """Parse a single C# file into a CodeGraph."""
        source = Path(file_path).read_bytes()
        tree = self._parser.parse(source)
        graph = CodeGraph(language="csharp", root_path=file_path)
        module_id = module_id_from_path(file_path)
        _visit_module(tree.root_node, file_path, module_id, graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        """Parse all .cs files under dir_path into a merged graph."""
        return parse_directory(
            self.parse_file,
            "csharp",
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
        _visit_top_level(child, file_path, module_id, graph)


def _visit_top_level(
    child: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Dispatch a single top-level node."""
    if child.type == "namespace_declaration":
        _extract_namespace(child, file_path, module_id, graph)
    elif child.type in _CLASS_NODE_KINDS:
        _extract_class(child, file_path, module_id, graph)
    elif child.type in _FUNCTION_TYPES:
        _extract_function(
            child,
            file_path,
            module_id,
            None,
            graph,
        )
    elif child.type == "using_directive":
        _extract_import(child, graph)


def _extract_namespace(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a namespace declaration and its children."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    ns_name = node_text(name_node)
    ns_id = f"{module_id}:{ns_name}"
    location = make_location(node, file_path)

    ns_unit = CodeUnit(
        id=ns_id,
        name=ns_name,
        kind=NodeKind.NAMESPACE,
        location=location,
    )
    graph.nodes[ns_id] = ns_unit
    add_contains_edge(graph, module_id, ns_id)

    body = node.child_by_field_name("body")
    if body is None:
        return
    for child in body.children:
        _visit_ns_child(child, file_path, module_id, ns_id, graph)


def _visit_ns_child(
    child: Node,
    file_path: str,
    module_id: str,
    ns_id: str,
    graph: CodeGraph,
) -> None:
    """Dispatch a child node inside a namespace body."""
    if child.type in _CLASS_NODE_KINDS:
        _extract_class(child, file_path, module_id, graph, ns_id)
    elif child.type == "namespace_declaration":
        _extract_namespace(child, file_path, module_id, graph)


def _extract_class(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
    container_id: str | None = None,
) -> None:
    """Extract a class, interface, struct, or enum declaration."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    class_name = node_text(name_node)
    class_id = f"{module_id}:{class_name}"
    kind = _CLASS_NODE_KINDS.get(node.type, NodeKind.CLASS)
    location = make_location(node, file_path)

    class_unit = CodeUnit(
        id=class_id,
        name=class_name,
        kind=kind,
        location=location,
        docstring=_extract_docstring(node),
    )
    graph.nodes[class_id] = class_unit
    parent = container_id if container_id else module_id
    add_contains_edge(graph, parent, class_id)

    _extract_base_list(node, class_id, module_id, graph)
    _visit_class_body(node, file_path, module_id, class_id, graph)


def _visit_class_body(
    node: Node,
    file_path: str,
    module_id: str,
    class_id: str,
    graph: CodeGraph,
) -> None:
    """Visit children of a class/struct/interface body."""
    body = node.child_by_field_name("body")
    if body is None:
        return
    for child in body.children:
        if child.type in _FUNCTION_TYPES:
            _extract_function(
                child,
                file_path,
                module_id,
                class_id,
                graph,
            )


def _find_base_list(node: Node) -> Node | None:
    """Find the base_list child node of a class declaration."""
    for child in node.children:
        if child.type == "base_list":
            return child
    return None


def _extract_base_list(
    node: Node,
    class_id: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract inheritance/implementation edges from base_list."""
    base_list = _find_base_list(node)
    if base_list is None:
        return
    first = True
    for child in base_list.children:
        base_name = _base_type_name(child)
        if not base_name:
            continue
        base_id = f"{module_id}:{base_name}"
        edge_kind = EdgeKind.INHERITS if first else EdgeKind.IMPLEMENTS
        graph.edges.append(
            CodeEdge(
                source_id=class_id,
                target_id=base_id,
                kind=edge_kind,
                confidence=EdgeConfidence.INFERRED,
            )
        )
        first = False


def _base_type_name(node: Node) -> str:
    """Extract the type name from a base_list child node."""
    if node.type in ("identifier", "generic_name"):
        return node_text(node)
    for child in node.children:
        if child.type in ("identifier", "generic_name"):
            return node_text(child)
    return ""


def _extract_function(
    node: Node,
    file_path: str,
    module_id: str,
    class_id: str | None,
    graph: CodeGraph,
) -> None:
    """Extract a method or constructor declaration."""
    func_name = _get_func_name(node, class_id)
    if not func_name:
        return

    func_id, kind, container_id = _func_identity(
        func_name,
        module_id,
        class_id,
    )
    params = _extract_parameters(node)
    return_type = _extract_return_type(node)
    body = node.child_by_field_name("body")
    branches, exception_types, calls = _walk_body(
        body,
        file_path,
    )

    unit = CodeUnit(
        id=func_id,
        name=func_name,
        kind=kind,
        location=make_location(node, file_path),
        parameters=tuple(params),
        return_type=return_type,
        exception_types=tuple(exception_types),
        cyclomatic_complexity=compute_complexity(branches),
        branches=tuple(branches),
        docstring=_extract_docstring(node),
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


def _get_func_name(node: Node, class_id: str | None) -> str:
    """Get function name from a method or constructor node."""
    name_node = node.child_by_field_name("name")
    if name_node is not None:
        return node_text(name_node)
    if node.type == "constructor_declaration" and class_id:
        return class_id.split(":")[-1]
    return ""


def _func_identity(
    func_name: str,
    module_id: str,
    class_id: str | None,
) -> tuple[str, NodeKind, str]:
    """Return (func_id, kind, container_id) for a function."""
    if class_id is not None:
        return (
            f"{class_id}.{func_name}",
            NodeKind.METHOD,
            class_id,
        )
    return (
        f"{module_id}:{func_name}",
        NodeKind.FUNCTION,
        module_id,
    )


def _walk_body(
    body: Node | None,
    file_path: str,
) -> tuple[list[BranchInfo], list[TypeRef], list[tuple[str, Node]]]:
    """Walk a function body collecting branches, throws, calls."""
    branches: list[BranchInfo] = []
    exception_types: list[TypeRef] = []
    calls: list[tuple[str, Node]] = []
    if body is not None:
        _visit_node(
            body,
            file_path,
            branches,
            exception_types,
            calls,
        )
    return branches, exception_types, calls


def _visit_node(
    node: Node,
    file_path: str,
    branches: list[BranchInfo],
    exception_types: list[TypeRef],
    calls: list[tuple[str, Node]],
) -> None:
    """Recursively visit a node, collecting body info."""
    if node.type in _BRANCH_NODE_TYPES:
        cond = node.child_by_field_name("condition")
        cond_text = node_text(cond) if cond else node.type
        branches.append(
            BranchInfo(
                location=make_location(node, file_path),
                condition=cond_text,
            )
        )

    if node.type in _THROW_TYPES:
        _collect_throw(node, exception_types)

    if node.type == "invocation_expression":
        call_name = _extract_call_name(node)
        if call_name:
            calls.append((call_name, node))

    for child in node.children:
        _visit_node(
            child,
            file_path,
            branches,
            exception_types,
            calls,
        )


def _extract_call_name(node: Node) -> str:
    """Extract function name from an invocation_expression."""
    func = node.child_by_field_name("function")
    if func is None:
        return ""
    if func.type == "identifier":
        return node_text(func)
    if func.type == "member_access_expression":
        return node_text(func)
    return ""


def _collect_throw(
    node: Node,
    exception_types: list[TypeRef],
) -> None:
    """Extract exception type from a throw statement."""
    for child in node.children:
        if child.type == "object_creation_expression":
            _collect_creation_type(child, exception_types)
            return
        if child.type == "identifier":
            exception_types.append(TypeRef(name=node_text(child)))
            return


def _collect_creation_type(
    node: Node,
    exception_types: list[TypeRef],
) -> None:
    """Extract type name from an object_creation_expression."""
    for child in node.children:
        if child.type in ("identifier", "qualified_name"):
            exception_types.append(TypeRef(name=node_text(child)))
            return


def _add_call_edges(
    calls: list[tuple[str, Node]],
    func_id: str,
    module_id: str,
    class_id: str | None,
    file_path: str,
    graph: CodeGraph,
) -> None:
    """Add CALLS edges for each collected call."""
    for call_name, call_node in calls:
        target_id = _resolve_call_target(
            call_name,
            module_id,
            class_id,
        )
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
    """Determine call confidence based on call form."""
    if "." not in call_name:
        return EdgeConfidence.CERTAIN
    return EdgeConfidence.INFERRED


def _extract_parameters(node: Node) -> list[Parameter]:
    """Extract parameters from a method declaration."""
    params_node = node.child_by_field_name("parameters")
    if params_node is None:
        return []

    params: list[Parameter] = []
    for child in params_node.children:
        if child.type == "parameter":
            param = _parse_single_param(child)
            if param is not None:
                params.append(param)
    return params


def _parse_single_param(node: Node) -> Parameter | None:
    """Parse a single parameter node into a Parameter."""
    name_node = node.child_by_field_name("name")
    type_node = node.child_by_field_name("type")
    if name_node is None:
        return None
    name = node_text(name_node)
    type_ref = _parse_type_node(type_node) if type_node else None
    return Parameter(name=name, type_ref=type_ref)


def _parse_type_node(type_node: Node) -> TypeRef:
    """Parse a C# type node into a TypeRef."""
    if type_node.type == "generic_name":
        return _parse_generic_type(type_node)
    if type_node.type == "nullable_type":
        inner = type_node.children[0] if type_node.children else None
        if inner:
            base = _parse_type_node(inner)
            return TypeRef(name=f"{base.name}?")
        return TypeRef(name=node_text(type_node))
    return TypeRef(name=node_text(type_node))


def _parse_generic_type(node: Node) -> TypeRef:
    """Parse a generic type like List<int> into a TypeRef."""
    name = ""
    args: list[TypeRef] = []
    for child in node.children:
        if child.type == "identifier":
            name = node_text(child)
        elif child.type == "type_argument_list":
            for sub in child.children:
                if sub.is_named and sub.type != ",":
                    args.append(_parse_type_node(sub))
    return TypeRef(name=name, generic_args=tuple(args))


def _extract_return_type(node: Node) -> TypeRef | None:
    """Extract return type from a method declaration."""
    type_node = node.child_by_field_name("returns")
    if type_node is None:
        return None
    return _parse_type_node(type_node)


def _extract_docstring(node: Node) -> str | None:
    """Extract XML doc comments (///) preceding a declaration."""
    comments: list[str] = []
    sibling = node.prev_named_sibling
    while sibling and sibling.type == "comment":
        text = node_text(sibling)
        if text.startswith("///"):
            comments.insert(0, text[3:].strip())
            sibling = sibling.prev_named_sibling
        else:
            break
    if comments:
        return "\n".join(comments)
    return None


def _resolve_call_target(
    call_name: str,
    module_id: str,
    class_id: str | None,
) -> str:
    """Resolve a call name to a target node ID (best effort)."""
    if "." not in call_name:
        if class_id is not None:
            return f"{class_id}.{call_name}"
        return f"{module_id}:{call_name}"
    return call_name


def _extract_import(node: Node, graph: CodeGraph) -> None:
    """Extract using directives as dependency info."""
    parts: list[str] = []
    for child in node.children:
        if child.is_named:
            parts.append(node_text(child))
    if parts:
        ns_name = parts[0].split(".")[0]
        if ns_name not in graph.dependencies:
            graph.dependencies.append(ns_name)
