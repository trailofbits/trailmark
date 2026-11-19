"""Tamarin symbol spaces and lexical term binders."""

from tree_sitter import Node

from trailmark.parsers._common import node_text
from trailmark.parsers._formal import Scope, walk


def symbol(node: Node) -> Node | None:
    return next((c for c in node.named_children if c.type in {"ident", "builtin_facts"}), None)


def fact_name(node: Node) -> str:
    name = symbol(node)
    arity = sum(c.type == "," for c in node.children) + int(
        any(c.type == "term" for c in node.named_children)
    )
    return f"{node_text(name) if name else '?'}/{arity}"


def reference(node: Node, namespace: str) -> str | None:
    if node.type == "builtin_operator":
        return f"{namespace}.function.{node_text(node)}"
    parent = node.parent
    if parent is None or node != symbol(parent):
        return None
    if parent.type == "fact":
        return f"{namespace}.fact.{fact_name(parent)}"
    if parent.type in {"app", "predicate_reference"}:
        role = "predicate" if parent.type == "predicate_reference" else "function"
        return f"{namespace}.{role}.{node_text(node)}"
    # Only declared nullary functions receive bare-name aliases. Sorted, quoted,
    # indexed and fresh/public variables are distinct from these constant terms.
    if parent.type in {"nonnode_var", "msg_var"} and node_text(parent) == node_text(node):
        return node_text(node)
    return None


def _rule_contexts(root: Node) -> list[Scope]:
    result = []
    if root.type == "simple_rule":
        context = "premise"
        for child in root.children:
            if child.type == "--[":
                context = "action"
            elif child.type in {"-->", "]->"}:
                context = "conclusion"
            elif child.type == "facts":
                result.append(Scope(frozenset(), child.start_byte, child.end_byte, context=context))
    return result


def scopes(root: Node, skip: frozenset[str]) -> tuple[Scope, ...]:
    result = []
    parameters = root.children_by_field_name("parameter")
    if parameters:
        result.append(
            Scope(frozenset(node_text(n) for n in parameters), root.start_byte, root.end_byte)
        )
    result.extend(_rule_contexts(root))
    for node in walk(root, skip):
        if node.type == "atom" and any(c.type == "quantifier" for c in node.named_children):
            names = frozenset(
                node_text(n)
                for child in node.named_children
                if child.type == "lvar"
                for n in walk(child)
                if n.type == "ident"
            )
            result.append(Scope(names, node.start_byte, node.end_byte))
        elif node.type == "let_block" and node.parent is not None:
            for binding in node.named_children:
                name = binding.child_by_field_name("name")
                value = binding.child_by_field_name("value")
                if name is None or value is None:
                    continue
                bound = frozenset({node_text(name)})
                result.extend(
                    (
                        Scope(bound, name.start_byte, name.end_byte),
                        Scope(bound, value.end_byte, node.parent.end_byte),
                    )
                )
    return tuple(result)


def conditional(node: Node) -> bool:
    while node.parent is not None:
        node = node.parent
        if node.type == "conditional":
            return True
    return False
