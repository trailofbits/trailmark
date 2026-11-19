"""Share symbol identity within an unambiguous inspected include theory."""

import json
from dataclasses import replace
from pathlib import Path

from trailmark.models.edges import CodeEdge, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind, NodeOrigin
from trailmark.parsers._formal_linking import link_symbols


def link_tamarin(graph: CodeGraph) -> None:
    adjacency, included = _includes(graph)
    seen = set()
    for root in adjacency:
        if root in seen:
            continue
        component, pending = set(), [root]
        while pending:
            node = pending.pop()
            if node not in component:
                component.add(node)
                pending.extend(adjacency[node])
        seen.update(component)
        roots = component - included
        if len(component) > 1 and len(roots) == 1:
            _theory(graph, component, next(iter(roots)))
    link_symbols(graph)
    graph.edges = [e for e in graph.edges if not dict(e.attributes).get("_formal_include_context")]


def _includes(graph: CodeGraph) -> tuple[dict[str, set[str]], set[str]]:
    files = {
        str(Path(n.location.file_path).resolve()): n.id
        for n in graph.nodes.values()
        if n.kind == NodeKind.MODULE and n.origin == NodeOrigin.SOURCE
    }
    adjacency: dict[str, set[str]] = {ident: set() for ident in files.values()}
    included = set()
    for edge in graph.edges:
        path = dict(edge.attributes).get("import_path")
        if edge.kind != EdgeKind.IMPORTS or not isinstance(path, str):
            continue
        source = graph.nodes[edge.source_id]
        target = files.get(str((Path(source.location.file_path).parent / path).resolve()))
        if target:
            adjacency[edge.source_id].add(target)
            adjacency[target].add(edge.source_id)
            included.add(target)
    return adjacency, included


def _theory(graph: CodeGraph, component: set[str], root: str) -> None:
    paths = {graph.nodes[ident].location.file_path for ident in component}
    namespaces = {}
    for unit in graph.nodes.values():
        qualified = dict(unit.attributes).get("qualified_name")
        if unit.location.file_path in paths and isinstance(qualified, str):
            namespaces[unit.location.file_path] = qualified.split(".")[0]
    namespace = namespaces.get(graph.nodes[root].location.file_path)
    if namespace is None:
        return
    replacements = dict.fromkeys(namespaces.values(), namespace)
    redirects = _members(graph, paths, namespace)
    for index, edge in enumerate(graph.edges):
        attrs = dict(edge.attributes)
        if graph.nodes[edge.source_id].location.file_path in paths:
            tiers = attrs.get("_formal_candidates")
            if isinstance(tiers, str):
                names = json.loads(tiers)
                attrs["_formal_candidates"] = json.dumps(
                    [
                        [
                            replacements.get(name.split(".")[0], name.split(".")[0])
                            + "."
                            + name.split(".", 1)[1]
                            if "." in name
                            else name
                            for name in tier
                        ]
                        for tier in names
                    ]
                )
        graph.edges[index] = replace(
            edge,
            source_id=redirects.get(edge.source_id, edge.source_id),
            target_id=redirects.get(edge.target_id, edge.target_id),
            attributes=tuple(attrs.items()),
        )
    for ident in redirects:
        del graph.nodes[ident]
    graph.edges.extend(
        CodeEdge(ident, root, EdgeKind.IMPORTS, attributes=(("_formal_include_context", True),))
        for ident in component
        if ident != root
    )


def _members(graph: CodeGraph, paths: set[str], namespace: str) -> dict[str, str]:
    canonical, redirects = {}, {}
    for ident, unit in tuple(graph.nodes.items()):
        if unit.location.file_path not in paths:
            continue
        attrs = dict(unit.attributes)
        qualified = attrs.get("qualified_name")
        if isinstance(qualified, str):
            attrs["qualified_name"] = namespace + "." + qualified.split(".", 1)[-1]
            unit = replace(unit, attributes=tuple(attrs.items()))
            graph.nodes[ident] = unit
            if attrs.get("declaration_role") == "fact_symbol" or attrs.get("builtin"):
                key = str(attrs["qualified_name"])
                if key in canonical:
                    redirects[ident] = canonical[key]
                else:
                    canonical[key] = ident
    return redirects
