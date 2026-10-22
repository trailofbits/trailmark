"""Zig declarations and conservative direct-call extraction using tree-sitter."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

from tree_sitter import Node, Parser
from tree_sitter_language_pack import get_language

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
from trailmark.parsers._simple import descendants, first_child_type

_LOG = logging.getLogger(__name__)
_BRANCHES = frozenset(
    {
        "IfStatement",
        "IfExpr",
        "WhileStatement",
        "WhileExpr",
        "ForStatement",
        "ForExpr",
        "SwitchProng",
    }
)


class ZigParser:
    """Parse Zig functions and named containers; do not evaluate comptime code."""

    @property
    def language(self) -> str:
        return "zig"

    def __init__(self) -> None:
        self._parser = Parser(get_language("zig"))

    def parse_file(self, file_path: str) -> CodeGraph:
        root = self._parser.parse(Path(file_path).read_bytes()).root_node
        if root.has_error:
            _LOG.warning("Zig syntax errors in %s; graph may be incomplete", file_path)
        graph = CodeGraph(language="zig", root_path=file_path)
        module = module_id_from_path(file_path)
        add_module_node(root, file_path, module, graph)
        extractor = _Extractor(graph, file_path, module)
        extractor.declarations(root, module)
        extractor.calls()
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        return parse_directory(self.parse_file, "zig", dir_path, (".zig",))


class _Extractor:
    def __init__(self, graph: CodeGraph, path: str, module: str) -> None:
        self.graph = graph
        self.path = path
        self.module = module
        self.parents: dict[str, str] = {}
        self.bodies: list[tuple[str, str, Node]] = []
        self.bindings: dict[str, set[str]] = {}

    def child_id(self, owner: str, name: str) -> str:
        return f"{owner}:{name}" if owner == self.module else f"{owner}.{name}"

    def declarations(self, root: Node, owner: str) -> None:
        self.bindings[owner] = set()
        for decl in root.named_children:
            if decl.type != "Decl":
                continue
            proto = first_child_type(decl, {"FnProto"})
            var = first_child_type(decl, {"VarDecl"})
            if proto is not None:
                self.function(decl, proto, owner)
            elif var is not None:
                name_node = var.child_by_field_name("variable_type_function")
                if name_node is None:
                    continue
                name = node_text(name_node)
                self.bindings[owner].add(name)
                # Only an immediate initializer container, not a container returned
                # by a function or a comptime expression, owns this declaration.
                expr = first_child_type(var, {"ErrorUnionExpr"})
                suffix = first_child_type(expr, {"SuffixExpr"}) if expr is not None else None
                container = (
                    first_child_type(suffix, {"ContainerDecl"}) if suffix is not None else None
                )
                if container is not None:
                    kind_node = first_child_type(container, {"ContainerDeclType"})
                    kind = node_text(kind_node).split("(", 1)[0].strip() if kind_node else "struct"
                    ident = self.child_id(owner, name)
                    self.graph.nodes[ident] = CodeUnit(
                        id=ident,
                        name=name,
                        kind=NodeKind.ENUM if kind == "enum" else NodeKind.STRUCT,
                        location=make_location(decl, self.path),
                        attributes=(("zig_container_kind", kind),),
                    )
                    self.parents[ident] = owner
                    add_contains_edge(self.graph, owner, ident)
                    self.declarations(container, ident)
        # Imports anywhere in this lexical container, excluding function bodies,
        # are recorded separately below when traversing bodies.
        for decl in root.named_children:
            if decl.type == "Decl" and first_child_type(decl, {"VarDecl"}) is not None:
                self.imports(decl)

    def function(self, decl: Node, proto: Node, owner: str) -> None:
        name_node = proto.child_by_field_name("function")
        if name_node is None:
            return
        name = node_text(name_node)
        ident = self.child_id(owner, name)
        params_node = first_child_type(proto, {"ParamDeclList"})
        parameters: list[Parameter] = []
        if params_node is not None:
            for param in params_node.named_children:
                param_name = param.child_by_field_name("parameter")
                param_type = first_child_type(param, {"ParamType"})
                if param_name is not None:
                    parameters.append(
                        Parameter(
                            node_text(param_name),
                            TypeRef(node_text(param_type)) if param_type else None,
                        )
                    )
        result_parts: list[str] = []
        after_params = False
        for child in proto.children:
            if child == params_node:
                after_params = True
            elif after_params and child.type not in {
                "ByteAlign",
                "LinkSection",
                "CallConv",
                "line_comment",
            }:
                result_parts.append(node_text(child))
        body = first_child_type(decl, {"Block"})
        branches = (
            [
                BranchInfo(make_location(n, self.path), node_text(n).split("{", 1)[0])
                for n in _body_nodes(body)
                if n.type in _BRANCHES
            ]
            if body
            else []
        )
        previous = decl.prev_sibling
        while previous is not None and previous.type in {"line_comment", "doc_comment"}:
            previous = previous.prev_sibling
        attributes = (
            ("zig_public", previous is not None and previous.type == "pub"),
            ("zig_export", any(n.type == "export" for n in decl.children)),
            ("zig_extern", any(n.type == "extern" for n in decl.children)),
        )
        self.graph.nodes[ident] = CodeUnit(
            id=ident,
            name=name,
            kind=NodeKind.FUNCTION if owner == self.module else NodeKind.METHOD,
            location=make_location(decl, self.path),
            parameters=tuple(parameters),
            return_type=TypeRef(" ".join(result_parts).replace("! ", "!"))
            if result_parts
            else None,
            cyclomatic_complexity=compute_complexity(branches) if body else None,
            branches=tuple(branches),
            attributes=attributes,
        )
        add_contains_edge(self.graph, owner, ident)
        if body is not None:
            self.bodies.append((ident, owner, body))

    def imports(self, root: Node) -> None:
        for node in descendants(root):
            if node.type != "SuffixExpr" or not node.named_children:
                continue
            if node_text(node.named_children[0]) != "@import":
                continue
            args = first_child_type(node, {"FnCallArguments"})
            if args is None:
                continue
            literals = [n for n in descendants(args) if n.type == "STRINGLITERALSINGLE"]
            if len(literals) == 1:
                target = node_text(literals[0])[1:-1]
                if target not in self.graph.dependencies:
                    self.graph.dependencies.append(target)
                    self.graph.edges.append(
                        CodeEdge(
                            self.module,
                            target,
                            EdgeKind.IMPORTS,
                            location=make_location(node, self.path),
                        )
                    )

    def resolve(self, raw: str, owner: str, shadows: set[str]) -> str | None:
        parts = raw.split(".")
        if parts[0] in shadows or raw.startswith("@"):
            return None
        scope = owner
        while True:
            ident = self.child_id(scope, raw)
            target = self.graph.nodes.get(ident)
            if target is not None and target.kind in {NodeKind.FUNCTION, NodeKind.METHOD}:
                return ident
            if parts[0] in self.bindings.get(scope, set()):
                return None
            if scope == self.module:
                return None
            scope = self.parents[scope]

    def calls(self) -> None:
        for ident, owner, body in self.bodies:
            self.imports(body)
            shadows = {p.name for p in self.graph.nodes[ident].parameters}
            # Without flow/type evaluation, any local binding or capture of a
            # name makes calls through that name uncertain throughout the body.
            for node in _body_nodes(body):
                if node.type == "VarDecl":
                    binding = node.child_by_field_name("variable_type_function")
                    if binding is not None:
                        shadows.add(node_text(binding))
                elif node.type in {"Payload", "PtrPayload", "PtrIndexPayload", "PtrListPayload"}:
                    shadows.update(
                        node_text(n) for n in descendants(node) if n.type == "IDENTIFIER"
                    )
            for node in _body_nodes(body):
                if node.type != "FnCallArguments" or node.parent is None:
                    continue
                parent = node.parent
                if parent.type == "FieldOrFnCall" and parent.parent is not None:
                    parent = parent.parent
                raw = node_text(parent)[: node.start_byte - parent.start_byte].strip()
                target = self.resolve(raw, owner, shadows)
                # Dotted identities prevent the directory heuristic from binding
                # a callback or unknown receiver to an unrelated free function.
                unresolved = f"{ident}.<dynamic>.{raw}" if raw.split(".")[0] in shadows else raw
                self.graph.edges.append(
                    CodeEdge(
                        ident,
                        target or unresolved,
                        EdgeKind.CALLS,
                        confidence=EdgeConfidence.CERTAIN if target else EdgeConfidence.UNCERTAIN,
                        location=make_location(node, self.path),
                    )
                )


def _body_nodes(body: Node) -> Iterator[Node]:
    """Do not attribute nested container methods to an enclosing function."""
    stack = list(reversed(body.named_children))
    while stack:
        node = stack.pop()
        if node.type in {"ContainerDecl", "FnProto"}:
            continue
        yield node
        stack.extend(reversed(node.named_children))
