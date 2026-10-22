"""Lua declarations, lexical bindings, and conservative call graphs."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from tree_sitter import Node, Parser
from tree_sitter_language_pack import get_language

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import BranchInfo, CodeUnit, NodeKind, Parameter
from trailmark.parsers._common import (
    add_contains_edge,
    add_module_node,
    compute_complexity,
    make_location,
    module_id_from_path,
    node_text,
    parse_directory,
)
from trailmark.parsers._simple import first_child_type

_LOG = logging.getLogger(__name__)
_BRANCHES = frozenset(
    {"if_statement", "elseif_statement", "while_statement", "for_statement", "repeat_statement"}
)


@dataclass
class _Binding:
    available: int
    target: str | None = None
    reassigned: bool = False


@dataclass
class _Scope:
    parent: _Scope | None = None
    bindings: dict[str, list[_Binding]] = field(default_factory=dict)

    def add(self, name: str, binding: _Binding) -> None:
        self.bindings.setdefault(name, []).append(binding)

    def lookup(self, name: str, position: int) -> _Binding | None:
        scope: _Scope | None = self
        while scope is not None:
            candidates = [b for b in scope.bindings.get(name, []) if b.available <= position]
            if candidates:
                return candidates[-1]
            scope = scope.parent
        return None


class LuaParser:
    """Parse Lua source without evaluating tables, metatables, or callbacks."""

    @property
    def language(self) -> str:
        return "lua"

    def __init__(self) -> None:
        self._parser = Parser(get_language("lua"))

    def parse_file(self, file_path: str) -> CodeGraph:
        root = self._parser.parse(Path(file_path).read_bytes()).root_node
        if root.has_error:
            _LOG.warning("Lua syntax errors in %s; graph may be incomplete", file_path)
        module = module_id_from_path(file_path)
        graph = CodeGraph(language="lua", root_path=file_path)
        add_module_node(root, file_path, module, graph)
        extractor = _Extractor(graph, module, file_path)
        extractor.chunk(root)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        return parse_directory(self.parse_file, "lua", dir_path, (".lua",))


def _owned_nodes(root: Node) -> Iterator[Node]:
    stack = list(reversed(root.named_children))
    while stack:
        node = stack.pop()
        if node.type in {"function_declaration", "function_definition"}:
            continue
        yield node
        stack.extend(reversed(node.named_children))


class _Extractor:
    def __init__(self, graph: CodeGraph, module: str, path: str) -> None:
        self.graph = graph
        self.module = module
        self.path = path
        self.pending: list[tuple[str, Node, _Scope]] = []
        self.writes: list[tuple[str, int, _Scope]] = []
        self.globals: dict[str, list[str]] = {}

    def child_id(self, owner: str, name: str, node: Node) -> str:
        ident = f"{owner}:{name}" if owner == self.module else f"{owner}.{name}"
        return f"{ident}@{node.start_byte}" if ident in self.graph.nodes else ident

    def chunk(self, root: Node) -> None:
        ident = f"{self.module}:<chunk>"
        self.unit(root, ident, "<chunk>", NodeKind.FUNCTION, (), root)
        add_contains_edge(self.graph, self.module, ident)
        scope = _Scope()
        for child in root.named_children:
            self.scan(child, scope, self.module)
        global_writes: set[str] = set()
        for name, position, write_scope in self.writes:
            binding = write_scope.lookup(name, position)
            if binding is not None:
                binding.reassigned = True
            else:
                global_writes.add(name)
        for owner, node, call_scope in self.pending:
            callee = node.child_by_field_name("name")
            if callee is None:
                continue
            raw = node_text(callee)
            target = None
            confidence = EdgeConfidence.UNCERTAIN
            if callee.type == "identifier":
                binding = call_scope.lookup(raw, node.start_byte)
                if binding is not None:
                    if not binding.reassigned:
                        target = binding.target
                        if target:
                            confidence = EdgeConfidence.CERTAIN
                elif (
                    call_scope.lookup("_ENV", node.start_byte) is None
                    and "_ENV" not in global_writes
                    and raw not in global_writes
                    and len(self.globals.get(raw, [])) == 1
                ):
                    target = self.globals[raw][0]
                    confidence = EdgeConfidence.INFERRED
                elif (
                    call_scope.lookup("_ENV", node.start_byte) is None
                    and "_ENV" not in global_writes
                    and raw == "require"
                    and raw not in global_writes
                    and raw not in self.globals
                ):
                    self.import_node(node)
            # Always qualify unresolved Lua calls: a unique function in another
            # file is insufficient evidence about Lua globals or receiver tables.
            unresolved = f"{owner}.<dynamic>.{raw}"
            self.graph.edges.append(
                CodeEdge(
                    owner,
                    target or unresolved,
                    EdgeKind.CALLS,
                    confidence=confidence,
                    location=make_location(node, self.path),
                )
            )

    def unit(
        self,
        node: Node,
        ident: str,
        name: str,
        kind: NodeKind,
        params: tuple[Parameter, ...],
        body: Node | None,
    ) -> None:
        branches = []
        if body is not None:
            for child in _owned_nodes(body):
                if child.type in _BRANCHES:
                    condition = child.child_by_field_name("condition")
                    branches.append(
                        BranchInfo(
                            make_location(child, self.path),
                            node_text(condition) if condition else child.type,
                        )
                    )
        self.graph.nodes[ident] = CodeUnit(
            id=ident,
            name=name,
            kind=kind,
            location=make_location(node, self.path),
            parameters=params,
            cyclomatic_complexity=compute_complexity(branches),
            branches=tuple(branches),
        )

    def function(
        self,
        node: Node,
        scope: _Scope,
        owner: str,
        raw_name: str | None = None,
        local: bool = False,
        available: int | None = None,
    ) -> str:
        name_node = node.child_by_field_name("name")
        raw = raw_name or (node_text(name_node) if name_node else f"<lambda>@{node.start_byte}")
        normalized = raw.replace(":", ".")
        ident = self.child_id(owner, normalized, node)
        params_node = node.child_by_field_name("parameters")
        params = (
            [
                Parameter(node_text(p))
                for p in params_node.named_children
                if p.type in {"identifier", "vararg_expression"}
            ]
            if params_node
            else []
        )
        if name_node is not None and name_node.type == "method_index_expression":
            params.insert(0, Parameter("self"))
        body = node.child_by_field_name("body")
        self.unit(
            node,
            ident,
            normalized.rsplit(".", 1)[-1],
            NodeKind.METHOD if "." in normalized else NodeKind.FUNCTION,
            tuple(params),
            body,
        )
        add_contains_edge(self.graph, owner, ident)
        if local:
            scope.add(raw, _Binding(node.start_byte if available is None else available, ident))
        elif name_node is not None and name_node.type == "identifier":
            existing = scope.lookup(raw, node.start_byte)
            if existing is not None:
                self.writes.append((raw, node.start_byte, scope))
            else:
                self.globals.setdefault(raw, []).append(ident)
        elif (
            raw_name is not None
            and "." not in normalized
            and scope.lookup(raw, node.start_byte) is None
        ):
            self.globals.setdefault(raw, []).append(ident)
        child_scope = _Scope(scope)
        for parameter in params:
            child_scope.add(parameter.name, _Binding(node.start_byte))
        if body is not None:
            for child in body.named_children:
                self.scan(child, child_scope, ident)
        return ident

    def scan(self, node: Node, scope: _Scope, owner: str) -> None:
        if node.type == "function_declaration":
            local = any(c.type == "local" for c in node.children)
            self.function(node, scope, owner, local=local)
            return
        if node.type == "function_definition":
            self.function(node, scope, owner)
            return
        if node.type in {"variable_declaration", "assignment_statement"}:
            self.assignment(node, scope, owner)
            return
        if node.type == "block":
            nested = _Scope(scope)
            for child in node.named_children:
                self.scan(child, nested, owner)
            return
        if node.type in {"for_statement", "repeat_statement"}:
            nested = _Scope(scope)
            body = node.child_by_field_name("body")
            clause = node.child_by_field_name("clause")
            if clause is not None:
                # Iterator/range expressions see the outer scope.
                self.scan(clause, scope, owner)
                names = clause.children_by_field_name("name")
                variables = first_child_type(clause, {"variable_list"})
                if variables is not None:
                    names = variables.children_by_field_name("name")
                for name in names:
                    nested.add(
                        node_text(name), _Binding(body.start_byte if body else node.start_byte)
                    )
            if body is not None:
                for child in body.named_children:
                    self.scan(child, nested, owner)
            condition = node.child_by_field_name("condition")
            if condition is not None:
                self.scan(condition, nested, owner)
            return
        if node.type == "function_call":
            caller = f"{self.module}:<chunk>" if owner == self.module else owner
            self.pending.append((caller, node, scope))
        for child in node.named_children:
            self.scan(child, scope, owner)

    def assignment(self, node: Node, scope: _Scope, owner: str) -> None:
        local = node.type == "variable_declaration"
        assignment = first_child_type(node, {"assignment_statement"}) if local else node
        if assignment is None:
            # A local declaration without an initializer is still a binding.
            assignment = node
        variables = first_child_type(assignment, {"variable_list"})
        expressions = first_child_type(assignment, {"expression_list"})
        names = variables.children_by_field_name("name") if variables else []
        values = expressions.named_children if expressions else []
        handled: set[int] = set()
        for index, name_node in enumerate(names):
            raw = node_text(name_node)
            value = values[index] if index < len(values) else None
            if local and name_node.type == "identifier":
                if value is not None and value.type == "function_definition":
                    self.function(value, scope, owner, raw, local=True, available=node.end_byte)
                    handled.add(value.id)
                else:
                    scope.add(raw, _Binding(node.end_byte))
            else:
                if name_node.type == "identifier":
                    self.writes.append((raw, node.start_byte, scope))
                if value is not None and value.type == "function_definition":
                    self.function(value, scope, owner, raw)
                    handled.add(value.id)
            if value is not None and value.type == "table_constructor":
                for item in value.named_children:
                    key = item.child_by_field_name("name")
                    function = item.child_by_field_name("value")
                    if (
                        key is not None
                        and key.type == "identifier"
                        and function is not None
                        and function.type == "function_definition"
                    ):
                        self.function(function, scope, owner, f"{raw}.{node_text(key)}")
                        handled.add(function.id)
        if expressions is not None:
            self.scan_values(expressions, scope, owner, handled)
        # Index expressions on assignment targets can themselves call functions.
        for name in names:
            for child in name.named_children:
                self.scan(child, scope, owner)

    def scan_values(self, node: Node, scope: _Scope, owner: str, handled: set[int]) -> None:
        for child in node.named_children:
            if child.id in handled:
                continue
            if child.type in {"table_constructor", "field"}:
                self.scan_values(child, scope, owner, handled)
            else:
                self.scan(child, scope, owner)

    def import_node(self, node: Node) -> None:
        arguments = node.child_by_field_name("arguments")
        if arguments is None or len(arguments.named_children) != 1:
            return
        string = arguments.named_children[0]
        if string.type != "string":
            return
        content = string.child_by_field_name("content")
        if content is None:
            return
        target = node_text(content)
        if target not in self.graph.dependencies:
            self.graph.dependencies.append(target)
            self.graph.edges.append(
                CodeEdge(
                    self.module, target, EdgeKind.IMPORTS, location=make_location(node, self.path)
                )
            )
