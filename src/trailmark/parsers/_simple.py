"""Small helpers for declarative tree-sitter parsers."""

from __future__ import annotations

import re
from collections.abc import Iterable

from tree_sitter import Node

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import BranchInfo, Parameter, TypeRef
from trailmark.parsers._common import make_location, node_text


def named_children(node: Node) -> list[Node]:
    """Return named children only."""
    return [child for child in node.children if child.is_named]


def descendants(node: Node) -> Iterable[Node]:
    """Yield named descendants depth-first, excluding ``node`` itself."""
    stack = list(reversed(named_children(node)))
    while stack:
        child = stack.pop()
        yield child
        stack.extend(reversed(named_children(child)))


def first_child_type(node: Node, types: set[str] | frozenset[str]) -> Node | None:
    """Return the first direct named child whose type is in ``types``."""
    for child in named_children(node):
        if child.type in types:
            return child
    return None


def first_descendant_type(node: Node, types: set[str] | frozenset[str]) -> Node | None:
    """Return the first named descendant whose type is in ``types``."""
    for child in descendants(node):
        if child.type in types:
            return child
    return None


def child_text(node: Node, types: set[str] | frozenset[str]) -> str:
    """Return text for the first matching direct child."""
    child = first_child_type(node, types)
    return node_text(child) if child is not None else ""


def descendant_text(node: Node, types: set[str] | frozenset[str]) -> str:
    """Return text for the first matching descendant."""
    child = first_descendant_type(node, types)
    return node_text(child) if child is not None else ""


def extract_parameters(
    node: Node,
    *,
    list_types: set[str] | frozenset[str] = frozenset({"parameters", "parameter_list"}),
    item_types: set[str] | frozenset[str] = frozenset(
        {"parameter", "parameter_declaration", "input_value_definition", "field"}
    ),
    name_types: set[str] | frozenset[str] = frozenset(
        {"identifier", "parameter", "name", "var", "field_name"}
    ),
    type_types: set[str] | frozenset[str] = frozenset(
        {
            "primitive_type",
            "type",
            "type_identifier",
            "qualified_type",
            "message_or_enum_type",
            "named_type",
            "non_null_type",
            "list_type",
            "type_expression",
        }
    ),
) -> tuple[Parameter, ...]:
    """Best-effort parameter extraction for simple grammars."""
    params_node = first_child_type(node, list_types)
    if params_node is None:
        return ()
    params: list[Parameter] = []
    for item in named_children(params_node):
        if item.type not in item_types:
            continue
        name = _parameter_name(item, name_types)
        if not name:
            continue
        type_ref = _parameter_type(item, type_types, name)
        params.append(Parameter(name=name, type_ref=type_ref))
    return tuple(params)


def _parameter_name(node: Node, name_types: set[str] | frozenset[str]) -> str:
    matches = [child for child in named_children(node) if child.type in name_types]
    if not matches:
        return ""
    # Func parameters use a node named "parameter" for the identifier.
    return node_text(matches[-1])


def _parameter_type(
    node: Node,
    type_types: set[str] | frozenset[str],
    name: str,
) -> TypeRef | None:
    for child in named_children(node):
        if child.type in type_types:
            text = node_text(child)
            if text and text != name:
                return TypeRef(name=clean_type_name(text))
    return None


def return_type_after_params(
    node: Node,
    type_types: set[str] | frozenset[str] = frozenset(
        {
            "primitive_type",
            "type",
            "type_identifier",
            "qualified_type",
            "message_or_enum_type",
            "named_type",
            "non_null_type",
            "list_type",
        }
    ),
) -> TypeRef | None:
    """Return the first type-looking child after a parameter list."""
    seen_params = False
    for child in named_children(node):
        if child.type in {"parameters", "parameter_list"}:
            seen_params = True
            continue
        if seen_params and child.type in type_types:
            return TypeRef(name=clean_type_name(node_text(child)))
    return None


def clean_type_name(text: str) -> str:
    """Normalize compact type syntax into a stable TypeRef name."""
    stripped = text.strip()
    stripped = stripped.removeprefix("bounced<").removesuffix(">")
    stripped = stripped.replace("!", "")
    return re.sub(r"\s+", " ", stripped)


def collect_branches_and_calls(
    body: Node | None,
    file_path: str,
    *,
    branch_types: set[str] | frozenset[str],
    call_types: set[str] | frozenset[str],
) -> tuple[list[BranchInfo], list[tuple[str, Node]]]:
    """Collect branch metadata and direct call expressions under ``body``."""
    branches: list[BranchInfo] = []
    calls: list[tuple[str, Node]] = []
    if body is None:
        return branches, calls
    for node in descendants(body):
        if node.type in branch_types:
            branches.append(
                BranchInfo(
                    location=make_location(node, file_path),
                    condition=_condition_text(node),
                )
            )
        if node.type in call_types:
            call_name = call_expression_name(node)
            if call_name:
                calls.append((call_name, node))
    return branches, calls


def _condition_text(node: Node) -> str:
    condition = node.child_by_field_name("condition")
    if condition is not None:
        return node_text(condition)
    return node.type


def call_expression_name(node: Node) -> str:
    """Extract a callable symbol from common call-expression shapes."""
    function = node.child_by_field_name("function")
    if function is not None:
        return _callable_text(function)

    for child in named_children(node):
        if child.type in {
            "identifier",
            "function_name",
            "func_identifier",
            "field_identifier",
            "scoped_identifier",
            "method_call",
            "field_access_expression",
            "var",
            "ref",
        }:
            return _callable_text(child)
    text = node_text(node).strip()
    return text.split("(", 1)[0].strip()


def _callable_text(node: Node) -> str:
    text = node_text(node).strip()
    text = text.split("(", 1)[0].strip()
    text = text.removeprefix("self.").removeprefix("this.")
    text = text.removeprefix("~")
    if "::" in text:
        return text.rsplit("::", 1)[-1]
    if "." in text:
        return text.rsplit(".", 1)[-1]
    return text


def add_call_edges(
    graph: CodeGraph,
    calls: list[tuple[str, Node]],
    source_id: str,
    module_id: str,
    file_path: str,
    container_id: str | None = None,
) -> None:
    """Add direct call edges with local best-effort target resolution."""
    for call_name, call_node in calls:
        target_id = resolve_call_target(call_name, module_id, container_id)
        graph.edges.append(
            CodeEdge(
                source_id=source_id,
                target_id=target_id,
                kind=EdgeKind.CALLS,
                confidence=EdgeConfidence.CERTAIN,
                location=make_location(call_node, file_path),
            )
        )


def resolve_call_target(call_name: str, module_id: str, container_id: str | None = None) -> str:
    """Resolve a bare call into the current container/module namespace."""
    name = call_name.strip()
    if not name:
        return f"{module_id}:<unknown>"
    if ":" in name:
        return name
    if "." in name:
        name = name.rsplit(".", 1)[-1]
    if container_id is not None:
        return f"{container_id}.{name}"
    return f"{module_id}:{name}"
