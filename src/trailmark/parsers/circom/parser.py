"""Circom language parser using a vendored tree-sitter grammar."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from tree_sitter import Language, Node, Parser

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import (
    BranchInfo,
    CodeUnit,
    NodeKind,
    NodeOrigin,
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
from trailmark.parsers._walk import walk_owned

_BRANCH_NODE_TYPES = frozenset(
    {
        "if_statement",
        "for_statement",
        "while_statement",
    }
)

_THROW_TYPES: frozenset[str] = frozenset()

_EXTENSIONS = (".circom",)


class CircomParser:
    """Parses Circom source files into CodeGraph."""

    @property
    def language(self) -> str:
        return "circom"

    def __init__(self) -> None:
        from trailmark.tree_sitter_custom.circom import (
            language as circom_language,
        )

        lang = Language(circom_language())
        self._parser = Parser(lang)

    def parse_file(self, file_path: str) -> CodeGraph:
        """Parse a single Circom file into a CodeGraph."""
        source = Path(file_path).read_bytes()
        tree = self._parser.parse(source)
        graph = CodeGraph(language="circom", root_path=file_path)
        module_id = module_id_from_path(file_path)
        _visit_module(tree.root_node, file_path, module_id, graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        """Parse all .circom files under dir_path."""
        graph = parse_directory(self.parse_file, "circom", dir_path, _EXTENSIONS)
        _link_includes(graph)
        return graph


def _visit_module(
    root: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Walk the top-level of a Circom file."""
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
    if child.type == "template_definition":
        _extract_template(child, file_path, module_id, graph)
    elif child.type == "function_definition":
        _extract_function(child, file_path, module_id, graph)
    elif child.type == "main_component_definition":
        _extract_main_component(child, file_path, module_id, graph)
    elif child.type == "include_directive":
        _extract_include(child, graph)


def _extract_template(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a template definition."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    tmpl_name = node_text(name_node).strip()
    tmpl_id = f"{module_id}:{tmpl_name}"

    params = _extract_parameters(node)
    body = node.child_by_field_name("body")
    branches, calls = _collect_body(body, file_path)
    complexity = compute_complexity(branches)
    docstring = _extract_docstring(node)

    unit = CodeUnit(
        id=tmpl_id,
        name=tmpl_name,
        kind=NodeKind.TEMPLATE,
        location=make_location(node, file_path),
        parameters=tuple(params),
        cyclomatic_complexity=complexity,
        branches=tuple(branches),
        docstring=docstring,
    )
    graph.nodes[tmpl_id] = unit
    add_contains_edge(graph, module_id, tmpl_id)
    _add_call_edges(calls, tmpl_id, module_id, file_path, graph)


def _extract_function(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a function definition."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    func_name = node_text(name_node).strip()
    func_id = f"{module_id}:{func_name}"

    params = _extract_parameters(node)
    body = node.child_by_field_name("body")
    branches, calls = _collect_body(body, file_path)
    complexity = compute_complexity(branches)
    docstring = _extract_docstring(node)

    unit = CodeUnit(
        id=func_id,
        name=func_name,
        kind=NodeKind.FUNCTION,
        location=make_location(node, file_path),
        parameters=tuple(params),
        cyclomatic_complexity=complexity,
        branches=tuple(branches),
        docstring=docstring,
    )
    graph.nodes[func_id] = unit
    add_contains_edge(graph, module_id, func_id)
    _add_call_edges(calls, func_id, module_id, file_path, graph)


def _extract_main_component(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract the main component definition as a synthetic function."""
    main_id = f"{module_id}:main"
    unit = CodeUnit(
        id=main_id,
        name="main",
        kind=NodeKind.FUNCTION,
        location=make_location(node, file_path),
        cyclomatic_complexity=1,
        origin=NodeOrigin.SYNTHETIC,
    )
    graph.nodes[main_id] = unit
    add_contains_edge(graph, module_id, main_id)

    value = node.child_by_field_name("value")
    if value is not None:
        _add_call_edges(_collect_calls(value), main_id, module_id, file_path, graph)


def _extract_include(node: Node, graph: CodeGraph) -> None:
    """Extract include directives as dependency info."""
    source = node.child_by_field_name("source")
    if source is None:
        return
    raw = node_text(source).strip("\"'")
    dep = raw
    if dep and dep not in graph.dependencies:
        graph.dependencies.append(dep)


def _extract_parameters(node: Node) -> list[Parameter]:
    """Extract parameters from a template/function definition."""
    params: list[Parameter] = []
    for child in node.children:
        if child.type == "parameter_list":
            for param in child.children:
                if param.type == "parameter":
                    name_node = param.child_by_field_name("name")
                    if name_node is not None:
                        name = node_text(name_node).strip()
                        params.append(Parameter(name=name))
    return params


def _extract_docstring(node: Node) -> str | None:
    """Extract /// doc comments preceding a node."""
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


def _collect_body(
    body: Node | None,
    file_path: str,
) -> tuple[list[BranchInfo], list[tuple[str, Node]]]:
    """Collect branches and calls from a template/function body."""
    branches: list[BranchInfo] = []
    exception_types: list[TypeRef] = []
    ignored_calls: list[tuple[str, Node]] = []
    if body is not None:
        collect_body_info(
            body,
            file_path,
            _BRANCH_NODE_TYPES,
            "call_expression",
            _THROW_TYPES,
            branches,
            exception_types,
            ignored_calls,
        )
    calls = _collect_calls(body) if body is not None else []
    return branches, calls


def _collect_calls(node: Node) -> list[tuple[str, Node]]:
    """Recursively collect call_expression nodes from a body."""
    calls: list[tuple[str, Node]] = []
    _walk_for_calls(node, calls)
    return calls


def _walk_for_calls(
    node: Node,
    calls: list[tuple[str, Node]],
) -> None:
    """Walk the AST collecting Circom call expressions."""
    if node.type == "call_expression":
        name = _circom_call_name(node)
        if name:
            calls.append((name, node))
    for child in node.children:
        _walk_for_calls(child, calls)


def _circom_call_name(node: Node) -> str:
    """Extract the callee name from a Circom call_expression.

    Circom call_expression has no 'function' field; the identifier
    is a direct child: ``identifier(argument_list)``.
    """
    for child in node.children:
        if child.type == "identifier":
            return node_text(child).strip()
    return ""


def _add_call_edges(
    calls: list[tuple[str, Node]],
    source_id: str,
    module_id: str,
    file_path: str,
    graph: CodeGraph,
) -> None:
    """Add CALLS edges for collected call expressions."""
    for call_name, call_node in calls:
        target_id = f"{module_id}:{call_name}"
        graph.edges.append(
            CodeEdge(
                source_id=source_id,
                target_id=target_id,
                kind=EdgeKind.CALLS,
                confidence=EdgeConfidence.CERTAIN,
                location=make_location(call_node, file_path),
                attributes=(("circom_dependency", True),),
            )
        )


def _link_includes(graph: CodeGraph) -> None:
    """Resolve symbols in include closures with one path and symbol index."""
    paths = {unit.location.file_path for unit in graph.nodes.values()}
    resolved = {path: Path(path).resolve() for path in paths}
    files = {
        resolved[n.location.file_path] for n in graph.nodes.values() if n.kind == NodeKind.MODULE
    }
    includes = _include_paths(files)
    symbols: dict[tuple[Path, str], list[str]] = {}
    for unit in graph.nodes.values():
        if unit.kind in {NodeKind.TEMPLATE, NodeKind.FUNCTION}:
            symbols.setdefault((resolved[unit.location.file_path], unit.name), []).append(unit.id)
    visible = {path: _include_closure(path, includes) for path in files}
    graph.edges[:] = [_include_edge(e, graph, resolved, visible, symbols) for e in graph.edges]


def _include_paths(files: set[Path]) -> dict[Path, set[Path]]:
    includes: dict[Path, set[Path]] = {}
    parser = CircomParser()
    for path in files:
        root = parser._parser.parse(path.read_bytes()).root_node
        targets = set()
        for node in walk_owned(root):
            if node.type != "include_directive":
                continue
            source = node.child_by_field_name("source")
            if source is not None:
                target = (path.parent / node_text(source).strip("\"'")).resolve()
                if target in files:
                    targets.add(target)
        includes[path] = targets
    return includes


def _include_closure(path: Path, includes: dict[Path, set[Path]]) -> set[Path]:
    visible, pending = set(), [path]
    while pending:
        current = pending.pop()
        if current not in visible:
            visible.add(current)
            pending.extend(includes.get(current, ()))
    return visible


def _include_edge(
    edge: CodeEdge,
    graph: CodeGraph,
    resolved: dict[str, Path],
    visible: dict[Path, set[Path]],
    symbols: dict[tuple[Path, str], list[str]],
) -> CodeEdge:
    if edge.kind != EdgeKind.CALLS or not dict(edge.attributes).get("circom_dependency"):
        return edge
    path = resolved[graph.nodes[edge.source_id].location.file_path]
    raw = dict(edge.attributes).get("raw_target", edge.target_id)
    name = str(raw).rsplit(":", 1)[-1]
    candidates = [
        ident
        for included in visible.get(path, {path})
        for ident in symbols.get((included, name), [])
    ]
    if len(candidates) == 1:
        target = candidates[0]
        confidence = (
            EdgeConfidence.CERTAIN
            if resolved[graph.nodes[target].location.file_path] == path
            else EdgeConfidence.INFERRED
        )
        return replace(edge, target_id=target, confidence=confidence)
    if candidates:
        return replace(
            edge,
            target_id=f"{edge.source_id}.$ambiguous.{name}",
            confidence=EdgeConfidence.UNCERTAIN,
        )
    return edge
