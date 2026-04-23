"""Cairo language parser using tree-sitter."""

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
        "if_expression",
        "match_arm",
        "else_clause",
    }
)

_THROW_TYPES: frozenset[str] = frozenset()

_EXTENSIONS = (".cairo",)


class CairoParser:
    """Parses Cairo source files into CodeGraph using tree-sitter."""

    @property
    def language(self) -> str:
        return "cairo"

    def __init__(self) -> None:
        self._parser = get_parser("cairo")

    def parse_file(self, file_path: str) -> CodeGraph:
        """Parse a single Cairo file into a CodeGraph."""
        source = Path(file_path).read_bytes()
        tree = self._parser.parse(source)
        graph = CodeGraph(language="cairo", root_path=file_path)
        module_id = module_id_from_path(file_path)
        _visit_module(tree.root_node, file_path, module_id, graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        """Parse all .cairo files under dir_path into a merged graph."""
        return parse_directory(
            self.parse_file,
            "cairo",
            dir_path,
            _EXTENSIONS,
        )


def _visit_module(
    root: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Walk the top-level of a Cairo file, extracting nodes and edges."""
    add_module_node(root, file_path, module_id, graph)
    # Cairo root is program > cairo_1_file; navigate one level deeper.
    file_node = _find_cairo_file_node(root)
    if file_node is None:
        return
    for child in file_node.children:
        _visit_top_level(child, file_path, module_id, graph)


def _find_cairo_file_node(root: Node) -> Node | None:
    """Find the cairo_1_file node inside the root program node."""
    for child in root.children:
        if child.type == "cairo_1_file":
            return child
    return root if root.type == "cairo_1_file" else None


def _visit_top_level(
    child: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Dispatch a single top-level node."""
    if child.type == "function_definition":
        _extract_function(child, file_path, module_id, None, graph)
    elif child.type == "trait_item":
        _extract_trait(child, file_path, module_id, graph)
    elif child.type == "struct_item":
        _extract_struct(child, file_path, module_id, graph)
    elif child.type == "enum_item":
        _extract_enum(child, file_path, module_id, graph)
    elif child.type == "mod_item":
        _extract_mod(child, file_path, module_id, graph)
    elif child.type == "impl_item":
        _extract_impl(child, file_path, module_id, graph)
    elif child.type == "use_declaration":
        _extract_import(child, graph)


def _extract_mod(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a mod item, using CONTRACT kind for starknet contracts."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    mod_name = node_text(name_node)
    mod_id = f"{module_id}:{mod_name}"
    kind = NodeKind.CONTRACT if _has_contract_attr(node) else NodeKind.MODULE
    docstring = _extract_docstring(node)

    unit = CodeUnit(
        id=mod_id,
        name=mod_name,
        kind=kind,
        location=make_location(node, file_path),
        docstring=docstring,
    )
    graph.nodes[mod_id] = unit
    add_contains_edge(graph, module_id, mod_id)

    body = node.child_by_field_name("body")
    if body is None:
        return
    for child in body.children:
        _visit_mod_body_child(child, file_path, module_id, mod_id, graph)


def _visit_mod_body_child(
    child: Node,
    file_path: str,
    module_id: str,
    mod_id: str,
    graph: CodeGraph,
) -> None:
    """Dispatch a child inside a mod body."""
    if child.type == "function_definition":
        _extract_function(child, file_path, module_id, mod_id, graph)
    elif child.type == "struct_item":
        _extract_struct_in(child, file_path, module_id, mod_id, graph)
    elif child.type == "enum_item":
        _extract_enum_in(child, file_path, module_id, mod_id, graph)
    elif child.type == "impl_item":
        _extract_impl_in(child, file_path, module_id, mod_id, graph)
    elif child.type == "use_declaration":
        _extract_import(child, graph)


def _has_contract_attr(node: Node) -> bool:
    """Check if preceding attribute_item siblings contain starknet::contract."""
    prev = node.prev_named_sibling
    while prev is not None and prev.type == "attribute_item":
        if "starknet::contract" in node_text(prev):
            return True
        prev = prev.prev_named_sibling
    return False


def _extract_trait(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a trait definition and its method signatures."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    trait_name = node_text(name_node)
    trait_id = f"{module_id}:{trait_name}"
    docstring = _extract_docstring(node)

    unit = CodeUnit(
        id=trait_id,
        name=trait_name,
        kind=NodeKind.TRAIT,
        location=make_location(node, file_path),
        docstring=docstring,
    )
    graph.nodes[trait_id] = unit
    add_contains_edge(graph, module_id, trait_id)

    body = node.child_by_field_name("body")
    if body is None:
        return
    for child in body.children:
        if child.type in ("function_definition", "function_signature"):
            _extract_function(child, file_path, module_id, trait_id, graph)


def _extract_struct(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a top-level struct definition."""
    _extract_struct_impl(node, file_path, module_id, module_id, graph)


def _extract_struct_in(
    node: Node,
    file_path: str,
    module_id: str,
    container_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a struct inside a mod/contract."""
    _extract_struct_impl(node, file_path, module_id, container_id, graph)


def _extract_struct_impl(
    node: Node,
    file_path: str,
    module_id: str,
    container_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a struct definition into the graph."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    struct_name = node_text(name_node)
    struct_id = f"{module_id}:{struct_name}"
    docstring = _extract_docstring(node)

    unit = CodeUnit(
        id=struct_id,
        name=struct_name,
        kind=NodeKind.STRUCT,
        location=make_location(node, file_path),
        docstring=docstring,
    )
    graph.nodes[struct_id] = unit
    add_contains_edge(graph, container_id, struct_id)


def _extract_enum(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a top-level enum definition."""
    _extract_enum_impl(node, file_path, module_id, module_id, graph)


def _extract_enum_in(
    node: Node,
    file_path: str,
    module_id: str,
    container_id: str,
    graph: CodeGraph,
) -> None:
    """Extract an enum inside a mod/contract."""
    _extract_enum_impl(node, file_path, module_id, container_id, graph)


def _extract_enum_impl(
    node: Node,
    file_path: str,
    module_id: str,
    container_id: str,
    graph: CodeGraph,
) -> None:
    """Extract an enum definition into the graph."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    enum_name = node_text(name_node)
    enum_id = f"{module_id}:{enum_name}"
    docstring = _extract_docstring(node)

    unit = CodeUnit(
        id=enum_id,
        name=enum_name,
        kind=NodeKind.ENUM,
        location=make_location(node, file_path),
        docstring=docstring,
    )
    graph.nodes[enum_id] = unit
    add_contains_edge(graph, container_id, enum_id)


def _extract_impl(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a top-level impl block."""
    _extract_impl_core(node, file_path, module_id, None, graph)


def _extract_impl_in(
    node: Node,
    file_path: str,
    module_id: str,
    mod_id: str,
    graph: CodeGraph,
) -> None:
    """Extract an impl block inside a mod/contract."""
    _extract_impl_core(node, file_path, module_id, mod_id, graph)


def _extract_impl_core(
    node: Node,
    file_path: str,
    module_id: str,
    container_id: str | None,
    graph: CodeGraph,
) -> None:
    """Extract an impl block, resolving the type it implements for."""
    impl_name = _get_impl_name(node)
    if not impl_name:
        return
    type_id = f"{module_id}:{impl_name}"

    _add_implements_edge(node, type_id, module_id, graph)

    body = _find_child_by_type(node, "block")
    if body is None:
        return
    for child in body.children:
        if child.type == "function_definition":
            _extract_function(child, file_path, module_id, type_id, graph)


def _get_impl_name(node: Node) -> str:
    """Extract the impl name (first type_identifier child)."""
    for child in node.children:
        if child.type == "type_identifier":
            return node_text(child)
    return ""


def _add_implements_edge(
    node: Node,
    type_id: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Add an IMPLEMENTS edge if the impl has a trait reference."""
    found_of = False
    for child in node.children:
        if child.type == "of":
            found_of = True
        elif found_of and child.type == "generic_type":
            trait_type = child.child_by_field_name("type")
            if trait_type is not None:
                trait_name = node_text(trait_type)
                trait_id = f"{module_id}:{trait_name}"
                graph.edges.append(
                    CodeEdge(
                        source_id=type_id,
                        target_id=trait_id,
                        kind=EdgeKind.IMPLEMENTS,
                        confidence=EdgeConfidence.CERTAIN,
                    )
                )
            return


def _extract_function(
    node: Node,
    file_path: str,
    module_id: str,
    container_id: str | None,
    graph: CodeGraph,
) -> None:
    """Extract a function or method definition."""
    func_name = _get_function_name(node)
    if not func_name:
        return

    if container_id is not None:
        func_id = f"{container_id}.{func_name}"
        kind = NodeKind.METHOD
        owner = container_id
    else:
        func_id = f"{module_id}:{func_name}"
        kind = NodeKind.FUNCTION
        owner = module_id

    params = _extract_parameters(node)
    return_type = _extract_return_type(node)
    body = _find_child_by_type(node, "block")

    branches, exception_types, calls = _collect_func_body(body, file_path)
    complexity = compute_complexity(branches)
    docstring = _extract_docstring(node)

    unit = CodeUnit(
        id=func_id,
        name=func_name,
        kind=kind,
        location=make_location(node, file_path),
        parameters=tuple(params),
        return_type=return_type,
        exception_types=tuple(exception_types),
        cyclomatic_complexity=complexity,
        branches=tuple(branches),
        docstring=docstring,
    )
    graph.nodes[func_id] = unit
    add_contains_edge(graph, owner, func_id)

    _add_call_edges(calls, func_id, module_id, container_id, file_path, graph)


def _get_function_name(node: Node) -> str:
    """Extract the function name (first identifier child)."""
    for child in node.children:
        if child.type == "identifier":
            return node_text(child)
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
    """Extract parameters from a function definition, skipping self."""
    params: list[Parameter] = []
    for child in node.children:
        if child.type == "parameter":
            _extract_single_param(child, params)
    return params


def _extract_single_param(
    param: Node,
    params: list[Parameter],
) -> None:
    """Extract name and type from a Cairo parameter node."""
    name_node = None
    type_node = None
    for child in param.children:
        if child.type == "identifier":
            name_node = child
        elif child.type == "self":
            return
        elif child.type in (
            "primitive_type",
            "type_identifier",
            "generic_type",
            "reference_type",
            "at_type",
            "scoped_type_identifier",
        ):
            type_node = child
    if name_node is None:
        return
    name = node_text(name_node)
    type_ref = TypeRef(name=node_text(type_node)) if type_node else None
    params.append(Parameter(name=name, type_ref=type_ref))


def _extract_return_type(node: Node) -> TypeRef | None:
    """Extract the return type from a Cairo function definition."""
    returns = node.child_by_field_name("returns")
    if returns is None:
        return None
    return TypeRef(name=node_text(returns))


def _extract_docstring(node: Node) -> str | None:
    """Extract /// doc comments preceding a node.

    Skips attribute_item siblings between comments and the node.
    Cairo uses "comment" node type (not "line_comment").
    """
    lines: list[str] = []
    prev = node.prev_named_sibling
    # Skip over attribute items to reach doc comments.
    while prev is not None and prev.type == "attribute_item":
        prev = prev.prev_named_sibling
    while prev is not None and prev.type == "comment":
        text = node_text(prev)
        if text.startswith("///"):
            lines.append(text[3:].strip())
            prev = prev.prev_named_sibling
        else:
            break
    if not lines:
        return None
    lines.reverse()
    return "\n".join(lines)


def _add_call_edges(
    calls: list[tuple[str, Node]],
    source_id: str,
    module_id: str,
    container_id: str | None,
    file_path: str,
    graph: CodeGraph,
) -> None:
    """Add CALLS edges for all collected call expressions."""
    for call_name, call_node in calls:
        target_id = _resolve_call_target(call_name, module_id, container_id)
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
    """Determine confidence for a Cairo call expression."""
    if "." in call_name or "::" in call_name:
        return EdgeConfidence.INFERRED
    return EdgeConfidence.CERTAIN


def _resolve_call_target(
    call_name: str,
    module_id: str,
    container_id: str | None,
) -> str:
    """Resolve a call name to a target node ID."""
    if "." not in call_name and "::" not in call_name:
        return f"{module_id}:{call_name}"
    return call_name


def _extract_import(node: Node, graph: CodeGraph) -> None:
    """Extract use declarations as dependency info."""
    for child in node.children:
        if child.type in ("scoped_identifier", "identifier"):
            dep = _first_path_segment(child)
            if dep and dep not in graph.dependencies:
                graph.dependencies.append(dep)
            return


def _first_path_segment(node: Node) -> str:
    """Extract the first path segment from a use path."""
    if node.type == "identifier":
        return node_text(node)
    for child in node.children:
        if child.type == "identifier":
            return node_text(child)
        if child.type == "scoped_identifier":
            return _first_path_segment(child)
    return ""


def _find_child_by_type(node: Node, type_name: str) -> Node | None:
    """Find the first child with the given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None
