"""Zig declarations and conservative direct-call extraction using tree-sitter."""

from __future__ import annotations

import ast
import dataclasses
import logging
import re
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
from trailmark.parsers._evidence import collect_source_evidence
from trailmark.parsers._simple import descendants, first_child_type

_LOG = logging.getLogger(__name__)
_BRANCHES = frozenset(
    {
        "if_statement",
        "if_expression",
        "while_statement",
        "while_expression",
        "for_statement",
        "for_expression",
        "switch_case",
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
        collect_source_evidence(root, file_path, graph)
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
            if decl.type == "function_declaration":
                self.function(decl, owner)
            elif decl.type == "variable_declaration":
                name_node = first_child_type(decl, {"identifier"})
                if name_node is None:
                    continue
                name = _identifier(node_text(name_node))
                self.bindings[owner].add(name)
                # Only an immediate initializer container, not a container returned
                # by a function or a comptime expression, owns this declaration.
                container = first_child_type(
                    decl,
                    {
                        "struct_declaration",
                        "enum_declaration",
                        "union_declaration",
                        "opaque_declaration",
                    },
                )
                if container is not None:
                    kind = container.type.removesuffix("_declaration")
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
            if decl.type == "variable_declaration":
                self.imports(decl)

    def function(self, decl: Node, owner: str) -> None:
        name_node = decl.child_by_field_name("name")
        if name_node is None:
            return
        name = _identifier(node_text(name_node))
        ident = self.child_id(owner, name)
        params_node = first_child_type(decl, {"parameters"})
        parameters: list[Parameter] = []
        if params_node is not None:
            for index, param in enumerate(params_node.named_children):
                param_name = param.child_by_field_name("name")
                param_type = param.child_by_field_name("type")
                if param_name is not None or param_type is not None:
                    parameters.append(
                        Parameter(
                            _identifier(node_text(param_name))
                            if param_name is not None
                            else f"$arg{index}",
                            TypeRef(node_text(param_type)) if param_type else None,
                        )
                    )
                elif node_text(param).strip() == "...":
                    parameters.append(Parameter("..."))
        result = decl.child_by_field_name("type")
        body = decl.child_by_field_name("body")
        branches = (
            [
                BranchInfo(make_location(n, self.path), node_text(n).split("{", 1)[0])
                for n in _body_nodes(body)
                if n.type in _BRANCHES
            ]
            if body
            else []
        )
        attributes = (
            ("zig_public", any(n.type == "pub" for n in decl.children)),
            ("zig_export", any(n.type == "export" for n in decl.children)),
            ("zig_extern", any(n.type == "extern" for n in decl.children)),
            ("variadic", any(p.name == "..." for p in parameters)),
        )
        location = make_location(decl, self.path)
        if decl.children[0].type == "pub":
            # The original grammar kept pub outside the declaration span.
            start = decl.children[1].start_point
            location = dataclasses.replace(
                location, start_line=start.row + 1, start_col=start.column
            )
        self.graph.nodes[ident] = CodeUnit(
            id=ident,
            name=name,
            kind=NodeKind.FUNCTION if owner == self.module else NodeKind.METHOD,
            location=location,
            parameters=tuple(parameters),
            return_type=TypeRef(node_text(result)) if result is not None else None,
            cyclomatic_complexity=compute_complexity(branches) if body else None,
            branches=tuple(branches),
            attributes=attributes,
        )
        add_contains_edge(self.graph, owner, ident)
        if body is not None:
            self.bodies.append((ident, owner, body))

    def imports(self, root: Node) -> None:
        for node in descendants(root):
            if node.type != "builtin_function" or not node.named_children:
                continue
            if node_text(node.named_children[0]) != "@import":
                continue
            args = first_child_type(node, {"arguments"})
            if args is None:
                continue
            literals = [n for n in descendants(args) if n.type == "string"]
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

    def resolve(self, raw: str, owner: str, shadows: set[str]) -> tuple[str | None, bool]:
        parts = _path_parts(raw)
        if not parts:
            return None, True
        raw = ".".join(parts)
        if parts[0] in shadows or raw.startswith("@"):
            return None, parts[0] in shadows
        scope = owner
        while True:
            ident = self.child_id(scope, raw)
            target = self.graph.nodes.get(ident)
            if target is not None and target.kind in {NodeKind.FUNCTION, NodeKind.METHOD}:
                return ident, True
            if parts[0] in self.bindings.get(scope, set()):
                return None, True
            if scope == self.module:
                return None, False
            scope = self.parents[scope]

    def calls(self) -> None:
        for ident, owner, body in self.bodies:
            self.imports(body)
            shadows = {p.name for p in self.graph.nodes[ident].parameters}
            # Without flow/type evaluation, any local binding or capture of a
            # name makes calls through that name uncertain throughout the body.
            for node in _body_nodes(body):
                if node.type == "variable_declaration":
                    shadows.update(
                        _identifier(node_text(binding))
                        for child in node.children
                        if child.type in {"const", "var"}
                        and (binding := child.next_named_sibling) is not None
                        and binding.type == "identifier"
                    )
                elif node.type == "payload":
                    shadows.update(
                        _identifier(node_text(n))
                        for n in descendants(node)
                        if n.type == "identifier"
                    )
            recovery = body.has_error
            local_types = _local_types(body, self.graph.nodes[ident].parameters)
            for node in _body_nodes(body):
                if node.type != "arguments" or node.parent is None:
                    continue
                parent = node.parent
                raw = node_text(parent)[: node.start_byte - parent.start_byte].strip()
                typed = _typed_callee(parent, local_types)
                target, bound = self.resolve(typed or raw, owner, shadows)
                if recovery:
                    target, bound = None, True
                # Dotted identities prevent the directory heuristic from binding
                # a callback or unknown receiver to an unrelated free function.
                unresolved = (
                    f"{ident}.<dynamic>.{raw}"
                    if bound and target is None and "." not in raw
                    else raw
                )
                self.graph.edges.append(
                    CodeEdge(
                        ident,
                        target or unresolved,
                        EdgeKind.CALLS,
                        confidence=EdgeConfidence.INFERRED
                        if target and typed
                        else EdgeConfidence.CERTAIN
                        if target
                        else EdgeConfidence.UNCERTAIN,
                        location=make_location(node, self.path),
                        attributes=(
                            ("binding_blocked", bound and target is None),
                            ("syntax_recovery", recovery),
                        ),
                    )
                )


def _body_nodes(body: Node) -> Iterator[Node]:
    """Do not attribute nested container methods to an enclosing function."""
    stack = list(reversed(body.named_children))
    while stack:
        node = stack.pop()
        if node.type in {
            "struct_declaration",
            "enum_declaration",
            "union_declaration",
            "opaque_declaration",
            "function_declaration",
        }:
            continue
        yield node
        stack.extend(reversed(node.named_children))


def _identifier(raw: str) -> str:
    if raw.startswith('@"'):
        try:
            raw = ast.literal_eval(raw[1:])
        except (ValueError, SyntaxError):
            return raw
    return raw.replace("\\", "\\\\").replace(".", "\\.")


def _path_parts(raw: str) -> list[str]:
    """Use identifier tokens so whitespace and quoted dots do not split names."""
    tokens = re.findall(r'@"(?:\\.|[^"\\])*"|@[A-Za-z_]\w*|[A-Za-z_]\w*|\.', raw)
    if re.sub(r'@"(?:\\.|[^"\\])*"|@[A-Za-z_]\w*|[A-Za-z_]\w*|\.|\s+', "", raw):
        return []
    if not tokens or any(part != "." for part in tokens[1::2]):
        return []
    return [_identifier(token) for token in tokens[::2]]


def _local_types(body: Node, parameters: tuple[Parameter, ...]) -> dict[str, tuple[str, Node]]:
    """Use only one explicitly typed local binding; captures and duplicates block it."""
    bindings: dict[str, list[tuple[str, Node] | None]] = {p.name: [None] for p in parameters}
    for node in _body_nodes(body):
        if node.type == "payload":
            for name in descendants(node):
                if name.type == "identifier":
                    bindings.setdefault(_identifier(node_text(name)), []).append(None)
        elif node.type == "variable_declaration":
            name = first_child_type(node, {"identifier"})
            if name is None:
                continue
            annotation = node.child_by_field_name("type")
            raw = node_text(annotation) if annotation is not None else ""
            raw = re.sub(r"^\*\s*(?:const\s+)?", "", raw).strip()
            parts = _path_parts(raw)
            binding = (
                (".".join(parts), node)
                if parts and node.parent is not None and node.parent.type == "block"
                else None
            )
            bindings.setdefault(_identifier(node_text(name)), []).append(binding)
    return {name: items[0] for name, items in bindings.items() if len(items) == 1 and items[0]}


def _typed_callee(call: Node, local_types: dict[str, tuple[str, Node]]) -> str | None:
    function = call.child_by_field_name("function")
    if function is None or function.type != "field_expression":
        return None
    receiver = function.child_by_field_name("object")
    member = function.child_by_field_name("member")
    if member is None:
        return None
    if receiver is None:
        declaration = call.parent
        if declaration is None or declaration.type != "variable_declaration":
            return None
        name = first_child_type(declaration, {"identifier"})
        if name is None or declaration.named_children[-1] != call:
            return None
    elif receiver.type == "identifier":
        name = receiver
    else:
        return None
    binding = local_types.get(_identifier(node_text(name)))
    if binding is None:
        return None
    type_name, declaration = binding
    scope = declaration.parent
    if scope is None or not (
        declaration.start_byte < call.start_byte < scope.end_byte
        and scope.start_byte <= call.start_byte
    ):
        return None
    return f"{type_name}.{_identifier(node_text(member))}"
