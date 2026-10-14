"""Resolve calls to declarations and relative imports present in a Rust file."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace

from tree_sitter import Node

from trailmark.models.edges import CodeEdge, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind, SourceLocation
from trailmark.parsers._common import node_text


def _path_parts(node: Node | None) -> tuple[str, ...]:
    if node is None:
        return ()
    parts = []
    stack = [node]
    while stack:
        child = stack.pop()
        if child.named_children:
            stack.extend(reversed(child.named_children))
        else:
            parts.append(node_text(child))
    return tuple(parts)


def _use_paths(node: Node, prefix: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], str]]:
    stack = [(node, prefix)]
    while stack:
        node, prefix = stack.pop()
        if node.type == "scoped_use_list":
            prefix += _path_parts(node.child_by_field_name("path"))
            children = node.child_by_field_name("list")
            if children is not None:
                stack.append((children, prefix))
        elif node.type == "use_list":
            stack.extend((child, prefix) for child in node.named_children)
        elif node.type == "use_as_clause":
            path = prefix + _path_parts(node.child_by_field_name("path"))
            alias = node.child_by_field_name("alias")
            if alias is not None:
                yield path, node_text(alias)
        else:
            path = prefix + _path_parts(node)
            alias = "*" if node.type == "use_wildcard" else path[-1]
            yield path, alias


def _relative_target(path: tuple[str, ...], module: str, root: str, wildcard: bool = False) -> str:
    parts = module.split(".")
    remaining = list(path)
    if remaining and remaining[0] == "crate":
        parts = root.split(".")
        remaining.pop(0)
    elif remaining and remaining[0] == "self":
        remaining.pop(0)
    while remaining and remaining[0] == "super":
        if len(parts) <= 1:
            return ""
        parts.pop()
        remaining.pop(0)
    if wildcard:
        return ".".join([*parts, *remaining])
    return f"{'.'.join([*parts, *remaining[:-1]])}:{remaining[-1]}" if remaining else ""


def _lexical_scopes(source: str) -> Iterator[str]:
    module, _, function = source.partition(":")
    while function:
        yield f"{module}:{function}"
        function = function.rpartition(".")[0]
    yield module


@dataclass(frozen=True)
class _Resolution:
    graph: CodeGraph
    imports: dict[str, list[tuple[str, str, Node]]]
    locals: dict[str, list[str]]
    functions: dict[str, Node]
    root: str


def _enclosing_block(node: Node) -> Node | None:
    parent = node.parent
    while parent is not None and parent.type != "block":
        parent = parent.parent
    return parent


def _visible(node: Node, location: SourceLocation | None) -> bool:
    parent = _enclosing_block(node)
    if parent is None or location is None:
        return True
    position = (location.start_line - 1, location.start_col or 0)
    return parent.start_point <= position < parent.end_point


def _block_start(node: Node) -> int:
    block = _enclosing_block(node)
    return block.start_byte if block is not None else 0


def _resolve_edge(edge: CodeEdge, context: _Resolution) -> CodeEdge:
    graph = context.graph
    if edge.kind != EdgeKind.CALLS:
        return edge
    module = edge.source_id.partition(":")[0]
    if "::" in edge.target_id:
        target = _relative_target(tuple(edge.target_id.split("::")), module, context.root)
        return replace(edge, target_id=target) if target in graph.nodes else edge
    prefix = f"{module}:"
    if not edge.target_id.startswith(prefix):
        return edge
    name = edge.target_id.removeprefix(prefix)
    target = _bare_target(edge, name, context)
    return replace(edge, target_id=target) if target else edge


def _local_target(edge: CodeEdge, scope: str, name: str, context: _Resolution) -> str | None:
    candidates = [
        node_id
        for node_id in context.locals.get(scope, [])
        if context.graph.nodes[node_id].name == name
        and _visible(context.functions[node_id], edge.location)
    ]
    # An inner block shadows a declaration with the same name in an outer block.
    return (
        max(candidates, key=lambda node_id: _block_start(context.functions[node_id]))
        if candidates
        else None
    )


def _bare_target(edge: CodeEdge, name: str, context: _Resolution) -> str | None:
    for scope in _lexical_scopes(edge.source_id):
        candidate = _local_target(edge, scope, name, context)
        if candidate:
            return candidate
        if ":" not in scope and f"{scope}:{name}" in context.graph.nodes:
            return f"{scope}:{name}"
        candidate = _import_target(edge, scope, name, context)
        if candidate:
            return candidate
    return None


def _import_target(edge: CodeEdge, scope: str, name: str, context: _Resolution) -> str | None:
    visible = [
        (alias, target, _block_start(node))
        for alias, target, node in context.imports.get(scope, [])
        if alias in {name, "*"} and _visible(node, edge.location)
    ]
    if not visible:
        return None
    nearest = max(start for _, _, start in visible)
    candidates = {
        f"{target}:{name}" if alias == "*" else target
        for alias, target, start in visible
        if start == nearest
    } & context.graph.nodes.keys()
    return candidates.pop() if len(candidates) == 1 else None


def _local_owners(graph: CodeGraph) -> dict[str, list[str]]:
    owners: dict[str, list[str]] = {}
    for edge in graph.edges:
        owner = graph.nodes.get(edge.source_id)
        if (
            edge.kind == EdgeKind.CONTAINS
            and owner is not None
            and owner.kind in {NodeKind.FUNCTION, NodeKind.METHOD}
        ):
            owners.setdefault(edge.source_id, []).append(edge.target_id)
    return owners


def resolve_calls(
    graph: CodeGraph, uses: list[tuple[str, Node]], root: str, functions: dict[str, Node]
) -> None:
    """Link lexical helpers and imports only when a declaration exists in this file."""
    imports: dict[str, list[tuple[str, str, Node]]] = {}
    for scope, node in uses:
        argument = node.child_by_field_name("argument")
        if argument is None:
            continue
        module = scope.partition(":")[0]
        for path, alias in _use_paths(argument):
            target = _relative_target(path, module, root, wildcard=alias == "*")
            imports.setdefault(scope, []).append((alias, target, node))
    context = _Resolution(graph, imports, _local_owners(graph), functions, root)
    graph.edges = [_resolve_edge(edge, context) for edge in graph.edges]
