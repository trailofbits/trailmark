"""Solidity language parser using tree-sitter."""

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
    }
)

_THROW_TYPES = frozenset({"revert_statement"})

_EXTENSIONS = (".sol",)

_CONTRACT_NODE_KINDS: dict[str, NodeKind] = {
    "contract_declaration": NodeKind.CONTRACT,
    "interface_declaration": NodeKind.INTERFACE,
    "library_declaration": NodeKind.LIBRARY,
}

_FUNCTION_TYPES = frozenset(
    {
        "function_definition",
        "constructor_definition",
        "modifier_definition",
    }
)


class SolidityParser:
    """Parses Solidity source files into CodeGraph using tree-sitter."""

    @property
    def language(self) -> str:
        return "solidity"

    def __init__(self) -> None:
        self._parser = get_parser("solidity")

    def parse_file(self, file_path: str) -> CodeGraph:
        """Parse a single Solidity file into a CodeGraph."""
        source = Path(file_path).read_bytes()
        tree = self._parser.parse(source)
        graph = CodeGraph(language="solidity", root_path=file_path)
        module_id = module_id_from_path(file_path)
        _visit_module(tree.root_node, file_path, module_id, graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        """Parse all .sol files under dir_path into a merged graph."""
        return parse_directory(
            self.parse_file,
            "solidity",
            dir_path,
            _EXTENSIONS,
        )


def _visit_module(
    root: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Walk the top-level of a Solidity file, extracting nodes and edges."""
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
    if child.type in _CONTRACT_NODE_KINDS:
        _extract_contract(child, file_path, module_id, graph)
    elif child.type == "struct_declaration":
        _extract_struct(child, file_path, module_id, graph)
    elif child.type == "enum_declaration":
        _extract_enum(child, file_path, module_id, graph)
    elif child.type == "function_definition":
        _extract_function(child, file_path, module_id, None, graph)
    elif child.type == "import_directive":
        _extract_import(child, graph)


def _extract_contract(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a contract, interface, or library declaration."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    contract_name = node_text(name_node)
    contract_id = f"{module_id}:{contract_name}"
    kind = _CONTRACT_NODE_KINDS[node.type]
    docstring = _extract_docstring(node)

    unit = CodeUnit(
        id=contract_id,
        name=contract_name,
        kind=kind,
        location=make_location(node, file_path),
        docstring=docstring,
    )
    graph.nodes[contract_id] = unit
    add_contains_edge(graph, module_id, contract_id)
    _extract_inheritance(node, contract_id, module_id, graph)
    _visit_contract_body(node, file_path, module_id, contract_id, graph)


def _extract_inheritance(
    node: Node,
    contract_id: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Add INHERITS edges from inheritance_specifier children."""
    for child in node.children:
        if child.type != "inheritance_specifier":
            continue
        for sub in child.children:
            if sub.type == "user_defined_type":
                base_name = _extract_type_name(sub)
                if base_name:
                    base_id = f"{module_id}:{base_name}"
                    graph.edges.append(
                        CodeEdge(
                            source_id=contract_id,
                            target_id=base_id,
                            kind=EdgeKind.INHERITS,
                            confidence=EdgeConfidence.INFERRED,
                        )
                    )


def _extract_type_name(node: Node) -> str:
    """Extract a type name from a user_defined_type or identifier node."""
    for child in node.children:
        if child.type == "identifier":
            return node_text(child)
    return node_text(node)


def _visit_contract_body(
    node: Node,
    file_path: str,
    module_id: str,
    contract_id: str,
    graph: CodeGraph,
) -> None:
    """Walk the body of a contract, interface, or library."""
    body = node.child_by_field_name("body")
    if body is None:
        return
    for child in body.children:
        if child.type in _FUNCTION_TYPES:
            _extract_function(child, file_path, module_id, contract_id, graph)
        elif child.type == "struct_declaration":
            _extract_struct(child, file_path, module_id, graph)
        elif child.type == "enum_declaration":
            _extract_enum(child, file_path, module_id, graph)


def _extract_struct(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a struct declaration."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    struct_name = node_text(name_node)
    struct_id = f"{module_id}:{struct_name}"
    unit = CodeUnit(
        id=struct_id,
        name=struct_name,
        kind=NodeKind.STRUCT,
        location=make_location(node, file_path),
    )
    graph.nodes[struct_id] = unit
    add_contains_edge(graph, module_id, struct_id)


def _extract_enum(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract an enum declaration."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    enum_name = node_text(name_node)
    enum_id = f"{module_id}:{enum_name}"
    unit = CodeUnit(
        id=enum_id,
        name=enum_name,
        kind=NodeKind.ENUM,
        location=make_location(node, file_path),
    )
    graph.nodes[enum_id] = unit
    add_contains_edge(graph, module_id, enum_id)


def _extract_function(
    node: Node,
    file_path: str,
    module_id: str,
    contract_id: str | None,
    graph: CodeGraph,
) -> None:
    """Extract a function, constructor, or modifier definition."""
    func_name = _get_function_name(node)
    if not func_name:
        return

    if contract_id is not None:
        func_id = f"{contract_id}.{func_name}"
        kind = NodeKind.METHOD
        owner = contract_id
    else:
        func_id = f"{module_id}:{func_name}"
        kind = NodeKind.FUNCTION
        owner = module_id

    params = _extract_parameters(node)
    return_type = _extract_return_type(node)
    body = node.child_by_field_name("body")

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

    _add_call_edges(calls, func_id, module_id, contract_id, file_path, graph)


def _get_function_name(node: Node) -> str:
    """Extract the function name from a definition node."""
    if node.type == "constructor_definition":
        return "constructor"
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return ""
    return node_text(name_node)


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
    """Extract parameters from a function definition."""
    params: list[Parameter] = []
    for child in node.children:
        if child.type == "parameter":
            _extract_single_param(child, params)
    return params


def _extract_single_param(
    param: Node,
    params: list[Parameter],
) -> None:
    """Extract name and type from a Solidity parameter node."""
    type_node = param.child_by_field_name("type")
    name_node = param.child_by_field_name("name")
    if name_node is None:
        return
    name = node_text(name_node)
    type_ref = None
    if type_node is not None:
        type_ref = TypeRef(name=node_text(type_node))
    params.append(Parameter(name=name, type_ref=type_ref))


def _extract_return_type(node: Node) -> TypeRef | None:
    """Extract the return type from a Solidity function definition."""
    rt = node.child_by_field_name("return_type")
    if rt is None:
        return None
    for child in rt.children:
        if child.type == "parameter":
            type_node = child.child_by_field_name("type")
            if type_node is not None:
                return TypeRef(name=node_text(type_node))
    return None


def _extract_docstring(node: Node) -> str | None:
    """Extract NatSpec (///) comments preceding a node."""
    lines: list[str] = []
    prev = node.prev_named_sibling
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
    contract_id: str | None,
    file_path: str,
    graph: CodeGraph,
) -> None:
    """Add CALLS edges for all collected call expressions."""
    for call_name, call_node in calls:
        target_id = _resolve_call_target(call_name, module_id, contract_id)
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
    """Determine confidence for a Solidity call expression."""
    if "." in call_name:
        return EdgeConfidence.INFERRED
    return EdgeConfidence.CERTAIN


def _resolve_call_target(
    call_name: str,
    module_id: str,
    contract_id: str | None,
) -> str:
    """Resolve a call name to a target node ID."""
    if "." not in call_name:
        return f"{module_id}:{call_name}"
    return call_name


def _extract_import(node: Node, graph: CodeGraph) -> None:
    """Extract import directives as dependency info."""
    for child in node.children:
        if child.type == "string":
            raw = node_text(child).strip("\"'")
            dep = raw.rsplit("/", maxsplit=1)[-1].removesuffix(".sol")
            if dep and dep not in graph.dependencies:
                graph.dependencies.append(dep)
            return
