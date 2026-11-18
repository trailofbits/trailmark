"""Iterative ProVerif binders and branch-specific lexical intervals."""

from tree_sitter import Node

from trailmark.parsers._common import node_text
from trailmark.parsers._formal import Scope, walk

OPAQUE = frozenset({"decl_cryptoverif", "process_get", "term_get", "process_foreach", "clauses"})
SKIP = OPAQUE | frozenset({"comment", "line_comment", "options"})


def pattern_names(root: Node) -> frozenset[str]:
    names, pending = set(), [root]
    while pending:
        node = pending.pop()
        if node.children and node.children[0].type == "=":
            continue
        if not any(c.type == "(" for c in node.children):
            names.update(node_text(c) for c in node.named_children if c.type == "ident")
        pending.extend(c for c in node.named_children if c.type == "pattern")
    return frozenset(names)


def typed_names(node: Node) -> frozenset[str]:
    return frozenset(node_text(c) for c in node.named_children if c.type == "ident")


def _following(node: Node, token: str) -> Node | None:
    found = False
    for child in node.children:
        if found and child.is_named and child.type not in {"comment", "line_comment", "options"}:
            return child
        found |= child.type == token
    return None


def _continuation(node: Node) -> Node | None:
    current = node
    while current.type != "process" and current.parent:
        body = _following(current, ";")
        if body:
            return body
        current = current.parent
    return _following(current, ";")


def _declarations(root: Node, names: frozenset[str]) -> list[Scope]:
    # Only binding identifiers; equality patterns and types retain their references.
    return [
        Scope(frozenset({node_text(n)}), n.start_byte, n.end_byte)
        for n in walk(root, frozenset({"pterm", "typeid"}))
        if n.type == "ident" and node_text(n) in names
    ]


def _let(node: Node) -> list[Scope]:
    pattern = next(
        (n for n in node.named_children if n.type in {"pattern", "typedecl", "ident"}), None
    )
    if pattern is None:
        return []
    names = (
        pattern_names(pattern)
        if pattern.type == "pattern"
        else typed_names(pattern)
        if pattern.type == "typedecl"
        else frozenset({node_text(pattern)})
    )
    result = (
        _declarations(pattern, names)
        if pattern.type != "ident"
        else [Scope(names, pattern.start_byte, pattern.end_byte)]
    )
    body = _following(node, "in")
    if body:
        result.append(Scope(names, body.start_byte, body.end_byte))
    condition = _following(node, "suchthat")
    if condition:
        result.append(Scope(names, condition.start_byte, condition.end_byte))
    return result


def _fresh(node: Node) -> list[Scope]:
    name = next((n for n in node.named_children if n.type == "ident"), None)
    if name is None:
        return []
    names = frozenset({node_text(name)})
    result = [Scope(names, name.start_byte, name.end_byte)]
    body = _following(node, ";") or _continuation(node)
    if body:
        result.append(Scope(names, body.start_byte, body.end_byte))
    return result


def _input(node: Node) -> list[Scope]:
    result = []
    body = _continuation(node)
    for pattern in (n for n in node.named_children if n.type == "pattern"):
        names = pattern_names(pattern)
        result.extend(_declarations(pattern, names))
        if body:
            result.append(Scope(names, body.start_byte, body.end_byte))
    return result


def _typed(node: Node) -> list[Scope]:
    parent = node.parent
    if (
        parent is None
        or parent.type == "process_let"
        or (
            parent.type in {"pterm", "gterm"}
            and parent.children
            and parent.children[0].type == "let"
        )
    ):
        return []
    return [Scope(typed_names(node), parent.start_byte, parent.end_byte)]


def scopes(root: Node) -> tuple[Scope, ...]:
    result = []
    handlers = {
        "process_new": _fresh,
        "process_input": _input,
        "process_let": _let,
        "typedecl": _typed,
        "failtypedecl": _typed,
    }
    for node in walk(root, SKIP):
        handler = handlers.get(node.type)
        if handler:
            result.extend(handler(node))
        elif (
            node.type in {"pterm", "gterm", "nounifdecl"}
            and node.children
            and node.children[0].type == "let"
        ):
            result.extend(_let(node))
        elif node.type in {"pterm", "process_item"} and any(
            c.type in {"new", "<-R", "<-", "<="} for c in node.children
        ):
            result.extend(_fresh(node))
    return tuple(result)
