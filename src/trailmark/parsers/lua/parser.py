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
from trailmark.parsers._exceptions import collect_exception_events
from trailmark.parsers._simple import first_child_type

_LOG = logging.getLogger(__name__)
_BRANCHES = frozenset(
    {"if_statement", "elseif_statement", "while_statement", "for_statement", "repeat_statement"}
)


@dataclass
class _Table:
    methods: dict[str, list[tuple[str, int, Node]]] = field(default_factory=dict)
    writes: set[str] = field(default_factory=set)
    escaped: bool = False


@dataclass
class _Binding:
    available: int
    target: str | None = None
    reassigned: bool = False
    table: _Table | None = None
    initializer: Node | None = None


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
        collect_exception_events(root, file_path, graph)
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


def _unparenthesized(node: Node) -> Node:
    while node.type == "parenthesized_expression" and len(node.named_children) == 1:
        node = node.named_children[0]
    return node


def _receiver_root(node: Node) -> Node:
    node = _unparenthesized(node)
    while (table := node.child_by_field_name("table")) is not None:
        node = _unparenthesized(table)
    return node


def _value_names(node: Node) -> Iterator[str]:
    """Names whose values can escape through an expression or container."""
    node = _unparenthesized(node)
    if node.type == "identifier":
        yield node_text(node)
    elif node.type not in {
        "function_call",
        "dot_index_expression",
        "method_index_expression",
        "bracket_index_expression",
        "function_definition",
    }:
        for child in node.named_children:
            yield from _value_names(child)


class _Extractor:
    def __init__(self, graph: CodeGraph, module: str, path: str) -> None:
        self.graph = graph
        self.module = module
        self.path = path
        self.pending: list[tuple[str, Node, _Scope]] = []
        self.writes: list[tuple[str, int, _Scope]] = []
        self.globals: dict[str, list[str]] = {}
        self.functions: dict[str, tuple[Node, _Scope]] = {}
        self.function_scopes: dict[int, _Scope] = {}
        self.function_parameters: dict[int, list[str]] = {}
        self.node_scopes: dict[int, _Scope] = {}
        self.method_tables: dict[int, _Table] = {}
        self.global_writes: set[str] = set()

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
        self.global_writes = global_writes
        self.table_escapes()
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
            elif callee.type in {"dot_index_expression", "method_index_expression"}:
                member = self.table_member(raw, call_scope, node.start_byte)
                if member is not None:
                    table, name = member
                    definitions = table.methods.get(name, [])
                    if not table.escaped and name not in table.writes and len(definitions) == 1:
                        ident, available, _ = definitions[0]
                        if owner != f"{self.module}:<chunk>" or available <= node.start_byte:
                            target, confidence = ident, EdgeConfidence.INFERRED
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

    def table_member(self, raw: str, scope: _Scope, position: int) -> tuple[_Table, str] | None:
        parts = raw.replace(":", ".").split(".")
        if len(parts) != 2 or not all(part.isidentifier() for part in parts):
            return None
        binding = scope.lookup(parts[0], position)
        return (
            (binding.table, parts[1])
            if binding is not None and binding.table is not None and not binding.reassigned
            else None
        )

    def table_escapes(self) -> None:
        """Invalidate tables passed to unknown writers; keep known library reads."""
        for function, _ in self.functions.values():
            table = self.method_tables.get(function.id)
            name = function.child_by_field_name("name")
            if (
                table is not None
                and name is not None
                and name.type == "method_index_expression"
                and self.parameter_written(function, "self", table)
            ):
                table.escaped = True
        for _, node, scope in self.pending:
            callee = node.child_by_field_name("name")
            arguments = node.child_by_field_name("arguments")
            if callee is None or arguments is None:
                continue
            callable_binding = scope.lookup(node_text(_unparenthesized(callee)), node.start_byte)
            if callable_binding is not None and callable_binding.table is not None:
                # A permitted __call metatable may mutate the receiver when used.
                callable_binding.table.escaped = True
            raw = node_text(callee)
            member = self.table_member(raw, scope, node.start_byte)
            for index, argument in enumerate(arguments.named_children):
                argument = _unparenthesized(argument)
                if argument.type != "identifier":
                    for name in _value_names(argument):
                        binding = scope.lookup(name, node.start_byte)
                        if binding is not None and binding.table is not None:
                            binding.table.escaped = True
                    continue
                binding = scope.lookup(node_text(argument), node.start_byte)
                if binding is None or binding.table is None:
                    continue
                unshadowed = self.unshadowed(raw, scope, node.start_byte)
                if self.readonly_builtin(raw, scope, node.start_byte):
                    continue
                if unshadowed and raw == "setmetatable" and len(arguments.named_children) == 2:
                    metatable = arguments.named_children[1]
                    if metatable.type == "table_constructor" and all(
                        (name := field.child_by_field_name("name")) is not None
                        and name.type == "identifier"
                        and not any(child.type == "[" for child in field.children)
                        and node_text(name) in {"__call", "__metatable"}
                        for field in metatable.named_children
                        if field.type == "field"
                    ):
                        continue
                if member is not None and member[0] is binding.table:
                    definitions = member[0].methods.get(member[1], [])
                    if len(definitions) == 1:
                        function = definitions[0][2]
                        names = self.function_parameters[function.id]
                        offset = 1 if callee.type == "method_index_expression" else 0
                        if index + offset < len(names) and not self.parameter_written(
                            function, names[index + offset], binding.table
                        ):
                            continue
                binding.table.escaped = True

    def unshadowed(self, name: str, scope: _Scope, position: int) -> bool:
        return (
            scope.lookup(name, position) is None
            and name not in self.globals
            and name not in self.global_writes
            and "_ENV" not in self.global_writes
            and scope.lookup("_ENV", position) is None
        )

    def readonly_builtin(
        self, name: str, scope: _Scope, position: int, seen: frozenset[int] = frozenset()
    ) -> bool:
        binding = scope.lookup(name, position)
        if binding is not None:
            value = binding.initializer
            return (
                not binding.reassigned
                and value is not None
                and value.type == "identifier"
                and value.id not in seen
                and self.readonly_builtin(
                    node_text(value),
                    self.node_scopes.get(value.id, scope),
                    value.start_byte,
                    seen | {value.id},
                )
            )
        return name in {
            "pairs",
            "ipairs",
            "next",
            "type",
            "tostring",
            "rawequal",
            "rawget",
        } and self.unshadowed(name, scope, position)

    def bound_function(self, callee: Node, scope: _Scope, table: _Table) -> Node | None:
        callee = _unparenthesized(callee)
        raw = node_text(callee)
        target = None
        if callee.type == "identifier":
            binding = scope.lookup(raw, callee.start_byte)
            if binding is not None and not binding.reassigned:
                target = binding.target
            elif (
                binding is None
                and scope.lookup("_ENV", callee.start_byte) is None
                and "_ENV" not in self.global_writes
                and raw not in self.global_writes
                and len(self.globals.get(raw, [])) == 1
            ):
                target = self.globals[raw][0]
        elif callee.type in {"dot_index_expression", "method_index_expression"}:
            member = self.table_member(raw, scope, callee.start_byte)
            if member is not None and member[0] is table and member[1] not in table.writes:
                definitions = table.methods.get(member[1], [])
                if len(definitions) == 1:
                    target = definitions[0][0]
        return self.functions[target][0] if target in self.functions else None

    def returns_readonly(self, function: Node) -> bool:
        """Recognize helpers whose explicit results are standard read-only iterators."""
        body = function.child_by_field_name("body")
        if body is None:
            return False
        scope = self.function_scopes[function.id]
        returns = [node for node in _owned_nodes(body) if node.type == "return_statement"]
        return bool(returns) and all(
            (values := first_child_type(node, {"expression_list"})) is not None
            and len(values.named_children) == 1
            and (value := _unparenthesized(values.named_children[0])).type == "identifier"
            and self.readonly_builtin(
                node_text(value), self.node_scopes.get(value.id, scope), value.start_byte
            )
            for node in returns
        )

    def readonly_iterator(self, callee: Node, scope: _Scope, table: _Table) -> bool:
        """An iterator selected by a source-backed local factory is read-only."""
        callee = _unparenthesized(callee)
        if callee.type == "identifier":
            binding = scope.lookup(node_text(callee), callee.start_byte)
            if binding is None or binding.reassigned or binding.initializer is None:
                return False
            callee = _unparenthesized(binding.initializer)
            scope = self.node_scopes.get(callee.id, scope)
        if callee.type != "function_call":
            return False
        selector = callee.child_by_field_name("name")
        factory = self.bound_function(selector, scope, table) if selector is not None else None
        return factory is not None and self.returns_readonly(factory)

    def parameter_written(
        self,
        function: Node,
        name: str,
        table: _Table,
        seen: frozenset[tuple[int, str]] = frozenset(),
    ) -> bool:
        if name == "...":
            # Packing and forwarding varargs needs tuple/alias dataflow.
            return True
        key = (function.id, name)
        if key in seen or len(seen) >= 16:
            return True
        seen = seen | {key}
        body = function.child_by_field_name("body")
        if body is None:
            return False
        scope = self.function_scopes[function.id]
        # Captured parameters can be handed to a writer from a nested closure.
        # Do not attempt closure effects or prove whether that closure executes.
        stack = list(body.named_children)
        while stack:
            nested = stack.pop()
            if nested.type in {"function_declaration", "function_definition"}:
                descendants = list(nested.named_children)
                while descendants:
                    child = descendants.pop()
                    if child.type == "identifier" and node_text(child) == name:
                        return True
                    descendants.extend(child.named_children)
                continue
            stack.extend(nested.named_children)
        for node in _owned_nodes(body):
            scope = self.node_scopes.get(node.id, self.function_scopes[function.id])
            if node.type == "identifier" and node_text(node) == name:
                value = node
                while value.parent is not None and value.parent.type == "parenthesized_expression":
                    value = value.parent
                parent = value.parent
                if parent is None or not (
                    parent.type == "arguments"
                    or parent.type in {"dot_index_expression", "bracket_index_expression"}
                    and parent.child_by_field_name("table") == value
                ):
                    # All other value uses require unmodeled alias/metamethod
                    # semantics. Calls and field reads are checked separately.
                    return True
            if node.type == "function_call":
                callee = node.child_by_field_name("name")
                arguments = node.child_by_field_name("arguments")
                if callee is None or arguments is None:
                    continue
                raw = node_text(_unparenthesized(callee))
                if node_text(_receiver_root(callee)) == name:
                    return True
                for index, argument in enumerate(arguments.named_children):
                    if name not in _value_names(argument):
                        continue
                    if node_text(_unparenthesized(argument)) != name:
                        return True
                    if self.readonly_builtin(raw, scope, node.start_byte):
                        continue
                    target = self.bound_function(callee, scope, table)
                    if target is not None:
                        names = self.function_parameters[target.id]
                        offset = 1 if callee.type == "method_index_expression" else 0
                        if index + offset < len(names) and not self.parameter_written(
                            target, names[index + offset], table, seen
                        ):
                            continue
                    if self.readonly_iterator(callee, scope, table):
                        continue
                    return True
            if node.type == "return_statement":
                values = first_child_type(node, {"expression_list"})
                if values is not None and any(
                    name in _value_names(v) for v in values.named_children
                ):
                    return True
            if node.type != "assignment_statement":
                continue
            variables = first_child_type(node, {"variable_list"})
            if variables is not None and any(
                target.type != "identifier" and node_text(_receiver_root(target)) == name
                for target in variables.children_by_field_name("name")
            ):
                return True
            values = first_child_type(node, {"expression_list"})
            if values is not None and any(
                name in _value_names(value) for value in values.named_children
            ):
                return True
        return False

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
        member = self.table_member(raw, scope, node.start_byte)
        if member is not None:
            table, name = member
            self.method_tables[node.id] = table
            parent = node.parent
            conditional = owner != self.module
            while parent is not None:
                conditional = conditional or parent.type in _BRANCHES
                parent = parent.parent
            if conditional:
                table.writes.add(name)
            else:
                table.methods.setdefault(name, []).append((ident, node.end_byte, node))
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
        self.functions[ident] = (node, child_scope)
        self.function_scopes[node.id] = child_scope
        self.function_parameters[node.id] = [parameter.name for parameter in params]
        if body is not None:
            for child in body.named_children:
                self.scan(child, child_scope, ident)
        return ident

    def scan(self, node: Node, scope: _Scope, owner: str) -> None:
        self.node_scopes[node.id] = scope
        if node.type == "return_statement" and owner != self.module:
            # Ordinary chunk return exports a module. A nested getter can expose
            # an alias that writes the table before another method is invoked.
            values = first_child_type(node, {"expression_list"})
            if values is not None:
                for value in values.named_children:
                    for name in _value_names(value):
                        binding = scope.lookup(name, value.start_byte)
                        if binding is not None and binding.table is not None:
                            binding.table.escaped = True
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
                    scope.add(
                        raw,
                        _Binding(
                            node.end_byte,
                            table=_Table()
                            if value is not None and value.type == "table_constructor"
                            else None,
                            initializer=value,
                        ),
                    )
            else:
                if name_node.type == "identifier":
                    self.writes.append((raw, node.start_byte, scope))
                elif (
                    value is None
                    or value.type != "function_definition"
                    or self.table_member(raw, scope, node.start_byte) is None
                ):
                    member = self.table_member(raw, scope, node.start_byte)
                    if member is not None:
                        member[0].writes.add(member[1])
                    else:
                        root = _receiver_root(name_node)
                        binding = scope.lookup(node_text(root), node.start_byte)
                        if binding is not None and binding.table is not None:
                            binding.table.escaped = True
                if value is not None and value.type == "function_definition":
                    self.function(value, scope, owner, raw)
                    handled.add(value.id)
            if value is not None:
                for value_name in _value_names(value):
                    alias = scope.lookup(value_name, node.start_byte)
                    if alias is not None and alias.table is not None:
                        alias.table.escaped = True
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
