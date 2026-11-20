"""Bind qualified paths to visible declarations without guessing imported bodies."""

from dataclasses import replace

from tree_sitter import Node

from trailmark.models.edges import EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind
from trailmark.parsers._common import node_text


def _generic_scopes(root: Node) -> list[tuple[Node, set[str]]]:
    result = []
    pending = [root]
    while pending:
        node = pending.pop()
        if node.type == "type_parameters" and node.parent is not None:
            names = set()
            for parameter in node.named_children:
                name = parameter.child_by_field_name("trait")
                if name is not None:
                    names.add(node_text(name))
                elif parameter.type == "type_identifier":
                    names.add(node_text(parameter))
            result.append((node.parent, names))
        pending.extend(node.named_children)
    return result


def _path(node: Node | None) -> tuple[str, ...]:
    if node is None:
        return ()
    return tuple(node_text(node).split("::"))


def _uses(node: Node, prefix: tuple[str, ...] = ()) -> list[tuple[str, tuple[str, ...]]]:
    if node.type == "use_as_clause":
        alias = node.child_by_field_name("alias")
        return (
            [(node_text(alias), prefix + _path(node.child_by_field_name("path")))] if alias else []
        )
    if node.type == "scoped_use_list":
        prefix += _path(node.child_by_field_name("path"))
        children = node.child_by_field_name("list")
        return _uses(children, prefix) if children else []
    if node.type in {"use_list", "use_declaration"}:
        return [binding for child in node.named_children for binding in _uses(child, prefix)]
    if node.type in {"identifier", "scoped_identifier"}:
        path = prefix + _path(node)
        return [(path[-1], path)]
    # Unknown import forms form a barrier to outward lexical lookup.
    return [("*", ())]


type _ImportScope = tuple[Node, tuple[str, ...], dict[str, set[tuple[str, ...]]]]


def _imports(root: Node) -> list[_ImportScope]:
    result: dict[int, _ImportScope] = {}
    pending: list[tuple[Node, tuple[str, ...]]] = [(root, ())]
    while pending:
        node, scope = pending.pop()
        if node.type == "mod_item":
            name = node.child_by_field_name("name")
            if name is not None:
                scope += (node_text(name),)
        if node.type == "use_declaration":
            owner = node.parent
            while owner is not None and owner.type not in {
                "block",
                "declaration_list",
                "cairo_1_file",
            }:
                owner = owner.parent
            if owner is None:
                continue
            _, _, bindings = result.setdefault(owner.id, (owner, scope, {}))
            for alias, path in _uses(node):
                bindings.setdefault(alias, set()).add(path)
        else:
            pending.extend((child, scope) for child in node.named_children)
    return sorted(result.values(), key=lambda entry: entry[0].end_byte - entry[0].start_byte)


def _contains(node: Node, line: int, column: int) -> bool:
    return (
        (node.start_point.row + 1, node.start_point.column)
        <= (line, column)
        < (node.end_point.row + 1, node.end_point.column)
    )


def _relative(path: tuple[str, ...], scope: tuple[str, ...]) -> tuple[str, ...] | None:
    if not path:
        return None
    if path[0] == "self":
        return scope + path[1:]
    if path[0] == "super":
        while path and path[0] == "super":
            if not scope:
                return None
            scope, path = scope[:-1], path[1:]
        return scope + path
    if path[0] == "crate":
        return None  # A crate can span files outside this lexical graph.
    return scope + path


def resolve_qualified_calls(root: Node, graph: CodeGraph, module: str) -> None:
    imports = _imports(root)
    generic_scopes = _generic_scopes(root)
    owners = {
        edge.target_id: edge.source_id for edge in graph.edges if edge.kind == EdgeKind.CONTAINS
    }
    edges = []
    for edge in graph.edges:
        if edge.kind != EdgeKind.CALLS or "::" not in edge.target_id:
            edges.append(edge)
            continue
        parts = tuple(edge.target_id.split("::"))
        if not all(part.isidentifier() for part in parts):
            edges.append(edge)
            continue
        location = edge.location
        if location is None or location.start_col is None:
            edges.append(edge)
            continue
        if any(
            parts[0] in names and _contains(region, location.start_line, location.start_col)
            for region, names in generic_scopes
        ):
            edges.append(edge)
            continue
        owner = owners.get(edge.source_id, module)
        while owner in graph.nodes and graph.nodes[owner].kind not in {
            NodeKind.MODULE,
            NodeKind.CONTRACT,
        }:
            owner = owners.get(owner, module)
        scope = tuple(owner.split(":", 1)[1].split(".")) if ":" in owner else ()
        imported = False
        paths: set[tuple[str, ...] | None] = set()
        for region, import_module, bindings in imports:
            if import_module != scope or not _contains(
                region, location.start_line, location.start_col
            ):
                continue
            if parts[0] in bindings:
                paths = {_relative(path, import_module) for path in bindings[parts[0]]}
                imported = True
                break
            if "*" in bindings:
                imported = True
                break
        if imported:
            path = next(iter(paths)) if len(paths) == 1 else None
            candidate = path + parts[1:] if path else None
        else:
            candidate = _relative(parts, scope)
        target = f"{module}:{'.'.join(candidate)}" if candidate else ""
        unit = graph.nodes.get(target)
        if unit is not None and unit.kind in {NodeKind.FUNCTION, NodeKind.METHOD}:
            edge = replace(edge, target_id=target)
        edges.append(edge)
    graph.edges[:] = edges
