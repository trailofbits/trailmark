"""Direct, declaration-level Solidity storage dependencies.

This pass resolves names within one contract. It does not infer storage aliases,
inheritance, library effects, or accesses through assembly and external calls.
"""

from __future__ import annotations

from tree_sitter import Node

from trailmark.models.edges import CodeEdge, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit, NodeKind
from trailmark.parsers._common import add_contains_edge, make_location, node_text

_READ = (EdgeKind.READS,)
_WRITE = (EdgeKind.WRITES,)
_READ_WRITE = (EdgeKind.READS, EdgeKind.WRITES)
_SKIP = frozenset({"type_name", "parameter", "assembly_statement"})


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
) -> None:
    """Record direct accesses in a function, constructor, or modifier."""
    if not states:
        return
    shadowed: set[str] = set()
    _bind_parameters(function, shadowed)
    returns = function.child_by_field_name("return_type")
    if returns is not None:
        _bind_parameters(returns, shadowed)
    collector = _AccessCollector(source_id, file_path, states, graph)
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
    ) -> None:
        self.source_id = source_id
        self.file_path = file_path
        self.states = states
        self.graph = graph

    def visit(
        self,
        node: Node,
        shadowed: set[str],
        kinds: tuple[EdgeKind, ...] = _READ,
    ) -> None:
        if node.type in _SKIP:
            return
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
            return
        if node.type in {"function_body", "block_statement", "for_statement", "catch_clause"}:
            shadowed = shadowed.copy()
            _bind_parameters(node, shadowed)
        if node.type == "catch_clause":
            body = node.child_by_field_name("body")
            if body is not None:
                self.visit(body, shadowed)
            return
        if node.type == "try_statement":
            attempt = node.child_by_field_name("attempt")
            if attempt is not None:
                self.visit(attempt, shadowed)
            success_scope = shadowed.copy()
            _bind_parameters(node, success_scope)
            body = node.child_by_field_name("body")
            if body is not None:
                self.visit(body, success_scope)
            for child in node.named_children:
                if child.type == "catch_clause":
                    self.visit(child, shadowed)
            return
        if node.type == "variable_declaration_statement":
            for child in node.named_children:
                if child.type == "variable_declaration_tuple":
                    for declaration in child.named_children:
                        self._bind_local(declaration, shadowed)
                elif child.type == "variable_declaration":
                    self._bind_local(child, shadowed)
            value = node.child_by_field_name("value")
            if value is not None:
                self.visit(value, shadowed)
            return
        if node.type in {"assignment_expression", "augmented_assignment_expression"}:
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            if left is not None:
                self.visit(
                    left,
                    shadowed,
                    _WRITE if node.type == "assignment_expression" else _READ_WRITE,
                )
            if right is not None:
                self.visit(right, shadowed)
            return
        if node.type in {"update_expression", "unary_expression"}:
            argument = node.child_by_field_name("argument")
            if argument is not None:
                if node.type == "update_expression":
                    kinds = _READ_WRITE
                elif any(c.type == "delete" for c in node.children):
                    kinds = _WRITE
                else:
                    kinds = _READ
                self.visit(argument, shadowed, kinds)
            return
        if node.type == "member_expression":
            obj = node.child_by_field_name("object")
            if obj is not None:
                self.visit(obj, shadowed, kinds)
            return
        if node.type == "array_access":
            base = node.child_by_field_name("base")
            index = node.child_by_field_name("index")
            if base is not None:
                self.visit(base, shadowed, kinds)
            if index is not None:
                self.visit(index, shadowed)
            return
        if node.type == "call_struct_argument":
            value = node.child_by_field_name("value")
            if value is not None:
                self.visit(value, shadowed)
            return
        if node.type == "call_expression":
            self._visit_call(node, shadowed)
            return
        for child in node.named_children:
            self.visit(child, shadowed, kinds)

    def _bind_local(self, node: Node, shadowed: set[str]) -> None:
        name = node.child_by_field_name("name")
        if name is not None:
            shadowed.add(node_text(name))

    def _visit_call(self, node: Node, shadowed: set[str]) -> None:
        function = node.child_by_field_name("function")
        if function is not None:
            receiver = self._array_mutation_receiver(_unwrap(function), shadowed)
            if receiver is not None:
                self.visit(receiver, shadowed, _READ_WRITE)
            else:
                self.visit(function, shadowed)
        for child in node.named_children:
            if child.type == "call_argument":
                self.visit(child, shadowed)

    def _array_mutation_receiver(self, target: Node, shadowed: set[str]) -> Node | None:
        obj = target.child_by_field_name("object")
        prop = target.child_by_field_name("property")
        if obj is None or prop is None or node_text(prop) not in {"push", "pop"}:
            return None
        receiver = _unwrap(obj)
        name = node_text(receiver)
        state = self.states.get(name)
        if state is None or name in shadowed:
            return None
        if not str(dict(state.attributes)["declared_type"]).endswith("]"):
            return None
        return obj
