"""Ruby language parser using tree-sitter."""

from __future__ import annotations

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
        "if",
        "unless",
        "while",
        "until",
        "for",
        "case",
        "when",
        "rescue",
        "conditional",
    }
)

_THROW_TYPES = frozenset[str]()

_EXTENSIONS = (".rb",)


class RubyParser:
    """Parses Ruby source files into CodeGraph using tree-sitter."""

    @property
    def language(self) -> str:
        return "ruby"

    def __init__(self) -> None:
        self._parser = Parser(get_language("ruby"))

    def parse_file(self, file_path: str) -> CodeGraph:
        """Parse a single Ruby file into a CodeGraph."""
        source = Path(file_path).read_bytes()
        tree = self._parser.parse(source)
        graph = CodeGraph(language="ruby", root_path=file_path)
        module_id = module_id_from_path(file_path)
        _visit_module(tree.root_node, file_path, module_id, graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        """Parse all .rb files under dir_path into a merged graph."""
        return parse_directory(
            self.parse_file,
            "ruby",
            dir_path,
            _EXTENSIONS,
        )


def _visit_module(
    root: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Walk the top-level of a Ruby module, extracting nodes."""
    add_module_node(root, file_path, module_id, graph)
    for child in root.children:
        _visit_top_level_node(child, file_path, module_id, graph)


def _visit_top_level_node(
    child: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Dispatch a single top-level Ruby node."""
    if child.type in ("method", "singleton_method"):
        _extract_function(
            child,
            file_path,
            module_id,
            None,
            graph,
        )
    elif child.type == "class":
        _extract_class(child, file_path, module_id, graph)
    elif child.type == "module":
        _extract_ruby_module(child, file_path, module_id, graph)
    elif child.type == "call":
        _check_require(child, graph)


def _extract_class(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a Ruby class definition and its methods."""
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

    _extract_superclass(node, class_id, module_id, graph)
    _visit_class_body(node, file_path, module_id, class_id, graph)


def _extract_ruby_module(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a Ruby module definition."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    mod_name = node_text(name_node)
    mod_node_id = f"{module_id}:{mod_name}"
    location = make_location(node, file_path)

    mod_unit = CodeUnit(
        id=mod_node_id,
        name=mod_name,
        kind=NodeKind.MODULE,
        location=location,
        docstring=_extract_docstring(node),
    )
    graph.nodes[mod_node_id] = mod_unit
    add_contains_edge(graph, module_id, mod_node_id)

    body = node.child_by_field_name("body")
    if body is not None:
        for child in body.children:
            if child.type == "method":
                _extract_function(
                    child,
                    file_path,
                    module_id,
                    mod_node_id,
                    graph,
                )
            elif child.type == "class":
                _extract_class(
                    child,
                    file_path,
                    module_id,
                    graph,
                )


def _extract_superclass_name(superclass: Node) -> str:
    """Extract the class name from a superclass node."""
    for child in superclass.children:
        if child.type in ("constant", "scope_resolution"):
            return node_text(child)
    return ""


def _extract_superclass(
    node: Node,
    class_id: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract INHERITS edge from superclass field."""
    superclass = node.child_by_field_name("superclass")
    if superclass is None:
        return
    base_name = _extract_superclass_name(superclass)
    if not base_name:
        return
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
    """Visit class body to extract methods."""
    body = node.child_by_field_name("body")
    if body is None:
        return
    for child in body.children:
        if child.type in ("method", "singleton_method"):
            _extract_function(
                child,
                file_path,
                module_id,
                class_id,
                graph,
            )


def _extract_function(
    node: Node,
    file_path: str,
    module_id: str,
    class_id: str | None,
    graph: CodeGraph,
) -> None:
    """Extract a Ruby method definition."""
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
        _collect_ruby_body_info(
            body,
            file_path,
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
        return_type=None,
        exception_types=tuple(exception_types),
        cyclomatic_complexity=complexity,
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


def _add_call_edges(
    calls: list[tuple[str, Node]],
    func_id: str,
    module_id: str,
    class_id: str | None,
    file_path: str,
    graph: CodeGraph,
) -> None:
    """Add CALLS edges for all collected call sites."""
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
    """Determine confidence for a Ruby call."""
    if "." in call_name:
        return EdgeConfidence.INFERRED
    return EdgeConfidence.CERTAIN


def _collect_ruby_body_info(
    body: Node,
    file_path: str,
    branches: list[BranchInfo],
    exception_types: list[TypeRef],
    calls: list[tuple[str, Node]],
) -> None:
    """Collect branches, exceptions, and calls from a Ruby body."""
    for child in body.children:
        _visit_ruby_body_node(
            child,
            file_path,
            branches,
            exception_types,
            calls,
        )


def _visit_ruby_body_node(
    node: Node,
    file_path: str,
    branches: list[BranchInfo],
    exception_types: list[TypeRef],
    calls: list[tuple[str, Node]],
) -> None:
    """Visit a single node in a Ruby method body."""
    if node.type in _BRANCH_NODE_TYPES:
        condition = _extract_condition_text(node)
        branches.append(
            BranchInfo(
                location=make_location(node, file_path),
                condition=condition,
            )
        )

    if _is_raise_call(node):
        _collect_raise_type(node, exception_types)

    if node.type == "call":
        call_name = _extract_ruby_call_name(node)
        if call_name and call_name not in ("require", "require_relative"):
            calls.append((call_name, node))

    for child in node.children:
        _visit_ruby_body_node(
            child,
            file_path,
            branches,
            exception_types,
            calls,
        )


def _extract_condition_text(node: Node) -> str:
    """Extract the condition expression from a branch node."""
    condition = node.child_by_field_name("condition")
    if condition is not None:
        return node_text(condition)
    return node.type


def _is_raise_call(node: Node) -> bool:
    """Check if a node is a `raise` call."""
    if node.type != "call":
        return False
    method = node.child_by_field_name("method")
    if method is not None and node_text(method) == "raise":
        return True
    return bool(node.children) and node_text(node.children[0]) == "raise"


def _collect_raise_type(
    node: Node,
    exception_types: list[TypeRef],
) -> None:
    """Extract the exception type from a raise call."""
    args = node.child_by_field_name("arguments")
    if args is None:
        return
    for child in args.children:
        if child.type == "constant":
            exception_types.append(TypeRef(name=node_text(child)))
            return
        if child.type == "call":
            method = child.child_by_field_name("method")
            if method is not None:
                exception_types.append(TypeRef(name=node_text(method)))
                return


def _extract_ruby_call_name(node: Node) -> str:
    """Extract the method name from a Ruby call node."""
    receiver = node.child_by_field_name("receiver")
    method = node.child_by_field_name("method")
    if method is None:
        # Try first child as method name
        if node.children:
            first = node.children[0]
            if first.type == "identifier":
                return node_text(first)
        return ""
    method_name = node_text(method)
    if receiver is not None:
        return f"{node_text(receiver)}.{method_name}"
    return method_name


def _extract_parameters(node: Node) -> list[Parameter]:
    """Extract parameters from a Ruby method definition."""
    params_node = node.child_by_field_name("parameters")
    if params_node is None:
        return []

    params: list[Parameter] = []
    for child in params_node.children:
        if child.type == "identifier":
            params.append(Parameter(name=node_text(child)))
        elif child.type == "optional_parameter":
            param = _parse_optional_parameter(child)
            if param is not None:
                params.append(param)
    return params


def _parse_optional_parameter(node: Node) -> Parameter | None:
    """Parse an optional_parameter node into a Parameter."""
    children = [c for c in node.children if c.type != "="]
    if len(children) < 2:
        return None
    name = node_text(children[0])
    default = node_text(children[-1])
    return Parameter(name=name, default=default)


def _extract_docstring(node: Node) -> str | None:
    """Extract comment block preceding a definition."""
    prev = node.prev_named_sibling
    if prev is not None and prev.type == "comment":
        text = node_text(prev)
        if text.startswith("#"):
            return text.lstrip("# ").strip()
    return None


def _check_require(node: Node, graph: CodeGraph) -> None:
    """Check if a call node is a require/require_relative."""
    method = node.child_by_field_name("method")
    if method is None:
        if node.children and node.children[0].type == "identifier":
            method = node.children[0]
        else:
            return
    method_name = node_text(method)
    if method_name not in ("require", "require_relative"):
        return
    args = node.child_by_field_name("arguments")
    if args is None:
        return
    for child in args.children:
        if child.type == "string":
            dep = _clean_string(node_text(child))
            if dep and dep not in graph.dependencies:
                graph.dependencies.append(dep)


def _clean_string(text: str) -> str:
    """Strip quote delimiters from a Ruby string."""
    if text.startswith('"') and text.endswith('"'):
        return text[1:-1]
    if text.startswith("'") and text.endswith("'"):
        return text[1:-1]
    return text


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
