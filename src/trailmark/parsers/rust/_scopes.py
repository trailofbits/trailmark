"""Resolve calls to declarations and relative imports present in a Rust file."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace

from tree_sitter import Node

from trailmark.models.edges import CodeEdge, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind, SourceLocation
from trailmark.parsers._common import make_location, node_text


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
    locals: dict[tuple[str, str], list[str]]
    declarations: dict[str, Node]
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
    if edge.kind not in {EdgeKind.CALLS, EdgeKind.IMPLEMENTS} or dict(edge.attributes).get(
        "binding_blocked"
    ):
        return edge
    prefix = f"{edge.source_id.partition(':')[0]}:"
    if edge.target_id.startswith(prefix):
        name = edge.target_id.removeprefix(prefix)
        target = _bare_target(edge.source_id, name, context, edge.location)
    elif "." in edge.target_id:
        target = _receiver_target(edge, context)
    else:
        path = tuple(edge.target_id.split("::"))
        target = _path_target(path, edge.source_id, context, edge.location)
    if target is None:
        return edge
    if edge.kind == EdgeKind.CALLS and context.graph.nodes[target].kind == NodeKind.MODULE:
        return edge
    return replace(edge, target_id=target)


def _member_target(
    scope: str, name: str, context: _Resolution, location: SourceLocation | None
) -> str | None:
    candidates = [
        node_id
        for node_id in context.locals.get((scope, name), [])
        if _visible(context.declarations[node_id], location)
    ]
    # An inner block shadows a declaration with the same name in an outer block.
    if not candidates:
        return None
    nearest = max(_block_start(context.declarations[node_id]) for node_id in candidates)
    candidates = [
        node_id for node_id in candidates if _block_start(context.declarations[node_id]) == nearest
    ]
    return candidates[0] if len(candidates) == 1 else None


def _binding_name(node_id: str, graph: CodeGraph, declarations: dict[str, Node]) -> str:
    name = declarations[node_id].child_by_field_name("name")
    return node_text(name) if name is not None else graph.nodes[node_id].name


def _receiver_target(edge: CodeEdge, context: _Resolution) -> str | None:
    # A unit struct can be a value receiver. Other receivers require type inference.
    receiver, _, name = edge.target_id.partition(".")
    owner = _bare_target(edge.source_id, receiver, context, edge.location)
    if owner is None:
        return None
    if context.graph.nodes[owner].kind not in {NodeKind.STRUCT, NodeKind.ENUM}:
        return None
    return _member_target(owner, name, context, edge.location)


def _bare_target(
    source: str,
    name: str,
    context: _Resolution,
    location: SourceLocation | None,
) -> str | None:
    for scope in _lexical_scopes(source):
        owner = context.graph.nodes.get(scope)
        if owner is None or owner.kind not in {
            NodeKind.FUNCTION,
            NodeKind.METHOD,
            NodeKind.MODULE,
        }:
            continue
        candidate = _member_target(scope, name, context, location)
        if candidate:
            return candidate
        candidate = _import_target(scope, name, context, location)
        if candidate:
            return candidate
        if owner.kind == NodeKind.MODULE:
            break
    return None


def _import_target(
    scope: str, name: str, context: _Resolution, location: SourceLocation | None
) -> str | None:
    visible = [
        (alias, target, _block_start(node))
        for alias, target, node in context.imports.get(scope, [])
        if alias in {name, "*"} and _visible(node, location)
    ]
    if not visible:
        return None
    nearest = max(start for _, _, start in visible)
    candidates = {
        _member_target(target, name, context, location) if alias == "*" else target
        for alias, target, start in visible
        if start == nearest
    } & context.graph.nodes.keys()
    return candidates.pop() if len(candidates) == 1 else None


def _module_scope(scope: str, context: _Resolution) -> str:
    for candidate in _lexical_scopes(scope):
        owner = context.graph.nodes.get(candidate)
        if owner is not None and owner.kind == NodeKind.MODULE:
            return candidate
    raise ValueError(f"Rust declaration has no enclosing module: {scope}")


def _path_start(
    path: list[str],
    source: str,
    context: _Resolution,
    location: SourceLocation | None,
) -> str | None:
    first = path.pop(0)
    if first == "crate":
        return context.root
    module = _module_scope(source, context)
    if first == "self":
        return module
    if first != "super":
        return _bare_target(source, first, context, location)
    while True:
        if module == context.root:
            return None
        module = _module_scope(module.rpartition(".")[0], context)
        if not path or path[0] != "super":
            return module
        path.pop(0)


def _path_target(
    path: tuple[str, ...],
    source: str,
    context: _Resolution,
    location: SourceLocation | None,
) -> str | None:
    if not path:
        return None
    remaining = list(path)
    target = _path_start(remaining, source, context, location)
    for name in remaining:
        if target is None:
            return None
        target = _member_target(target, name, context, location)
    return target


def _scope_members(
    graph: CodeGraph, declarations: dict[str, Node]
) -> dict[tuple[str, str], list[str]]:
    members: dict[tuple[str, str], list[str]] = {}
    for edge in graph.edges:
        if edge.kind == EdgeKind.CONTAINS and edge.source_id in graph.nodes:
            name = _binding_name(edge.target_id, graph, declarations)
            members.setdefault((edge.source_id, name), []).append(edge.target_id)
    return members


def _local_impl_owners(context: _Resolution) -> dict[str, str]:
    owners: dict[str, str] = {}
    for edge in context.graph.edges:
        if edge.kind != EdgeKind.CONTAINS or edge.source_id in context.graph.nodes:
            continue
        scope, _, name = edge.source_id.rpartition(".")
        if scope not in context.graph.nodes:
            continue
        location = context.graph.nodes[edge.target_id].location
        target = _path_target(tuple(name.split("::")), scope, context, location)
        if target is not None and context.graph.nodes[target].kind in {
            NodeKind.STRUCT,
            NodeKind.ENUM,
            NodeKind.TRAIT,
        }:
            owners[edge.source_id] = target
    return owners


def _resolve_impl_owners(context: _Resolution) -> _Resolution:
    owners = _local_impl_owners(context)
    if not owners:
        return context
    context.graph.edges = [
        replace(_resolve_edge(edge, context), source_id=owners[edge.source_id])
        if edge.source_id in owners
        else edge
        for edge in context.graph.edges
    ]
    return replace(context, locals=_scope_members(context.graph, context.declarations))


def resolve_calls(
    graph: CodeGraph, uses: list[tuple[str, Node]], root: str, functions: dict[str, Node]
) -> None:
    """Link lexical helpers and imports only when a declaration exists in this file."""
    imports: dict[str, list[tuple[str, str, Node]]] = {}
    context = _Resolution(graph, {}, _scope_members(graph, functions), functions, root)
    for scope, node in uses:
        argument = node.child_by_field_name("argument")
        if argument is None:
            continue
        for path, alias in _use_paths(argument):
            target = _path_target(
                path,
                scope,
                context,
                make_location(node, graph.root_path),
            )
            if target is not None:
                imports.setdefault(scope, []).append((alias, target, node))
    context = replace(context, imports=imports)
    context = _resolve_impl_owners(context)
    graph.edges = [_resolve_edge(edge, context) for edge in graph.edges]
