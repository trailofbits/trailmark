"""Move language parser using a vendored tree-sitter grammar."""

from __future__ import annotations

from pathlib import Path

from tree_sitter import Language, Node, Parser

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import BranchInfo, CodeUnit, NodeKind, Parameter, TypeRef
from trailmark.parsers._common import (
    add_contains_edge,
    add_module_node,
    collect_body_info,
    compute_complexity,
    first_child_by_type,
    make_location,
    module_id_from_path,
    node_text,
    parse_directory,
)

_BRANCH_NODE_TYPES = frozenset(
    {
        "if_expression",
        "while_expression",
        "loop_expression",
    }
)

_THROW_TYPES: frozenset[str] = frozenset()

_EXTENSIONS = (".move",)

_FUNCTION_TYPES = frozenset(
    {
        "function_definition",
        "native_function_definition",
        "macro_function_definition",
    }
)


class MoveParser:
    """Parses Move source files into CodeGraph."""

    @property
    def language(self) -> str:
        return "move"

    def __init__(self) -> None:
        from trailmark.tree_sitter_custom.move import language as move_language

        lang = Language(move_language())
        self._parser = Parser(lang)

    def parse_file(self, file_path: str) -> CodeGraph:
        """Parse a single Move file into a CodeGraph."""
        source = Path(file_path).read_bytes()
        tree = self._parser.parse(source)
        graph = CodeGraph(language="move", root_path=file_path)
        module_id = module_id_from_path(file_path)
        _visit_file(tree.root_node, file_path, module_id, graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        """Parse all .move files under dir_path."""
        return parse_directory(
            self.parse_file,
            "move",
            dir_path,
            _EXTENSIONS,
        )


def _visit_file(
    root: Node,
    file_path: str,
    file_module_id: str,
    graph: CodeGraph,
) -> None:
    """Walk the file root, extracting module definitions and members."""
    add_module_node(root, file_path, file_module_id, graph)
    for child in root.children:
        if child.type == "module_definition":
            _extract_module_definition(child, file_path, file_module_id, graph)
        elif child.type == "module_extension_definition":
            module_node = child.child_by_field_name("module")
            if module_node is not None and module_node.type == "module_definition":
                _extract_module_definition(module_node, file_path, file_module_id, graph)


def _extract_module_definition(
    node: Node,
    file_path: str,
    file_module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract one Move module and its members."""
    identity = node.child_by_field_name("module_identity")
    if identity is None:
        return
    name_node = identity.child_by_field_name("module")
    if name_node is None:
        return
    move_module_name = node_text(name_node)
    move_module_id = f"{file_module_id}:{move_module_name}"

    unit = CodeUnit(
        id=move_module_id,
        name=move_module_name,
        kind=NodeKind.MODULE,
        location=make_location(node, file_path),
        docstring=_extract_docstring(node),
    )
    graph.nodes[move_module_id] = unit
    add_contains_edge(graph, file_module_id, move_module_id)

    body = node.child_by_field_name("module_body")
    if body is None:
        return
    for child in body.children:
        _visit_module_child(child, file_path, move_module_id, graph)


def _visit_module_child(
    child: Node,
    file_path: str,
    move_module_id: str,
    graph: CodeGraph,
) -> None:
    """Dispatch one child in a module body."""
    if child.type in _FUNCTION_TYPES:
        _extract_function(child, file_path, move_module_id, graph)
    elif child.type in {"struct_definition", "native_struct_definition"}:
        _extract_struct(child, file_path, move_module_id, graph)
    elif child.type == "enum_definition":
        _extract_enum(child, file_path, move_module_id, graph)
    elif child.type == "use_declaration":
        _extract_use(child, graph)


def _extract_struct(
    node: Node,
    file_path: str,
    move_module_id: str,
    graph: CodeGraph,
) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    struct_name = node_text(name_node)
    struct_id = f"{move_module_id}.{struct_name}"
    unit = CodeUnit(
        id=struct_id,
        name=struct_name,
        kind=NodeKind.STRUCT,
        location=make_location(node, file_path),
        docstring=_extract_docstring(node),
    )
    graph.nodes[struct_id] = unit
    add_contains_edge(graph, move_module_id, struct_id)


def _extract_enum(
    node: Node,
    file_path: str,
    move_module_id: str,
    graph: CodeGraph,
) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    enum_name = node_text(name_node)
    enum_id = f"{move_module_id}.{enum_name}"
    unit = CodeUnit(
        id=enum_id,
        name=enum_name,
        kind=NodeKind.ENUM,
        location=make_location(node, file_path),
        docstring=_extract_docstring(node),
    )
    graph.nodes[enum_id] = unit
    add_contains_edge(graph, move_module_id, enum_id)


def _extract_function(
    node: Node,
    file_path: str,
    move_module_id: str,
    graph: CodeGraph,
) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    func_name = node_text(name_node)
    func_id = f"{move_module_id}.{func_name}"

    params = _extract_parameters(node)
    return_type = _extract_return_type(node)
    body = node.child_by_field_name("body")
    branches, calls = _collect_body_info(body, file_path)

    unit = CodeUnit(
        id=func_id,
        name=func_name,
        kind=NodeKind.FUNCTION,
        location=make_location(node, file_path),
        parameters=tuple(params),
        return_type=return_type,
        cyclomatic_complexity=compute_complexity(branches),
        branches=tuple(branches),
        docstring=_extract_docstring(node),
    )
    graph.nodes[func_id] = unit
    add_contains_edge(graph, move_module_id, func_id)

    _add_call_edges(calls, func_id, move_module_id, file_path, graph)


def _extract_parameters(node: Node) -> list[Parameter]:
    params_node = node.child_by_field_name("parameters")
    if params_node is None:
        return []
    params: list[Parameter] = []
    for child in params_node.children:
        param_node = child
        if child.type == "mut_function_parameter":
            inner = first_child_by_type(child, "function_parameter")
            if inner is not None:
                param_node = inner
        if param_node.type != "function_parameter":
            continue
        name_node = param_node.child_by_field_name("name")
        if name_node is None:
            continue
        type_node = param_node.child_by_field_name("type")
        params.append(
            Parameter(
                name=node_text(name_node),
                type_ref=TypeRef(name=node_text(type_node)) if type_node is not None else None,
            )
        )
    return params


def _extract_return_type(node: Node) -> TypeRef | None:
    ret = node.child_by_field_name("return_type")
    if ret is None:
        return None
    text = node_text(ret).lstrip(":").strip()
    if not text:
        return None
    return TypeRef(name=text)


def _collect_body_info(
    body: Node | None,
    file_path: str,
) -> tuple[list[BranchInfo], list[tuple[str, Node]]]:
    branches: list[BranchInfo] = []
    ignored_exceptions: list[TypeRef] = []
    ignored_calls: list[tuple[str, Node]] = []
    calls: list[tuple[str, Node]] = []
    if body is not None:
        collect_body_info(
            body,
            file_path,
            _BRANCH_NODE_TYPES,
            "__no_call_node_type__",
            _THROW_TYPES,
            branches,
            ignored_exceptions,
            ignored_calls,
        )
        calls = _collect_calls(body)
    return branches, calls


def _collect_calls(node: Node) -> list[tuple[str, Node]]:
    calls: list[tuple[str, Node]] = []
    stack: list[Node] = [node]
    while stack:
        current = stack.pop()
        if current.type == "call_expression":
            call_name = _move_call_name(current)
            if call_name:
                calls.append((call_name, current))
        stack.extend(reversed(current.children))
    return calls


def _move_call_name(call: Node) -> str:
    """Extract a Move call target name from call_expression."""
    for child in call.children:
        if child.type != "name_expression":
            continue
        access = child.child_by_field_name("access")
        if access is None:
            continue
        member = access.child_by_field_name("member")
        if member is not None:
            module = access.child_by_field_name("module")
            if module is not None:
                return f"{node_text(module)}::{node_text(member)}"
            return node_text(member)
        return node_text(access)
    return ""


def _add_call_edges(
    calls: list[tuple[str, Node]],
    source_id: str,
    move_module_id: str,
    file_path: str,
    graph: CodeGraph,
) -> None:
    for call_name, call_node in calls:
        target_id = _resolve_call_target(call_name, move_module_id)
        graph.edges.append(
            CodeEdge(
                source_id=source_id,
                target_id=target_id,
                kind=EdgeKind.CALLS,
                confidence=_call_confidence(call_name),
                location=make_location(call_node, file_path),
            )
        )


def _resolve_call_target(call_name: str, move_module_id: str) -> str:
    if "::" in call_name or "." in call_name:
        return call_name
    return f"{move_module_id}.{call_name}"


def _call_confidence(call_name: str) -> EdgeConfidence:
    if "::" in call_name or "." in call_name:
        return EdgeConfidence.INFERRED
    return EdgeConfidence.CERTAIN


def _extract_use(node: Node, graph: CodeGraph) -> None:
    """Extract use declarations as dependency info."""
    module_id = _find_module_identity(node)
    if module_id is None:
        return
    mod = module_id.child_by_field_name("module")
    if mod is None:
        return
    dep = node_text(mod)
    if dep and dep not in graph.dependencies:
        graph.dependencies.append(dep)


def _find_module_identity(node: Node) -> Node | None:
    for child in node.children:
        if child.type == "module_identity":
            return child
        nested = _find_module_identity(child)
        if nested is not None:
            return nested
    return None


def _extract_docstring(node: Node) -> str | None:
    lines: list[str] = []
    prev = node.prev_sibling
    while prev is not None:
        if prev.type == "line_comment":
            text = node_text(prev)
            if text.startswith("///"):
                lines.append(text[3:].strip())
                prev = prev.prev_sibling
                continue
            break
        if prev.type == "newline":
            prev = prev.prev_sibling
            continue
        break
    if not lines:
        return None
    lines.reverse()
    return "\n".join(lines)
