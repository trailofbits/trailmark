"""Iterative F* binding extraction and source-local lexical intervals."""

from dataclasses import dataclass

from tree_sitter import Node

from trailmark.parsers._common import node_text
from trailmark.parsers._formal import Scope, walk

OPAQUE = frozenset(
    {
        "language_content",
        "extension_content",
        "quotation_expression",
        "splice_declaration",
        "quoted_pattern",
        "tactic_expression",
    }
)
SKIP = OPAQUE | frozenset(
    {
        "line_comment",
        "block_comment",
        "string",
        "character",
        "attribute",
        "smt_trigger",
        "variable_pattern",
        "operator_identifier",
    }
)


def bindings(root: Node) -> tuple[str, ...]:
    result, pending = [], [root]
    while pending:
        node = pending.pop()
        if node.type in {"variable_pattern", "binder", "refinement_type"}:
            result.extend(node_text(n) for n in node.children_by_field_name("name"))
        elif node.type == "ascribed_pattern":
            pattern = node.child_by_field_name("pattern")
            if pattern is not None:
                pending.append(pattern)
        elif node.type == "record_pattern_field" and node.child_by_field_name("pattern") is None:
            field = node.child_by_field_name("field")
            if field is not None:
                result.append(node_text(field))
        else:
            pending.extend(reversed(node.named_children))
    return tuple(dict.fromkeys(result))


@dataclass(frozen=True)
class Binding:
    name: Node
    parameters: tuple[Node, ...]
    types: tuple[Node, ...]
    values: tuple[Node, ...]


def groups(node: Node) -> list[Binding]:
    result = []
    names = node.children_by_field_name("name")
    for index, name in enumerate(names):
        end = names[index + 1].start_byte if index + 1 < len(names) else node.end_byte
        fields = [
            tuple(
                p for p in node.children_by_field_name(field) if name.end_byte <= p.start_byte < end
            )
            for field in ("parameters", "type", "value")
        ]
        result.append(Binding(name, *fields))
    return result


def _parameters(node: Node) -> list[Scope]:
    result = []
    slots = groups(node)
    if not slots:
        body = node.child_by_field_name("body")
        if body is None:
            return result
        slots = [Binding(node, tuple(node.children_by_field_name("parameters")), (), (body,))]
    for slot in slots:
        end = max((n.end_byte for n in (*slot.types, *slot.values)), default=node.end_byte)
        for parameter in slot.parameters:
            result.append(Scope(frozenset(bindings(parameter)), parameter.end_byte, end))
    return result


def _local(node: Node) -> list[Scope]:
    result = []
    names = frozenset(node_text(n) for n in node.children_by_field_name("name")) | frozenset(
        name for p in node.children_by_field_name("pattern") for name in bindings(p)
    )
    body = node.child_by_field_name("body")
    if body:
        result.append(Scope(names, body.start_byte, body.end_byte))
    if any(c.type == "rec" for c in node.children):
        result.extend(
            Scope(names, value.start_byte, value.end_byte)
            for value in node.children_by_field_name("value")
        )
    result.extend(
        Scope(names, name.start_byte, name.end_byte) for name in node.children_by_field_name("name")
    )
    return result


def _match(node: Node) -> list[Scope]:
    pattern = node.child_by_field_name("pattern")
    if pattern is None:
        return []
    return [Scope(frozenset(bindings(pattern)), pattern.end_byte, node.end_byte)]


def _dependent(node: Node) -> list[Scope]:
    result = []
    condition = node.child_by_field_name("condition")
    names = frozenset(bindings(node))
    if condition:
        result.append(Scope(names, condition.start_byte, condition.end_byte))
    result.extend(
        Scope(names, n.start_byte, n.end_byte) for n in node.children_by_field_name("name")
    )
    return result


def _arrow(node: Node) -> list[Scope]:
    left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
    if left is None or right is None:
        return []
    return [Scope(frozenset(bindings(left)), right.start_byte, right.end_byte)]


def _arguments(node: Node) -> list[Scope]:
    head = node.child_by_field_name("function")
    if head is None:
        return []
    return [
        Scope(
            frozenset(),
            arg.start_byte,
            arg.end_byte,
            context="argument",
            callee=head.start_byte,
            argument=index,
        )
        for index, arg in enumerate(node.children_by_field_name("argument"))
    ]


def _open(node: Node) -> list[Scope]:
    body = node.child_by_field_name("body")
    prefix = node.child_by_field_name("name") or node.child_by_field_name("value")
    if body is None or prefix is None:
        return []
    return [Scope(frozenset(), body.start_byte, body.end_byte, (node_text(prefix),))]


_HANDLERS = {
    "let_expression": _local,
    "match_branch": _match,
    "arrow_type": _arrow,
    "application_expression": _arguments,
    "let_open_expression": _open,
    "module_open_expression": _open,
    **dict.fromkeys(("refinement_type", "binder", "ascribed_pattern"), _dependent),
}


def scopes(root: Node) -> tuple[Scope, ...]:
    result = []
    for node in walk(root, OPAQUE):
        if node.children_by_field_name("parameters"):
            result.extend(_parameters(node))
        handler = _HANDLERS.get(node.type)
        if handler:
            result.extend(handler(node))
    return tuple(result)
