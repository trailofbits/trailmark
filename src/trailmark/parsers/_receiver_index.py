"""Parse-local indexes for direct TypeScript receiver construction."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from tree_sitter import Node

from trailmark.parsers._walk import owned_syntax

_FUNCTIONS = frozenset(
    {
        "function_declaration",
        "generator_function_declaration",
        "arrow_function",
        "function",
        "function_expression",
        "generator_function",
        "method_definition",
    }
)
_CLASSES = frozenset({"class", "class_declaration", "abstract_class_declaration"})
_BOUNDARIES = _FUNCTIONS | _CLASSES
_ASSIGNMENTS = frozenset({"variable_declarator", "assignment_expression"})
_CONDITIONAL = frozenset(
    {
        "if_statement",
        "switch_case",
        "for_statement",
        "for_in_statement",
        "while_statement",
        "ternary_expression",
        "catch_clause",
    }
)


@dataclass(frozen=True)
class _Assignment:
    position: int
    concrete: str | None
    blocks: frozenset[int]
    conditional: frozenset[int]


@dataclass
class _Receivers:
    scopes: dict[int, Node | None] = field(default_factory=dict)
    assignments: dict[int, dict[str, list[_Assignment]]] = field(default_factory=dict)
    contexts: dict[tuple[int, str], frozenset[int]] = field(default_factory=dict)
    results: dict[tuple[int, str, int, frozenset[int]], str | None] = field(default_factory=dict)

    def scope(self, node: Node) -> Node | None:
        pending = []
        current = node.parent
        while current is not None and current.id not in self.scopes:
            if current.type in _BOUNDARIES:
                self.scopes[current.id] = current if current.type in _FUNCTIONS else None
                break
            pending.append(current.id)
            current = current.parent
        result = self.scopes.get(current.id) if current is not None else None
        for key in pending:
            self.scopes[key] = result
        return result

    def latest(self, scope: Node, call: Node, receiver: str) -> str | None:
        if scope.id not in self.assignments:
            self.assignments[scope.id] = _index_assignments(scope)
        candidates = self.assignments[scope.id].get(receiver, [])
        position = bisect_left(candidates, call.start_byte, key=lambda item: item.position)
        context = (scope.id, receiver)
        if context not in self.contexts:
            self.contexts[context] = frozenset().union(
                *(item.blocks | item.conditional for item in candidates)
            )
        ancestors = _ancestors(call, scope) & self.contexts[context]
        key = (*context, position, ancestors)
        if key not in self.results:
            self.results[key] = _latest_visible(candidates, position, ancestors)
        return self.results[key]


def _latest_visible(
    candidates: list[_Assignment], position: int, ancestors: frozenset[int]
) -> str | None:
    for index in range(position - 1, -1, -1):
        item = candidates[index]
        if item.blocks <= ancestors:
            return item.concrete if item.conditional <= ancestors else None
    return None


def _ancestors(node: Node, scope: Node) -> frozenset[int]:
    result = set()
    parent = node.parent
    while parent is not None and parent != scope:
        result.add(parent.id)
        parent = parent.parent
    return frozenset(result)


def _assignment(node: Node, scope: Node) -> tuple[str, _Assignment] | None:
    variable = node.type == "variable_declarator"
    name = node.child_by_field_name("name" if variable else "left")
    value = node.child_by_field_name("value" if variable else "right")
    if name is None or name.type != "identifier" or value is None:
        return None
    concrete = value.child_by_field_name("constructor") if value.type == "new_expression" else None
    blocks, conditional = set(), set()
    child, parent = node, node.parent
    while parent is not None and parent != scope:
        if variable and parent.type == "statement_block":
            blocks.add(parent.id)
        if parent.type in _CONDITIONAL:
            conditional.add(
                child.id if parent.type in {"if_statement", "ternary_expression"} else parent.id
            )
        child, parent = parent, parent.parent
    return (name.text or b"").decode(), _Assignment(
        node.start_byte,
        (concrete.text or b"").decode()
        if concrete is not None and concrete.type == "identifier"
        else None,
        frozenset(blocks),
        frozenset(conditional),
    )


def _index_assignments(scope: Node) -> dict[str, list[_Assignment]]:
    result: dict[str, list[_Assignment]] = {}
    body = scope.child_by_field_name("body")
    if body is not None:
        for node in owned_syntax(body, _ASSIGNMENTS, boundaries=_BOUNDARIES):
            item = _assignment(node, scope)
            if item is not None:
                name, assignment = item
                result.setdefault(name, []).append(assignment)
    return result


_CURRENT: ContextVar[_Receivers | None] = ContextVar("_RECEIVERS", default=None)


@contextmanager
def receiver_scope() -> Generator[None]:
    """Discard syntax and receiver indexes when the file parse ends."""
    token = _CURRENT.set(_Receivers())
    try:
        yield
    finally:
        _CURRENT.reset(token)


def concrete_receiver(call: Node, receiver: str) -> str | None:
    """Resolve the latest visible direct construction without rescanning a body."""
    index = _CURRENT.get() or _Receivers()
    scope = index.scope(call)
    return index.latest(scope, call, receiver) if scope is not None else None
