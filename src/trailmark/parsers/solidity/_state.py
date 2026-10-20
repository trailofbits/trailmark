"""Direct, declaration-level Solidity storage dependencies.

This pass resolves names within one contract. It does not infer storage aliases,
inheritance, library effects, or accesses through assembly and external calls.
"""

from __future__ import annotations

from dataclasses import dataclass

from tree_sitter import Node

from trailmark.models.edges import CodeEdge, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit, NodeKind
from trailmark.parsers._common import add_contains_edge, make_location, node_text

_READ = (EdgeKind.READS,)
_WRITE = (EdgeKind.WRITES,)
_READ_WRITE = (EdgeKind.READS, EdgeKind.WRITES)
_SKIP = frozenset({"type_name", "parameter", "assembly_statement"})


@dataclass(frozen=True)
class StorageTypes:
    """AST type declarations shared by access collectors within a contract."""

    state_types: dict[str, Node]
    struct_fields: dict[str, dict[str, Node]]


def extract_state_variables(
    body: Node,
    file_path: str,
    contract_id: str,
    graph: CodeGraph,
) -> dict[str, CodeUnit]:
    """Materialize persistent storage declarations before visiting functions."""
    states: dict[str, CodeUnit] = {}
    for child in body.named_children:
        if child.type != "state_variable_declaration":
            continue
        if any(c.type in {"constant", "immutable"} for c in child.children):
            continue
        location = child.child_by_field_name("location")
        if location is not None and node_text(location) == "transient":
            continue
        name = child.child_by_field_name("name")
        declared_type = child.child_by_field_name("type")
        if name is None or declared_type is None:
            continue
        state_name = node_text(name)
        state_id = f"{contract_id}.{state_name}"
        unit = CodeUnit(
            id=state_id,
            name=state_name,
            kind=NodeKind.STATE_VARIABLE,
            location=make_location(child, file_path),
            attributes=(("declared_type", node_text(declared_type)),),
        )
        states[state_name] = unit
        graph.nodes[state_id] = unit
        add_contains_edge(graph, contract_id, state_id)
    return states


def add_state_access_edges(
    function: Node,
    source_id: str,
    file_path: str,
    states: dict[str, CodeUnit],
    graph: CodeGraph,
    *,
    storage_types: StorageTypes | None = None,
) -> None:
    """Record direct accesses in a function, constructor, or modifier."""
    if not states:
        return
    shadowed: set[str] = set()
    _bind_parameters(function, shadowed)
    returns = function.child_by_field_name("return_type")
    if returns is not None:
        _bind_parameters(returns, shadowed)
    if storage_types is None:
        body = function.parent
        while body is not None and body.type != "contract_body":
            body = body.parent
        storage_types = collect_storage_types(body) if body is not None else StorageTypes({}, {})
    collector = _AccessCollector(source_id, file_path, states, graph, storage_types)
    for child in function.named_children:
        if child.type == "modifier_invocation":
            for argument in child.named_children:
                if argument.type == "call_argument":
                    collector.visit(argument, shadowed)
    body = function.child_by_field_name("body")
    if body is not None:
        collector.visit(body, shadowed)


def _bind_parameters(node: Node, shadowed: set[str]) -> None:
    for child in node.named_children:
        if child.type == "parameter":
            name = child.child_by_field_name("name")
            if name is not None:
                shadowed.add(node_text(name))


def _unwrap(node: Node) -> Node:
    while node.type == "expression" and len(node.named_children) == 1:
        node = node.named_children[0]
    return node


class _AccessCollector:
    def __init__(
        self,
        source_id: str,
        file_path: str,
        states: dict[str, CodeUnit],
        graph: CodeGraph,
        storage_types: StorageTypes,
    ) -> None:
        self.source_id = source_id
        self.file_path = file_path
        self.states = states
        self.graph = graph
        self.state_types = storage_types.state_types
        self.struct_fields = storage_types.struct_fields

    def visit(
        self,
        node: Node,
        shadowed: set[str],
        kinds: tuple[EdgeKind, ...] = _READ,
    ) -> None:
        """Walk in source order without depending on Python's recursion limit."""
        stack: list[_Visit | _Bind] = [_Visit(node, shadowed, kinds)]
        while stack:
            frame = stack.pop()
            if isinstance(frame, _Bind):
                self._bind_declaration(frame.node, frame.shadowed)
            else:
                stack.extend(reversed(self._children(frame.node, frame.shadowed, frame.kinds)))

    def _children(
        self,
        node: Node,
        shadowed: set[str],
        kinds: tuple[EdgeKind, ...],
    ) -> list[_Visit | _Bind]:
        if node.type in _SKIP:
            return []
        if node.type == "identifier":
            name = node_text(node)
            state = self.states.get(name)
            if state is not None and name not in shadowed:
                for kind in kinds:
                    self.graph.edges.append(
                        CodeEdge(
                            source_id=self.source_id,
                            target_id=state.id,
                            kind=kind,
                            location=make_location(node, self.file_path),
                        )
                    )
            return []
        if node.type in {"function_body", "block_statement", "for_statement", "catch_clause"}:
            shadowed = shadowed.copy()
            _bind_parameters(node, shadowed)
        if node.type == "catch_clause":
            body = node.child_by_field_name("body")
            return [_Visit(body, shadowed)] if body is not None else []
        if node.type == "try_statement":
            children: list[_Visit | _Bind] = []
            attempt = node.child_by_field_name("attempt")
            if attempt is not None:
                children.append(_Visit(attempt, shadowed))
            success_scope = shadowed.copy()
            _bind_parameters(node, success_scope)
            body = node.child_by_field_name("body")
            if body is not None:
                children.append(_Visit(body, success_scope))
            for child in node.named_children:
                if child.type == "catch_clause":
                    children.append(_Visit(child, shadowed))
            return children
        if node.type == "variable_declaration_statement":
            value = node.child_by_field_name("value")
            # The initializer sees the enclosing scope, including for tuple and
            # for-loop declarations. Activate the new bindings only afterward.
            return (
                [_Visit(value, shadowed), _Bind(node, shadowed)]
                if value is not None
                else [_Bind(node, shadowed)]
            )
        if node.type in {"assignment_expression", "augmented_assignment_expression"}:
            children = []
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            if left is not None:
                children.append(
                    _Visit(
                        left,
                        shadowed,
                        _WRITE if node.type == "assignment_expression" else _READ_WRITE,
                    )
                )
            if right is not None:
                children.append(_Visit(right, shadowed))
            return children
        if node.type in {"update_expression", "unary_expression"}:
            argument = node.child_by_field_name("argument")
            if argument is not None:
                if node.type == "update_expression":
                    kinds = _READ_WRITE
                elif any(c.type == "delete" for c in node.children):
                    kinds = _WRITE
                else:
                    kinds = _READ
                return [_Visit(argument, shadowed, kinds)]
            return []
        if node.type == "member_expression":
            obj = node.child_by_field_name("object")
            return [_Visit(obj, shadowed, kinds)] if obj is not None else []
        if node.type == "array_access":
            children = []
            base = node.child_by_field_name("base")
            index = node.child_by_field_name("index")
            if base is not None:
                children.append(_Visit(base, shadowed, kinds))
            if index is not None:
                children.append(_Visit(index, shadowed))
            return children
        if node.type in {"call_struct_argument", "struct_field_assignment"}:
            value = node.child_by_field_name("value")
            return [_Visit(value, shadowed)] if value is not None else []
        if node.type == "call_expression":
            return self._call_children(node, shadowed)
        return [_Visit(child, shadowed, kinds) for child in node.named_children]

    def _bind_declaration(self, node: Node, shadowed: set[str]) -> None:
        for child in node.named_children:
            declarations = (
                child.named_children if child.type == "variable_declaration_tuple" else [child]
            )
            for declaration in declarations:
                if declaration.type == "variable_declaration":
                    name = declaration.child_by_field_name("name")
                    if name is not None:
                        shadowed.add(node_text(name))

    def _call_children(self, node: Node, shadowed: set[str]) -> list[_Visit | _Bind]:
        children: list[_Visit | _Bind] = []
        arguments = [child for child in node.named_children if child.type == "call_argument"]
        function = node.child_by_field_name("function")
        if function is not None:
            receiver = self._array_mutation_receiver(_unwrap(function), shadowed, len(arguments))
            if receiver is not None:
                children.append(_Visit(receiver, shadowed, _READ_WRITE))
            else:
                children.append(_Visit(function, shadowed))
        children.extend(_Visit(argument, shadowed) for argument in arguments)
        return children

    def _array_mutation_receiver(
        self, target: Node, shadowed: set[str], argument_count: int
    ) -> Node | None:
        obj = target.child_by_field_name("object")
        prop = target.child_by_field_name("property")
        if obj is None or prop is None or node_text(prop) not in {"push", "pop"}:
            return None
        if argument_count > (1 if node_text(prop) == "push" else 0):
            return None
        declared_type = self._receiver_type(obj, shadowed)
        if declared_type is None or not _is_dynamic_array(declared_type):
            return None
        return obj

    def _receiver_type(self, node: Node, shadowed: set[str]) -> Node | None:
        """Resolve only declared storage roots, array elements and struct fields."""
        steps: list[tuple[str, str]] = []
        node = _unwrap(node)
        while node.type in {"member_expression", "array_access"}:
            if node.type == "member_expression":
                prop = node.child_by_field_name("property")
                parent = node.child_by_field_name("object")
                if prop is None:
                    return None
                steps.append(("member", node_text(prop)))
            else:
                parent = node.child_by_field_name("base")
                steps.append(("index", ""))
            if parent is None:
                return None
            node = _unwrap(parent)
        name = node_text(node)
        if node.type != "identifier" or name not in self.states or name in shadowed:
            return None
        declared_type = self.state_types.get(name)
        for operation, member in reversed(steps):
            if declared_type is None:
                return None
            if operation == "member":
                declared_type = self._struct_member_type(declared_type, member)
            else:
                declared_type = _element_type(declared_type)
        return declared_type

    def _struct_member_type(self, declared_type: Node, member: str) -> Node | None:
        type_name = node_text(declared_type)
        owner = declared_type.parent
        while owner is not None:
            if owner.type in {
                "contract_declaration",
                "interface_declaration",
                "library_declaration",
            }:
                name = owner.child_by_field_name("name")
                if name is not None:
                    fields = self.struct_fields.get(f"{node_text(name)}.{type_name}")
                    if fields is not None:
                        return fields.get(member)
                break
            owner = owner.parent
        return self.struct_fields.get(type_name, {}).get(member)


@dataclass(frozen=True)
class _Visit:
    node: Node
    shadowed: set[str]
    kinds: tuple[EdgeKind, ...] = _READ


@dataclass(frozen=True)
class _Bind:
    node: Node
    shadowed: set[str]


def _element_type(declared_type: Node) -> Node | None:
    value = declared_type.child_by_field_name("value_type")
    if value is not None:
        return value
    if any(child.type == "[" for child in declared_type.children):
        return next(
            (child for child in declared_type.named_children if child.type == "type_name"), None
        )
    return None


def _is_dynamic_array(declared_type: Node) -> bool:
    if node_text(declared_type) == "bytes":
        return True
    return any(child.type == "[" for child in declared_type.children) and not any(
        child.type == "expression" for child in declared_type.named_children
    )


def collect_storage_types(body: Node) -> StorageTypes:
    """Read same-contract state types and file-local struct member declarations."""
    root = body
    while root.parent is not None:
        root = root.parent
    state_types: dict[str, Node] = {}
    for declaration in body.named_children:
        if declaration.type == "state_variable_declaration":
            name = declaration.child_by_field_name("name")
            declared_type = declaration.child_by_field_name("type")
            if name is not None and declared_type is not None:
                state_types[node_text(name)] = declared_type
    structs: dict[str, dict[str, Node]] = {}
    for declaration in root.named_children:
        if declaration.type == "struct_declaration":
            _add_struct_fields(declaration, "", structs)
        elif declaration.type in {
            "contract_declaration",
            "interface_declaration",
            "library_declaration",
        }:
            owner = declaration.child_by_field_name("name")
            owner_body = declaration.child_by_field_name("body")
            if owner is None or owner_body is None:
                continue
            for member in owner_body.named_children:
                if member.type == "struct_declaration":
                    _add_struct_fields(member, f"{node_text(owner)}.", structs)
    return StorageTypes(state_types, structs)


def _add_struct_fields(declaration: Node, prefix: str, structs: dict[str, dict[str, Node]]) -> None:
    name = declaration.child_by_field_name("name")
    body = declaration.child_by_field_name("body")
    if name is None or body is None:
        return
    fields: dict[str, Node] = {}
    for member in body.named_children:
        field_name = member.child_by_field_name("name")
        field_type = member.child_by_field_name("type")
        if field_name is not None and field_type is not None:
            fields[node_text(field_name)] = field_type
    structs[f"{prefix}{node_text(name)}"] = fields
