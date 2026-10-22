"""Move declarations and calls using the bundled Move project grammar."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from tree_sitter import Language, Node, Parser

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import BranchInfo, CodeUnit, NodeKind, Parameter, TypeRef
from trailmark.parsers._common import (
    add_contains_edge,
    add_module_node,
    compute_complexity,
    make_location,
    module_id_from_path,
    node_text,
    parse_directory,
)
from trailmark.parsers._walk import walk_owned
from trailmark.tree_sitter_custom.move import language

_EXTENSIONS = (".move",)
_FUNCTIONS = {"function_definition", "native_function_definition", "macro_function_definition"}


class MoveParser:
    """Parse supported Aptos/Sui syntax without runtime address or value inference."""

    @property
    def language(self) -> str:
        return "move"

    def __init__(self) -> None:
        self._parser = Parser(Language(language()))

    def parse_file(self, file_path: str) -> CodeGraph:
        root = self._parser.parse(Path(file_path).read_bytes()).root_node
        graph = CodeGraph(language="move", root_path=file_path)
        module = module_id_from_path(file_path)
        add_module_node(root, file_path, module, graph)
        for declaration in root.named_children:
            if declaration.type != "module_definition":
                continue
            identity = declaration.child_by_field_name("module_identity")
            body = declaration.child_by_field_name("module_body")
            if identity is None or body is None:
                continue
            logical = node_text(identity)
            owner = f"{module}:{logical}"
            graph.nodes[owner] = CodeUnit(
                id=owner,
                name=logical,
                kind=NodeKind.MODULE,
                location=make_location(declaration, file_path),
            )
            add_contains_edge(graph, module, owner)
            imports: dict[str, str] = {}
            for item in body.named_children:
                if item.type == "use_declaration":
                    graph.dependencies.append(
                        node_text(item).removeprefix("use").strip().rstrip(";")
                    )
                    for imported in item.named_children:
                        target = next(
                            (c for c in imported.named_children if c.type == "module_identity"),
                            None,
                        )
                        if target is not None and imported.type == "use_module":
                            alias = imported.child_by_field_name("alias")
                            imports[
                                node_text(alias) if alias else node_text(target).rsplit("::", 1)[-1]
                            ] = node_text(target)
                        elif target is not None:
                            prefix = node_text(target)
                            for member in imported.children_by_field_name("use_member"):
                                symbol = member.child_by_field_name("member")
                                alias = member.child_by_field_name("alias")
                                if symbol is None:
                                    continue
                                name = node_text(symbol)
                                if name == "Self":
                                    imports[
                                        node_text(alias) if alias else prefix.rsplit("::", 1)[-1]
                                    ] = prefix
                                else:
                                    imports[node_text(alias) if alias else name] = (
                                        f"{prefix}::{name}"
                                    )
            for item in body.named_children:
                if item.type in _FUNCTIONS:
                    _function(item, owner, logical, imports, file_path, graph)
                elif item.type in {
                    "struct_definition",
                    "native_struct_definition",
                    "enum_definition",
                }:
                    name = item.child_by_field_name("name")
                    if name is not None:
                        ident = f"{owner}:{node_text(name)}"
                        graph.nodes[ident] = CodeUnit(
                            id=ident,
                            name=node_text(name),
                            kind=NodeKind.ENUM
                            if item.type == "enum_definition"
                            else NodeKind.STRUCT,
                            location=make_location(item, file_path),
                        )
                        add_contains_edge(graph, owner, ident)
        _link_calls(graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        graph = parse_directory(self.parse_file, "move", dir_path, _EXTENSIONS)
        _link_calls(graph)
        return graph


def _function(
    node: Node, owner: str, logical: str, imports: dict[str, str], path: str, graph: CodeGraph
) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    name = node_text(name_node)
    ident = f"{owner}:{name}"
    if ident in graph.nodes:
        ident += f"@{node.start_point.row + 1}_{node.start_point.column}"
    params = []
    parameters = node.child_by_field_name("parameters")
    if parameters is not None:
        for parameter in walk_owned(parameters):
            if parameter.type != "function_parameter":
                continue
            local = parameter.child_by_field_name("name")
            annotation = parameter.child_by_field_name("type")
            if local is not None:
                params.append(
                    Parameter(
                        node_text(local), TypeRef(node_text(annotation)) if annotation else None
                    )
                )
    body = node.child_by_field_name("body")
    branches = []
    calls = []
    bound = {p.name for p in params}
    if body is not None:
        nodes = list(walk_owned(body, boundaries=frozenset({"lambda_expression"})))
        for site in nodes:
            if site.type == "let_statement":
                binding = site.child_by_field_name("bind")
                if binding is not None:
                    bound.update(
                        node_text(c)
                        for c in walk_owned(binding)
                        if c.type in {"variable_identifier", "identifier"}
                    )
            if site.type in {"if_expression", "while_expression", "loop_expression", "match_arm"}:
                branches.append(BranchInfo(make_location(site, path), site.type))
            if site.type in {"call_expression", "macro_call_expression"}:
                expression = next(
                    (c for c in site.named_children if c.type == "name_expression"), None
                )
                access = (
                    expression.child_by_field_name("access")
                    if expression is not None
                    else site.child_by_field_name("access")
                )
                if access is not None:
                    calls.append((site, access))
    result = node.child_by_field_name("return_type")
    graph.nodes[ident] = CodeUnit(
        id=ident,
        name=name,
        kind=NodeKind.FUNCTION,
        location=make_location(node, path),
        parameters=tuple(params),
        return_type=TypeRef(node_text(result).removeprefix(":").strip()) if result else None,
        branches=tuple(branches),
        cyclomatic_complexity=compute_complexity(branches),
        attributes=(
            ("move_symbol", f"{logical}::{name}"),
            (
                "move_entry",
                any(node_text(c) == "entry" for c in node.named_children if c.type == "modifier"),
            ),
            (
                "move_public",
                any(
                    node_text(c).startswith("public")
                    for c in node.named_children
                    if c.type == "modifier"
                ),
            ),
        ),
    )
    add_contains_edge(graph, owner, ident)
    for site, access in calls:
        member = access.child_by_field_name("member")
        raw = node_text(access)
        if member is not None:
            # Type application is metadata on access, not a comparison expression.
            prefix = raw[: member.start_byte - access.start_byte]
            raw = prefix + node_text(member)
        if "::" in raw:
            first, tail = raw.split("::", 1)
            raw = imports.get(first, first) + "::" + tail
            target = raw
        else:
            raw = imports.get(raw, raw)
            target = raw if "::" in raw else f"{owner}:{raw}"
        blocked = raw in bound or root_error(site)
        if blocked:
            target = f"{ident}.$binding.{raw}"
        graph.edges.append(
            CodeEdge(
                ident,
                target,
                EdgeKind.CALLS,
                confidence=EdgeConfidence.UNCERTAIN
                if blocked or "::" in raw
                else EdgeConfidence.CERTAIN,
                location=make_location(site, path),
                attributes=(("binding_blocked", True), ("move_target", raw if not blocked else "")),
            )
        )


def root_error(node: Node) -> bool:
    """Recovery cannot establish a concrete local callable binding."""
    while node.parent is not None:
        node = node.parent
    return node.has_error


def _link_calls(graph: CodeGraph) -> None:
    symbols: dict[str, list[str]] = {}
    for unit in graph.nodes.values():
        symbol = dict(unit.attributes).get("move_symbol")
        if isinstance(symbol, str):
            symbols.setdefault(symbol, []).append(unit.id)
    edges = []
    for edge in graph.edges:
        raw = dict(edge.attributes).get("move_target")
        candidates = symbols.get(raw, []) if isinstance(raw, str) else []
        if len(candidates) == 1:
            edge = replace(edge, target_id=candidates[0], confidence=EdgeConfidence.INFERRED)
        edges.append(edge)
    graph.edges[:] = edges
