"""Shared JavaScript/TypeScript callable discovery and lexical ownership."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace

from tree_sitter import Node

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import (
    BranchInfo,
    CodeUnit,
    NodeKind,
    NodeOrigin,
    SourceLocation,
    TypeRef,
)
from trailmark.parsers._binding_index import record_unit, syntax_nodes
from trailmark.parsers._bindings import _guard_named_call, guard_call, namespace_receiver_matches
from trailmark.parsers._callable_syntax import callable_parent, unwrap_callable
from trailmark.parsers._common import (
    _collect_throw_type,
    _extract_condition_text,
    add_contains_edge,
    extract_call_name,
    link_cross_file_calls,
    make_location,
    node_text,
)
from trailmark.parsers._declarations import _runtime_target, prepare_call_bindings
from trailmark.parsers._ecmascript_imports import ImportBinding, import_bindings
from trailmark.parsers._nesting import nesting_diagnostic
from trailmark.parsers._walk import owned_syntax

CLASSES = frozenset({"class_declaration", "abstract_class_declaration", "class"})
NAMESPACES = frozenset({"internal_module", "module"})
CALL_SITES = frozenset({"call_expression", "new_expression"})


def link_ecmascript_calls(graph: CodeGraph) -> None:
    """Resolve exact imported classes after their source declarations are merged."""
    link_cross_file_calls(graph)
    bind_imported_constructors(graph)


def bind_imported_constructors(graph: CodeGraph) -> None:
    """Point a proven named-import construction at its inspected constructor body."""
    for index, edge in enumerate(graph.edges):
        attributes = dict(edge.attributes)
        if (
            edge.kind != EdgeKind.CALLS
            or not attributes.get("import_binding")
            or not attributes.get("constructor_call")
            or attributes.get("binding_blocked")
        ):
            continue
        target = graph.nodes.get(edge.target_id)
        if target is None or target.kind != NodeKind.CLASS or target.origin != NodeOrigin.SOURCE:
            continue
        constructor = _class_constructor(target, graph)
        if constructor is not None:
            graph.edges[index] = replace(edge, target_id=constructor.id)


def _class_constructor(target: CodeUnit, graph: CodeGraph) -> CodeUnit | None:
    constructors = []
    for edge in graph.edges:
        if edge.kind != EdgeKind.CONTAINS or edge.source_id != target.id:
            continue
        unit = graph.nodes[edge.target_id]
        attributes = dict(unit.attributes)
        if (
            unit.kind == NodeKind.METHOD
            and unit.name == "constructor"
            and not attributes.get("ecmascript_static")
            and not attributes.get("declaration_only")
        ):
            constructors.append(unit)
    return constructors[0] if len(constructors) == 1 else None


CALLABLES = frozenset(
    {
        "function_declaration",
        "generator_function_declaration",
        "arrow_function",
        "function_expression",
        "generator_function",
        "method_definition",
        "function",
    }
)


def body_info(
    body: Node | None,
    path: str,
    *,
    branch_types: frozenset[str],
    throw_types: frozenset[str],
    boundaries: frozenset[str],
    parameters: Node | None = None,
) -> tuple[list[BranchInfo], list[TypeRef], list[tuple[str, Node]]]:
    """Collect ECMAScript body sites with the selected JS, TS, or TSX grammar."""
    branches: list[BranchInfo] = []
    exceptions: list[TypeRef] = []
    calls: list[tuple[str, Node]] = []
    for region in (parameters, body):
        if region is None or region == parameters and not region.named_child_count:
            continue
        types = branch_types | throw_types | CALL_SITES
        region_boundaries = boundaries | {"decorator"} if region == parameters else boundaries
        for node in _execution_sites(region, types, region_boundaries):
            if node.type in branch_types:
                branches.append(
                    BranchInfo(make_location(node, path), _extract_condition_text(node))
                )
            if node.type in throw_types:
                _collect_throw_type(node, exceptions)
            if node.type in CALL_SITES and (name := _execution_name(node)):
                calls.append((name, node))
    return branches, exceptions, calls


def _execution_sites(
    body: Node, types: frozenset[str], boundaries: frozenset[str]
) -> Iterator[Node]:
    """Include class evaluation sites while keeping method bodies with their owners."""
    boundaries = boundaries - CLASSES
    for node in owned_syntax(
        body, types | {"method_definition"}, boundaries=boundaries, tokens=types
    ):
        if node.type == "method_definition":
            name = node.child_by_field_name("name")
            if name is not None and name.type == "computed_property_name":
                yield from owned_syntax(name, types, boundaries=boundaries, tokens=types)
        else:
            yield node


def discover_callables(
    root: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
    extract: Callable[[Node, str, str, str | None], str | None],
) -> None:
    """Preserve nested and anonymous bodies without implying their execution."""
    by_location = _callable_locations(graph, file_path)
    stack: list[tuple[int, str, str | None]] = [(root.end_byte + 1, module_id, None)]
    calls: dict[SourceLocation, Node] = {}
    for node in _bounded_discovery(root, file_path, graph):
        if node.type in CALL_SITES:
            calls[make_location(node, file_path)] = node
            continue
        while node.start_byte >= stack[-1][0]:
            stack.pop()
        _, owner, class_owner = stack[-1]
        if node.type in CALLABLES:
            class_owner = _callable_class_owner(node, class_owner)
            node_id = _discover_callable(node, owner, class_owner, by_location, extract)
            if node_id is None:
                continue
            owner = node_id
        else:
            position = (node.start_point.row + 1, node.start_point.column)
            owner = by_location.get(position) or _discover_container(
                node, owner, graph, file_path=file_path
            )
            class_owner = owner if node.type in CLASSES else None
        stack.append((node.end_byte, owner, class_owner))
    # Forward nested declarations are available only after discovery completes.
    _guard_discovered_calls(graph, calls, import_bindings(root, file_path, graph.language))


def _bounded_discovery(root: Node, file_path: str, graph: CodeGraph) -> list[Node]:
    containers = CALLABLES | CLASSES | NAMESPACES
    nodes = []
    enclosing: list[int] = []
    skipped_until = -1
    for node in syntax_nodes(root, containers | CALL_SITES):
        if node.start_byte < skipped_until:
            continue
        while enclosing and node.start_byte >= enclosing[-1]:
            enclosing.pop()
        if node.type in containers:
            if len(enclosing) >= 48:
                graph.parse_diagnostics.append(nesting_diagnostic(node, file_path, graph.language))
                skipped_until = node.end_byte
                continue
            enclosing.append(node.end_byte)
        nodes.append(node)
    return nodes


def _discover_container(node: Node, owner: str, graph: CodeGraph, *, file_path: str) -> str:
    namespace = node.type in NAMESPACES
    name_node = node.child_by_field_name("name")
    name = (
        node_text(name_node)
        if name_node is not None
        else f"$class@{node.start_point.row + 1}_{node.start_point.column}"
    )
    if name_node is not None and name_node.type == "string":
        name = name[1:-1]
    separator = "." if ":" in owner else ":"
    node_id = owner + separator + name
    attributes = ()
    if namespace and node_id in graph.nodes:
        if graph.nodes[node_id].kind not in {NodeKind.FUNCTION, NodeKind.METHOD}:
            return node_id
        attributes = (("merged_callable", node_id),)
        node_id += ".$namespace"
        if node_id in graph.nodes:
            return node_id
    if node_id in graph.nodes:
        node_id += f"@{node.start_point.row + 1}_{node.start_point.column}"
    graph.nodes[node_id] = CodeUnit(
        id=node_id,
        name=name,
        kind=NodeKind.NAMESPACE if namespace else NodeKind.CLASS,
        location=make_location(node, file_path),
        attributes=attributes,
    )
    if not namespace:
        record_unit(graph.nodes[node_id])
    add_contains_edge(graph, owner, node_id)
    return node_id


def _callable_class_owner(node: Node, owner: str | None) -> str | None:
    if node.type == "arrow_function" or (
        node.type == "method_definition"
        and node.parent is not None
        and node.parent.type == "class_body"
    ):
        return owner
    return None


def _discover_callable(
    node: Node,
    owner: str,
    class_owner: str | None,
    by_location: dict[tuple[int, int | None], str],
    extract: Callable[[Node, str, str, str | None], str | None],
) -> str | None:
    position = (node.start_point.row + 1, node.start_point.column)
    known = by_location.get(position)
    return known if known is not None else extract(node, _discovered_name(node), owner, class_owner)


def _callable_locations(graph: CodeGraph, path: str) -> dict[tuple[int, int | None], str]:
    return {
        (unit.location.start_line, unit.location.start_col): unit.id
        for unit in graph.nodes.values()
        if unit.location.file_path == path and unit.kind != NodeKind.MODULE
    }


def declared_name(node: Node, override: str | None) -> str | None:
    """Read a declaration name while retaining an explicit anonymous identity."""
    if override is not None:
        return override
    name = node.child_by_field_name("name")
    return node_text(name) if name is not None else None


def _guard_discovered_calls(
    graph: CodeGraph, calls: dict[SourceLocation, Node], imports: dict[str, ImportBinding]
) -> None:
    prepare_call_bindings(graph)
    aliases = {
        original: unit.id
        for unit in graph.nodes.values()
        if isinstance((original := dict(unit.attributes).get("merged_callable")), str)
    }
    maximum = max((k.count(".") for k in aliases), default=0)
    graph.edges[:] = [
        _construction_edge(
            _guard_context_call(edge, calls[edge.location], graph, aliases, maximum, imports),
            calls[edge.location],
            graph,
        )
        if edge.kind == EdgeKind.CALLS
        and not dict(edge.attributes).get("import_binding")
        and edge.location is not None
        and edge.location in calls
        else edge
        for edge in graph.edges
    ]


def _guard_context_call(
    edge: CodeEdge,
    occurrence: Node,
    graph: CodeGraph,
    aliases: dict[str, str],
    maximum: int,
    imports: dict[str, ImportBinding],
) -> CodeEdge:
    name = _execution_name(occurrence)
    if name.startswith(("this.", "super.")) and _class_initializer(occurrence):
        return replace(edge, target_id=name, confidence=EdgeConfidence.UNCERTAIN)
    constructor = occurrence.child_by_field_name("constructor")
    constructor = unwrap_callable(constructor) if constructor is not None else None
    if (
        occurrence.type == "new_expression"
        and constructor is not None
        and constructor.type == "identifier"
    ):
        edge = _guard_named_call(edge, occurrence, graph, name)
    else:
        edge = guard_call(edge, occurrence, graph=graph)
    imported = imports.get(name)
    if imported is not None and edge.target_id == edge.source_id.split(":", 1)[0] + ":" + name:
        return replace(
            edge,
            target_id=imported.target,
            confidence=EdgeConfidence.UNCERTAIN if imported.type_only else EdgeConfidence.INFERRED,
            attributes=(
                *edge.attributes,
                ("import_binding", True),
                ("binding_blocked", imported.type_only),
            ),
        )
    if aliases and "." in name:
        qualified = edge.source_id.split(":", 1)[0] + ":" + name
        target = _runtime_target(qualified, aliases, maximum=maximum)
        receiver = edge.source_id.split(":", 1)[0] + ":" + name.split(".", 1)[0]
        if (
            target != qualified
            and target in graph.nodes
            and namespace_receiver_matches(edge, occurrence, graph, receiver)
        ):
            return replace(edge, target_id=target)
    return edge


def _execution_name(node: Node) -> str:
    if node.type != "new_expression":
        return extract_call_name(node)
    constructor = node.child_by_field_name("constructor")
    constructor = unwrap_callable(constructor) if constructor is not None else None
    if constructor is not None and constructor.type in {"identifier", "member_expression"}:
        return node_text(constructor)
    return f"$constructor@{node.start_point.row + 1}_{node.start_point.column}"


def _construction_edge(edge: CodeEdge, occurrence: Node, graph: CodeGraph) -> CodeEdge:
    """Keep unknown construction sites while binding explicit local constructors."""
    if occurrence.type != "new_expression":
        return edge
    attributes = dict(edge.attributes)
    attributes["constructor_call"] = True
    target = graph.nodes.get(edge.target_id)
    if target is not None and target.kind == NodeKind.CLASS:
        constructor = _class_constructor(target, graph)
        return replace(
            edge,
            target_id=constructor.id if constructor is not None else target.id,
            confidence=EdgeConfidence.INFERRED,
            attributes=tuple(attributes.items()),
        )
    if target is None and not attributes.get("import_binding"):
        attributes["binding_blocked"] = True
        return replace(
            edge, confidence=EdgeConfidence.UNCERTAIN, attributes=tuple(attributes.items())
        )
    return replace(edge, attributes=tuple(attributes.items()))


def _class_initializer(occurrence: Node) -> bool:
    """Class field values use the class receiver; computed keys use the outer one."""
    parent = occurrence.parent
    while parent is not None:
        if parent.type in CALLABLES | {"computed_property_name"}:
            return False
        if parent.type in {"field_definition", "public_field_definition", "class_static_block"}:
            return True
        parent = parent.parent
    return False


def _discovered_name(node: Node) -> str:
    name = _callable_name(node)
    parent = callable_parent(node)
    if parent is not None and parent.type == "pair" and parent.parent is not None:
        name = f"{_object_binding(parent.parent)}.{name}"
    if (
        node.type == "method_definition"
        and node.parent is not None
        and node.parent.type == "object"
    ):
        name = f"{_object_binding(node.parent)}.{name}"
    return name


def _callable_name(node: Node) -> str:
    name = node.child_by_field_name("name")
    if name is not None and node.type not in {
        "function_expression",
        "function",
        "generator_function",
    }:
        return node_text(name)
    parent = callable_parent(node)
    if parent is not None:
        field = {
            "variable_declarator": "name",
            "assignment_expression": "left",
            "field_definition": "property",
            "public_field_definition": "name",
            "pair": "key",
        }.get(parent.type)
        if field is not None:
            binding = parent.child_by_field_name(field)
            if binding is not None:
                return node_text(binding).removeprefix("module.exports.").removeprefix("exports.")
        if parent.type == "export_statement":
            return "default"
    if name is not None:
        return node_text(name)
    return f"$lambda@{node.start_point.row + 1}_{node.start_point.column}"


def _object_binding(node: Node) -> str:
    parts = []
    current: Node | None = node
    while current is not None:
        parent = current.parent
        if parent is None:
            break
        field = {"variable_declarator": "name", "assignment_expression": "left", "pair": "key"}.get(
            parent.type
        )
        binding = parent.child_by_field_name(field) if field else None
        if binding is not None:
            parts.append(node_text(binding))
        if parent.type != "pair":
            break
        current = parent.parent
    return (
        ".".join(reversed(parts)) or f"$object@{node.start_point.row + 1}_{node.start_point.column}"
    )


def class_member_callable(node: Node, class_owner: str | None) -> bool:
    """Class methods and arrow-valued fields retain method metadata."""
    if class_owner is None:
        return False
    if node.type == "method_definition":
        return True
    parent = callable_parent(node)
    return (
        node.type == "arrow_function"
        and parent is not None
        and parent.type in {"field_definition", "public_field_definition"}
    )
