"""Java language parser using tree-sitter."""

from __future__ import annotations

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
from trailmark.parsers._binding_index import binding_scope
from trailmark.parsers._bindings import guard_call
from trailmark.parsers._common import (
    add_contains_edge,
    add_module_node,
    compute_complexity,
    extract_type_parameters,
    link_type_relationships,
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
)
from trailmark.parsers._nesting import bounded_nesting
from trailmark.parsers._walk import walk_owned

_BRANCH_NODE_TYPES = frozenset(
    {
        "if_statement",
        "for_statement",
        "enhanced_for_statement",
        "while_statement",
        "do_statement",
        "switch_block_statement_group",
        "catch_clause",
        "ternary_expression",
        "conditional_expression",
    }
)

_THROW_TYPES = frozenset({"throw_statement"})

_EXTENSIONS = (".java",)

_CLASS_NODE_KINDS: dict[str, NodeKind] = {
    "class_declaration": NodeKind.CLASS,
    "interface_declaration": NodeKind.INTERFACE,
    "enum_declaration": NodeKind.ENUM,
}

_FUNCTION_TYPES = frozenset(
    {"method_declaration", "constructor_declaration"},
)


class JavaParser:
    """Parses Java source files into CodeGraph using tree-sitter."""

    @property
    def language(self) -> str:
        return "java"

    def __init__(self) -> None:
        self._parser = Parser(get_language("java"))

    @binding_scope
    def parse_file(self, file_path: str) -> CodeGraph:
        """Parse a single Java file into a CodeGraph."""
        source = Path(file_path).read_bytes()
        tree = self._parser.parse(source)
        graph = CodeGraph(language="java", root_path=file_path)
        module_id = module_id_from_path(file_path)
        with callable_scope(graph):
            _visit_module(tree.root_node, file_path, module_id, graph)
        link_type_relationships(graph)
        bind_lexical_calls(graph)
        _bind_constructors(graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        """Parse all .java files under dir_path into a merged graph."""
        return parse_directory(
            self.parse_file,
            "java",
            dir_path,
            _EXTENSIONS,
            link_calls=_bind_constructors,
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
    if child.type in _CLASS_NODE_KINDS:
        _extract_class(child, file_path, module_id, graph)
    elif child.type in _FUNCTION_TYPES:
        _extract_function(
            child,
            file_path,
            module_id,
            None,
            graph,
        )
    elif child.type == "import_declaration":
        _extract_import(child, graph)


@bounded_nesting
def _extract_class(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
    parent_id: str | None = None,
    *,
    name_override: str | None = None,
) -> None:
    """Extract a class, interface, or enum declaration."""
    name_node = node.child_by_field_name("name")
    if name_node is None and name_override is None:
        return
    class_name = name_override or (node_text(name_node) if name_node is not None else "")
    class_id = f"{parent_id}.{class_name}" if parent_id else f"{module_id}:{class_name}"
    kind = _CLASS_NODE_KINDS.get(node.type, NodeKind.CLASS)
    location = make_location(node, file_path)

    class_unit = CodeUnit(
        id=class_id,
        name=class_name,
        kind=kind,
        location=location,
        type_parameters=extract_type_parameters(node),
        docstring=_extract_docstring(node),
        attributes=(("java_servlet_base", _servlet_base(node)),),
    )
    add_type(graph, class_unit)
    add_contains_edge(graph, parent_id or module_id, class_id)

    _extract_inheritance(node, class_id, module_id, graph)
    _extract_implements(node, class_id, module_id, graph)
    _visit_class_body(node, file_path, module_id, class_id, graph)


def _visit_class_body(
    node: Node,
    file_path: str,
    module_id: str,
    class_id: str,
    graph: CodeGraph,
) -> None:
    """Visit children of a class body."""
    body = node.child_by_field_name("body") or next(
        (c for c in node.named_children if c.type == "class_body"), None
    )
    if body is None:
        return
    children = list(reversed(body.children))
    while children:
        child = children.pop()
        if child.type == "enum_body_declarations":
            children.extend(reversed(child.children))
            continue
        if child.type in _FUNCTION_TYPES:
            _extract_function(
                child,
                file_path,
                module_id,
                class_id,
                graph,
            )

        elif child.type in _CLASS_NODE_KINDS:
            _extract_class(child, file_path, module_id, graph, class_id)


def _extract_inheritance(
    node: Node,
    class_id: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract INHERITS edge from superclass field."""
    superclass = node.child_by_field_name("superclass")
    if superclass is None:
        return
    for child in superclass.children:
        if child.type in {"type_identifier", "scoped_type_identifier", "generic_type"}:
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
            return


def _extract_implements(
    node: Node,
    class_id: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract IMPLEMENTS edges from interfaces field."""
    interfaces = node.child_by_field_name("interfaces")
    if interfaces is None:
        return
    for child in interfaces.children:
        iface_name = _interface_type_name(child)
        if iface_name:
            iface_id = f"{module_id}:{iface_name}"
            graph.edges.append(
                CodeEdge(
                    source_id=class_id,
                    target_id=iface_id,
                    kind=EdgeKind.IMPLEMENTS,
                    confidence=EdgeConfidence.INFERRED,
                )
            )


def _interface_type_name(node: Node) -> str:
    """Extract interface name from a type_list child."""
    if node.type in ("type_identifier", "generic_type"):
        return node_text(node)
    for child in node.children:
        if child.type in ("type_identifier", "generic_type"):
            return node_text(child)
    return ""


@bounded_nesting
def _extract_function(
    node: Node,
    file_path: str,
    module_id: str,
    class_id: str | None,
    graph: CodeGraph,
    *,
    name_override: str | None = None,
) -> None:
    """Extract a method or constructor declaration."""
    func_name = name_override or _get_func_name(node)
    if not func_name:
        return

    func_id, kind, container_id = _func_identity(
        func_name,
        module_id,
        class_id,
    )
    if node.type == "lambda_expression":
        kind = NodeKind.FUNCTION
    unit, calls = _function_unit(node, file_path, func_id=func_id, func_name=func_name, kind=kind)
    body = node.child_by_field_name("body")
    func_id = add_callable(graph, unit)
    add_contains_edge(graph, container_id, func_id)

    _extract_nested_callables(body, file_path, module_id, func_id, graph)
    _add_call_edges(
        calls,
        func_id,
        module_id,
        class_id,
        file_path=file_path,
        graph=graph,
    )


def _get_func_name(node: Node) -> str:
    """Get function name from a method or constructor node."""
    name_node = node.child_by_field_name("name")
    if name_node is not None:
        return node_text(name_node)
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
    if node.type in {"lambda_expression", "class_declaration", "class_body"}:
        return
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

    if node.type == "method_invocation":
        call_name = _extract_call_name(node)
        if call_name:
            calls.append((call_name, node))

    if node.type in {"object_creation_expression", "explicit_constructor_invocation"}:
        target = node.child_by_field_name("type") or node.child_by_field_name("constructor")
        if target is not None:
            calls.append((node_text(target), node))
    for child in node.children:
        _visit_node(
            child,
            file_path,
            branches,
            exception_types,
            calls,
        )


def _extract_call_name(node: Node) -> str:
    """Extract function name from a method_invocation node."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return ""
    obj_node = node.child_by_field_name("object")
    if obj_node is not None:
        return f"{node_text(obj_node)}.{node_text(name_node)}"
    return node_text(name_node)


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
    type_node = node.child_by_field_name("type")
    if type_node is not None:
        exception_types.append(TypeRef(name=node_text(type_node)))
        return
    for child in node.children:
        if child.type in ("type_identifier", "scoped_type_id"):
            exception_types.append(TypeRef(name=node_text(child)))
            return


def _call_confidence(call_name: str) -> EdgeConfidence:
    """Determine call confidence based on call form."""
    if "." not in call_name:
        return EdgeConfidence.CERTAIN
    return EdgeConfidence.INFERRED


def _add_call_edges(
    calls: list[tuple[str, Node]],
    func_id: str,
    module_id: str,
    class_id: str | None,
    *,
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
        construction = call_node.type in {
            "object_creation_expression",
            "explicit_constructor_invocation",
        }
        if construction:
            target_id = f"{func_id}.$constructor.{call_name}"
        confidence = EdgeConfidence.UNCERTAIN if construction else _call_confidence(call_name)
        graph.edges.append(
            guard_call(
                CodeEdge(
                    source_id=func_id,
                    target_id=target_id,
                    kind=EdgeKind.CALLS,
                    confidence=confidence,
                    location=make_location(call_node, file_path),
                    attributes=(("binding_blocked", True), ("constructor_type", call_name))
                    if construction
                    else (("bare_name", call_name),)
                    if "." not in call_name
                    else (),
                ),
                call_node,
                graph=graph,
            )
        )


def _extract_parameters(node: Node) -> list[Parameter]:
    """Extract parameters from a method declaration."""
    params_node = node.child_by_field_name("parameters")
    if params_node is None:
        return []

    params: list[Parameter] = []
    for child in params_node.children:
        if child.type == "formal_parameter":
            param = _parse_single_param(child)
            if param is not None:
                params.append(param)
    return params


def _parse_single_param(node: Node) -> Parameter | None:
    """Parse a single formal_parameter node into a Parameter."""
    name_node = node.child_by_field_name("name")
    type_node = node.child_by_field_name("type")
    if name_node is None:
        return None
    name = node_text(name_node)
    type_ref = _parse_type_node(type_node) if type_node else None
    return Parameter(name=name, type_ref=type_ref)


def _parse_type_node(type_node: Node) -> TypeRef:
    """Parse a Java type node into a TypeRef."""
    if type_node.type == "generic_type":
        return _parse_generic_type(type_node)
    if type_node.type == "array_type":
        inner = type_node.children[0] if type_node.children else None
        if inner:
            base = _parse_type_node(inner)
            return TypeRef(name=f"{base.name}[]")
        return TypeRef(name=node_text(type_node))
    return TypeRef(name=node_text(type_node))


def _parse_generic_type(node: Node) -> TypeRef:
    """Parse a generic type like List<Integer> into a TypeRef."""
    name = ""
    args: list[TypeRef] = []
    for child in node.children:
        if child.type == "type_identifier":
            name = node_text(child)
        elif child.type == "type_arguments":
            for sub in child.children:
                if sub.is_named and sub.type != ",":
                    args.append(_parse_type_node(sub))
    return TypeRef(name=name, generic_args=tuple(args))


def _extract_return_type(node: Node) -> TypeRef | None:
    """Extract return type from a method declaration."""
    type_node = node.child_by_field_name("type")
    if type_node is None:
        return None
    return _parse_type_node(type_node)


def _extract_docstring(node: Node) -> str | None:
    """Extract Javadoc comments (/** ... */) preceding a node."""
    sibling = node.prev_named_sibling
    if sibling is None:
        return None
    if sibling.type in ("block_comment", "comment"):
        text = node_text(sibling)
        if text.startswith("/**"):
            return _clean_javadoc(text)
    return None


def _clean_javadoc(text: str) -> str:
    """Strip Javadoc delimiters and leading asterisks."""
    text = text[3:]
    if text.endswith("*/"):
        text = text[:-2]
    lines: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("*"):
            stripped = stripped[1:].strip()
        lines.append(stripped)
    return "\n".join(line for line in lines if line).strip()


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
    """Extract import declarations as dependency info."""
    parts: list[str] = []
    for child in node.children:
        if child.is_named:
            text = node_text(child)
            parts.append(text)
    if parts:
        full_path = parts[0]
        pkg_name = full_path.split(".")[0]
        if pkg_name not in graph.dependencies:
            graph.dependencies.append(pkg_name)


def _servlet_base(node: Node) -> bool:
    superclass = node.child_by_field_name("superclass")
    raw = node_text(superclass).removeprefix("extends").strip() if superclass is not None else ""
    qualified = {"javax.servlet.http.HttpServlet", "jakarta.servlet.http.HttpServlet"}
    if raw in qualified:
        return True
    root = node
    while root.parent is not None:
        root = root.parent
    imports = {
        node_text(c).removeprefix("import").strip().removesuffix(";")
        for c in root.named_children
        if c.type == "import_declaration"
    }
    return raw == "HttpServlet" and bool(
        imports & (qualified | {"javax.servlet.http.*", "jakarta.servlet.http.*"})
    )


def _bind_constructors(graph: CodeGraph) -> None:
    owners = {e.target_id: e.source_id for e in graph.edges if e.kind == EdgeKind.CONTAINS}
    bases = {e.source_id: e.target_id for e in graph.edges if e.kind == EdgeKind.INHERITS}
    types: dict[str, list[str]] = {}
    constructors: dict[str, list[str]] = {}
    for unit in graph.nodes.values():
        if unit.kind == NodeKind.CLASS:
            types.setdefault(unit.name, []).append(unit.id)
        _index_constructor(unit, graph, owners, constructors)
    graph.edges[:] = [_constructor_edge(e, owners, bases, types, constructors) for e in graph.edges]


def _index_constructor(
    unit: CodeUnit,
    graph: CodeGraph,
    owners: dict[str, str],
    constructors: dict[str, list[str]],
) -> None:
    owner = graph.nodes.get(owners.get(unit.id, ""))
    if unit.kind == NodeKind.METHOD and owner is not None and unit.name == owner.name:
        constructors.setdefault(owner.id, []).append(unit.id)


def _constructor_edge(
    edge: CodeEdge,
    owners: dict[str, str],
    bases: dict[str, str],
    types: dict[str, list[str]],
    constructors: dict[str, list[str]],
) -> CodeEdge:
    name = dict(edge.attributes).get("constructor_type")
    if not isinstance(name, str):
        return edge
    owner = owners.get(edge.source_id, "")
    candidates = _constructor_types(name, owner, bases, types)
    targets = constructors.get(candidates[0], []) if len(candidates) == 1 else []
    if len(targets) == 1:
        return replace(
            edge,
            target_id=targets[0],
            confidence=EdgeConfidence.INFERRED,
            attributes=tuple((k, v) for k, v in edge.attributes if k != "binding_blocked"),
        )
    return edge


def _constructor_types(
    name: str,
    owner: str,
    bases: dict[str, str],
    types: dict[str, list[str]],
) -> list[str]:
    if name == "this":
        return [owner]
    if name == "super":
        return [bases[owner]] if owner in bases else []
    return types.get(name, [])


def _extract_nested_callables(
    body: Node | None, file_path: str, module_id: str, func_id: str, graph: CodeGraph
) -> None:
    if body is not None:
        for nested in walk_owned(
            body, boundaries=frozenset({"lambda_expression", "class_declaration", "class_body"})
        ):
            if nested.type == "lambda_expression":
                _extract_function(
                    nested,
                    file_path,
                    module_id,
                    func_id,
                    graph,
                    name_override=(
                        f"$lambda@{nested.start_point.row + 1}_{nested.start_point.column}"
                    ),
                )
            elif nested.type == "class_declaration":
                _extract_class(nested, file_path, module_id, graph, func_id)
            elif (
                nested.type == "class_body"
                and nested.parent is not None
                and nested.parent.type == "object_creation_expression"
            ):
                _extract_class(
                    nested.parent,
                    file_path,
                    module_id,
                    graph,
                    func_id,
                    name_override=(
                        f"$anonymous@{nested.start_point.row + 1}_{nested.start_point.column}"
                    ),
                )


def _function_unit(
    node: Node, file_path: str, *, func_id: str, func_name: str, kind: NodeKind
) -> tuple[CodeUnit, list[tuple[str, Node]]]:
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
        type_parameters=extract_type_parameters(node),
        cyclomatic_complexity=compute_complexity(branches),
        branches=tuple(branches),
        docstring=_extract_docstring(node),
        attributes=(
            (
                "java_private",
                any(
                    c.type == "modifiers" and "private" in node_text(c).split()
                    for c in node.named_children
                ),
            ),
            (
                "java_static",
                any(
                    c.type == "modifiers" and "static" in node_text(c).split()
                    for c in node.named_children
                ),
            ),
        ),
    )
    return unit, calls
