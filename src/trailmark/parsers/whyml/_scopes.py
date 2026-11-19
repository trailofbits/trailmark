"""Lexical and erased regions for the bounded WhyML grammar."""

from tree_sitter import Node

from trailmark.models.nodes import Parameter, TypeRef
from trailmark.parsers._common import node_text
from trailmark.parsers._formal import Scope, walk

SKIP = frozenset({"comment", "string", "attribute", "type_variable"})


def parameters(node: Node) -> tuple[Parameter, ...]:
    result = []
    for part in node.children_by_field_name("parameter"):
        names = (part,) if part.type == "identifier" else part.children_by_field_name("name")
        if part.type == "unit":
            result.append(Parameter("()"))
        typed = part.child_by_field_name("type")
        result.extend(
            Parameter(node_text(n), TypeRef(node_text(typed)) if typed else None) for n in names
        )
    return tuple(result)


def _parameters(node: Node) -> list[Scope]:
    target = node.child_by_field_name("value") if node.type == "let_expression" else node
    if target is None:
        return []
    result = []
    for parameter in node.children_by_field_name("parameter"):
        names = (
            (parameter,)
            if parameter.type == "identifier"
            else parameter.children_by_field_name("name")
        )
        bound = frozenset(node_text(n) for n in names)
        result.append(Scope(bound, parameter.end_byte, target.end_byte))
        result.extend(Scope(bound, n.start_byte, n.end_byte) for n in names)
    return result


def _local(node: Node) -> list[Scope]:
    names = frozenset(node_text(n) for n in node.children_by_field_name("name"))
    result = [Scope(names, n.start_byte, n.end_byte) for n in node.children_by_field_name("name")]
    body, value = node.child_by_field_name("body"), node.child_by_field_name("value")
    if body:
        result.append(Scope(names, body.start_byte, body.end_byte))
    tokens = {c.type for c in node.children}
    if value:
        if "rec" in tokens:
            result.append(Scope(names, value.start_byte, value.end_byte))
        if "ghost" in tokens:
            result.append(Scope(frozenset(), value.start_byte, value.end_byte, context="proof"))
    return result


def _for(node: Node) -> list[Scope]:
    body = node.child_by_field_name("body")
    regions = [n for n in node.named_children if n.type == "contract" or n == body]
    names = frozenset(node_text(n) for n in node.children_by_field_name("name"))
    return [
        Scope(names, n.start_byte, n.end_byte)
        for n in (*node.children_by_field_name("name"), *regions)
    ]


def _pattern(node: Node) -> list[Scope]:
    pattern = node.child_by_field_name("pattern")
    if pattern is None:
        return []
    names = frozenset(node_text(n) for p in walk(pattern) for n in p.children_by_field_name("name"))
    return [Scope(names, pattern.start_byte, node.end_byte)]


def _quantifier(node: Node) -> list[Scope]:
    body = node.child_by_field_name("body")
    if body is None:
        return []
    names = frozenset(
        node_text(n)
        for p in node.named_children
        if p.type == "quantifier_binder"
        for n in p.children_by_field_name("name")
    )
    return [Scope(names, node.start_byte, body.end_byte)]


def _proof(node: Node) -> list[Scope]:
    return [
        Scope(
            frozenset({"result"}),
            node.start_byte,
            node.end_byte,
            context="contract" if node.type == "contract" else "proof",
        )
    ]


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
            argument=i,
        )
        for i, arg in enumerate(node.children_by_field_name("argument"))
    ]


_HANDLERS = {
    "let_expression": _local,
    "for_expression": _for,
    "match_case": _pattern,
    "quantifier": _quantifier,
    "application": _arguments,
    "label_expression": lambda n: [
        Scope(
            frozenset(node_text(c) for c in n.named_children if c.type == "constructor_identifier"),
            n.start_byte,
            n.end_byte,
        )
    ],
    **dict.fromkeys(("contract", "assert_expression", "ghost_expression"), _proof),
}


def scopes(root: Node) -> tuple[Scope, ...]:
    result = []
    for node in walk(root, SKIP):
        if node.children_by_field_name("parameter"):
            result.extend(_parameters(node))
        handler = _HANDLERS.get(node.type)
        if handler:
            result.extend(handler(node))
    return tuple(result)
