"""PHP language parser using tree-sitter."""

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
from trailmark.parsers._php_linking import link_php_functions
from trailmark.parsers._walk import walk_owned

_BRANCH_NODE_TYPES = frozenset(
    {
        "if_statement",
        "for_statement",
        "foreach_statement",
        "while_statement",
        "do_statement",
        "switch_case",
        "catch_clause",
    }
)

_THROW_TYPES = frozenset({"throw_expression"})

_CALL_TYPES = frozenset(
    {
        "function_call_expression",
        "member_call_expression",
        "nullsafe_member_call_expression",
        "scoped_call_expression",
    }
)
_CALLABLE_TYPES = frozenset(
    {"function_definition", "anonymous_function", "arrow_function", "class_declaration"}
)

_EXTENSIONS = (".php",)

_CLASS_LIKE_TYPES = frozenset({"class_declaration", "interface_declaration", "trait_declaration"})

_NODE_KIND_MAP = {
    "class_declaration": NodeKind.CLASS,
    "interface_declaration": NodeKind.INTERFACE,
    "trait_declaration": NodeKind.TRAIT,
}


class PHPParser:
    """Parses PHP source files into CodeGraph using tree-sitter."""

    @property
    def language(self) -> str:
        return "php"

    def __init__(self) -> None:
        self._parser = Parser(get_language("php"))

    def parse_file(self, file_path: str) -> CodeGraph:
        """Parse a single PHP file into a CodeGraph."""
        source = Path(file_path).read_bytes()
        tree = self._parser.parse(source)
        graph = CodeGraph(language="php", root_path=file_path)
        module_id = module_id_from_path(file_path)
        _visit_module(tree.root_node, file_path, module_id, graph)
        link_php_functions(graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        """Parse all .php files under dir_path into a merged graph."""
        return parse_directory(
            self.parse_file,
            "php",
            dir_path,
            _EXTENSIONS,
        )


def _visit_module(
    root: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Walk the top-level of a PHP module, extracting nodes."""
    add_module_node(root, file_path, module_id, graph)
    _walk_children(root, file_path, module_id, graph)


def _walk_children(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Recursively walk children to find top-level definitions."""
    for child in node.children:
        if child.type == "program":
            _walk_children(child, file_path, module_id, graph)
        elif child.type == "namespace_definition":
            _extract_namespace(
                child,
                file_path,
                module_id,
                graph,
            )
        elif child.type in _CLASS_LIKE_TYPES:
            _extract_class(child, file_path, module_id, graph)
        elif child.type == "function_definition":
            _extract_function(
                child,
                file_path,
                module_id,
                None,
                graph,
            )
        elif child.type in {"anonymous_function", "arrow_function"}:
            _extract_function(
                child,
                file_path,
                module_id,
                None,
                graph,
                name_override=f"$lambda@{child.start_point.row + 1}_{child.start_point.column}",
            )
        elif child.type == "namespace_use_declaration":
            _extract_import(child, graph)
        else:
            _walk_children(child, file_path, module_id, graph)


def _extract_namespace(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a namespace definition node."""
    name_node = node.child_by_field_name("name")
    ns_name = node_text(name_node) if name_node is not None else ""
    ns_id = f"{module_id}:{ns_name.replace(chr(92), '.')}"
    location = make_location(node, file_path)

    ns_unit = CodeUnit(
        id=ns_id,
        name=ns_name,
        kind=NodeKind.NAMESPACE,
        location=location,
    )
    if ns_name:
        graph.nodes[ns_id] = ns_unit
        add_contains_edge(graph, module_id, ns_id)

    body = node.child_by_field_name("body")
    if body is not None:
        _walk_children(body, file_path, module_id, graph)


def _extract_class(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a class, interface, or trait definition."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    class_name = node_text(name_node)
    class_id = _php_reference_id(node, module_id, class_name)
    kind = _NODE_KIND_MAP.get(node.type, NodeKind.CLASS)
    location = make_location(node, file_path)

    class_unit = CodeUnit(
        id=class_id,
        name=class_name,
        kind=kind,
        location=location,
        docstring=_extract_docstring(node),
    )
    graph.nodes[class_id] = class_unit
    add_contains_edge(graph, _namespace_owner(node, module_id), class_id)

    _extract_inheritance(node, class_id, module_id, graph)
    _extract_implements(node, class_id, module_id, graph)
    _visit_class_body(node, file_path, module_id, class_id, graph)


def _extract_inheritance(
    node: Node,
    class_id: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract INHERITS edges from base_clause."""
    for child in node.children:
        if child.type == "base_clause":
            for name_child in child.children:
                if name_child.type == "name":
                    base_name = node_text(name_child)
                    base_id = _php_reference_id(node, module_id, base_name)
                    graph.edges.append(
                        CodeEdge(
                            source_id=class_id,
                            target_id=base_id,
                            kind=EdgeKind.INHERITS,
                            confidence=EdgeConfidence.INFERRED,
                        )
                    )


def _extract_implements(
    node: Node,
    class_id: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract IMPLEMENTS edges from class_interface_clause."""
    for child in node.children:
        if child.type == "class_interface_clause":
            for name_child in child.children:
                if name_child.type == "name":
                    iface_name = node_text(name_child)
                    iface_id = _php_reference_id(node, module_id, iface_name)
                    graph.edges.append(
                        CodeEdge(
                            source_id=class_id,
                            target_id=iface_id,
                            kind=EdgeKind.IMPLEMENTS,
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
        if child.type == "method_declaration":
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
    *,
    name_override: str | None = None,
    owner_id: str | None = None,
) -> None:
    """Extract a function or method definition."""
    name_node = node.child_by_field_name("name")
    if name_node is None and name_override is None:
        return
    func_name = name_override or (node_text(name_node) if name_node is not None else "")

    if owner_id is not None:
        func_id = f"{owner_id}.{func_name}"
        kind = NodeKind.FUNCTION
        container_id = owner_id
    elif class_id is not None:
        func_id = f"{class_id}.{func_name}"
        kind = NodeKind.METHOD
        container_id = class_id
    else:
        func_id = _php_reference_id(node, module_id, func_name)
        kind = NodeKind.FUNCTION
        container_id = _namespace_owner(node, module_id)

    params = _extract_parameters(node)
    return_type = _extract_return_type(node)
    body = node.child_by_field_name("body")

    branches: list[BranchInfo] = []
    exception_types: list[TypeRef] = []
    calls: list[tuple[str, Node]] = []

    if body is not None:
        _collect_php_body_info(
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
        return_type=return_type,
        exception_types=tuple(exception_types),
        cyclomatic_complexity=complexity,
        branches=tuple(branches),
        docstring=_extract_docstring(node),
    )
    graph.nodes[func_id] = unit
    add_contains_edge(graph, container_id, func_id)
    if body is not None:
        for nested in walk_owned(body, boundaries=_CALLABLE_TYPES):
            if nested.type in {"function_definition", "anonymous_function", "arrow_function"}:
                name = nested.child_by_field_name("name")
                _extract_function(
                    nested,
                    file_path,
                    module_id,
                    class_id,
                    graph,
                    owner_id=func_id,
                    name_override=node_text(name)
                    if name is not None
                    else f"$lambda@{nested.start_point.row + 1}_{nested.start_point.column}",
                )

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
        if call_node.type == "function_call_expression" and not call_name.startswith("$"):
            target_id = _php_reference_id(call_node, module_id, call_name)
        elif call_node.type == "scoped_call_expression":
            scope, _, method = call_name.partition("::")
            if scope == "self" and class_id is not None:
                target_id, confidence = f"{class_id}.{method}", EdgeConfidence.CERTAIN
            elif scope == "parent" and class_id is not None:
                bases = [
                    edge.target_id
                    for edge in graph.edges
                    if edge.source_id == class_id and edge.kind == EdgeKind.INHERITS
                ]
                if len(bases) == 1:
                    target_id = f"{bases[0]}.{method}"
            elif scope == "static":
                confidence = EdgeConfidence.UNCERTAIN
            elif not scope.startswith("$"):
                target_id = f"{_php_reference_id(call_node, module_id, scope)}.{method}"
        attributes: tuple[tuple[str, str | bool], ...] = ()
        if call_node.type == "function_call_expression" and not call_name.startswith("$"):
            attributes = (
                (
                    "php_function_target",
                    _php_reference_id(call_node, module_id, call_name).split(":", 1)[-1],
                ),
                ("php_global_fallback", "\\" not in call_name),
            )
        graph.edges.append(
            CodeEdge(
                source_id=func_id,
                target_id=target_id,
                kind=EdgeKind.CALLS,
                confidence=confidence,
                location=make_location(call_node, file_path),
                attributes=attributes,
            )
        )


def _call_confidence(call_name: str) -> EdgeConfidence:
    """Determine confidence for a PHP call."""
    if "->" in call_name or "::" in call_name:
        return EdgeConfidence.INFERRED
    return EdgeConfidence.CERTAIN


def _collect_php_body_info(
    body: Node,
    file_path: str,
    branches: list[BranchInfo],
    exception_types: list[TypeRef],
    calls: list[tuple[str, Node]],
) -> None:
    """Collect branches, exceptions, and calls from a PHP body."""
    _visit_php_body_node(
        body,
        file_path,
        branches,
        exception_types,
        calls,
    )


def _visit_php_body_node(
    node: Node,
    file_path: str,
    branches: list[BranchInfo],
    exception_types: list[TypeRef],
    calls: list[tuple[str, Node]],
) -> None:
    """Visit a single node in a PHP function body."""
    if node.type in _CALLABLE_TYPES:
        return
    if node.type in _BRANCH_NODE_TYPES:
        condition = _extract_condition_text(node)
        branches.append(
            BranchInfo(
                location=make_location(node, file_path),
                condition=condition,
            )
        )

    if node.type in _THROW_TYPES:
        _collect_throw_type(node, exception_types)

    if node.type in _CALL_TYPES:
        call_name = _extract_php_call_name(node)
        arguments = node.child_by_field_name("arguments")
        acquisition = arguments is not None and any(
            child.type == "variadic_placeholder" for child in arguments.named_children
        )
        if call_name and not acquisition:
            calls.append((call_name, node))

    for child in node.children:
        _visit_php_body_node(
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


def _collect_throw_type(
    node: Node,
    exception_types: list[TypeRef],
) -> None:
    """Extract the exception type from a throw expression."""
    for child in node.children:
        if child.type == "object_creation_expression":
            for sub in child.children:
                if sub.type == "name":
                    exception_types.append(TypeRef(name=node_text(sub)))
                    return


def _extract_php_call_name(node: Node) -> str:
    """Extract the function/method name from a PHP call node."""
    if node.type == "function_call_expression":
        func = node.child_by_field_name("function")
        if func is not None:
            return node_text(func)
    elif node.type in {"member_call_expression", "nullsafe_member_call_expression"}:
        name = node.child_by_field_name("name")
        obj = node.child_by_field_name("object")
        if name is not None and obj is not None:
            return f"{node_text(obj)}->{node_text(name)}"
        if name is not None:
            return node_text(name)
    elif node.type == "scoped_call_expression":
        scope = node.child_by_field_name("scope")
        name = node.child_by_field_name("name")
        if scope is not None and name is not None:
            return f"{node_text(scope)}::{node_text(name)}"
    return ""


def _extract_parameters(node: Node) -> list[Parameter]:
    """Extract parameters from a PHP function/method definition."""
    params_node = node.child_by_field_name("parameters")
    if params_node is None:
        return []

    params: list[Parameter] = []
    for child in params_node.children:
        if child.type == "simple_parameter":
            param = _parse_simple_parameter(child)
            if param is not None:
                params.append(param)
    return params


def _parse_simple_parameter(node: Node) -> Parameter | None:
    """Parse a single simple_parameter node into a Parameter."""
    name = ""
    type_ref = None
    default = None

    for child in node.children:
        if child.type == "variable_name":
            raw = node_text(child)
            name = raw.lstrip("$")
        elif child.type in (
            "named_type",
            "union_type",
            "optional_type",
            "primitive_type",
        ):
            type_ref = TypeRef(name=node_text(child))
        elif child.type == "=":
            continue
        elif name and child.type not in ("variable_name", ","):
            default = node_text(child)

    if not name:
        return None
    return Parameter(name=name, type_ref=type_ref, default=default)


def _extract_return_type(node: Node) -> TypeRef | None:
    """Extract return type from a PHP function/method definition."""
    for child in node.children:
        if child.type == ":":
            idx = node.children.index(child)
            if idx + 1 < len(node.children):
                ret = node.children[idx + 1]
                if ret.type in (
                    "named_type",
                    "union_type",
                    "optional_type",
                    "primitive_type",
                ):
                    return TypeRef(name=node_text(ret))
    return_type = node.child_by_field_name("return_type")
    if return_type is not None:
        return TypeRef(name=node_text(return_type))
    return None


def _extract_docstring(node: Node) -> str | None:
    """Extract PHPDoc comment preceding a definition."""
    prev = node.prev_named_sibling
    if prev is not None and prev.type == "comment":
        text = node_text(prev)
        if text.startswith("/**"):
            return _clean_phpdoc(text)
    return None


def _clean_phpdoc(text: str) -> str:
    """Strip PHPDoc delimiters and leading asterisks."""
    text = text.strip()
    if text.startswith("/**"):
        text = text[3:]
    if text.endswith("*/"):
        text = text[:-2]
    lines = []
    for line in text.split("\n"):
        cleaned = line.strip().lstrip("* ").strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _extract_import(node: Node, graph: CodeGraph) -> None:
    """Extract namespace use declarations as dependencies."""
    for child in node.children:
        if child.type == "namespace_use_clause":
            text = node_text(child)
            parts = text.split("\\")
            dep_name = parts[0] if parts else text
            if dep_name and dep_name not in graph.dependencies:
                graph.dependencies.append(dep_name)


def _resolve_call_target(
    call_name: str,
    module_id: str,
    class_id: str | None,
) -> str:
    """Resolve a call name to a target node ID (best effort)."""
    if call_name.startswith("$this->") and class_id is not None:
        method_name = call_name[7:]
        return f"{class_id}.{method_name}"
    if "->" not in call_name and "::" not in call_name:
        return f"{module_id}:{call_name}"
    return call_name


def _namespace(node: Node) -> str:
    current: Node | None = node
    while current is not None:
        if current.type == "namespace_definition":
            name = current.child_by_field_name("name")
            return node_text(name) if name is not None else ""
        previous = current.prev_named_sibling
        while previous is not None:
            if (
                previous.type == "namespace_definition"
                and previous.child_by_field_name("body") is None
            ):
                name = previous.child_by_field_name("name")
                return node_text(name) if name is not None else ""
            previous = previous.prev_named_sibling
        current = current.parent
    return ""


def _namespace_owner(node: Node, module: str) -> str:
    namespace = _namespace(node).replace("\\", ".")
    return f"{module}:{namespace}" if namespace else module


def _php_reference_id(node: Node, module: str, name: str) -> str:
    absolute = name.startswith("\\")
    name = name.lstrip("\\").replace("\\", ".")
    namespace = "" if absolute else _namespace(node).replace("\\", ".")
    return f"{module}:{namespace}.{name}" if namespace else f"{module}:{name}"
