"""Dafny binders and evaluation regions, using explicit grammar structure."""

from tree_sitter import Node

from trailmark.models.nodes import Parameter, TypeRef
from trailmark.parsers._common import node_text
from trailmark.parsers._formal import Scope, walk

SKIP = frozenset({"line_comment", "block_comment", "string_literal", "char_literal", "attribute"})
_SPEC = frozenset(
    {
        "assert_statement",
        "assume_statement",
        "calc_statement",
        "reveal_statement",
        "assert_expression",
        "assume_expression",
        "calc_expression",
        "fn_assert_expression",
        "fn_assume_expression",
        "fn_calc_expression",
    }
)


def _formals(node: Node) -> list[Node]:
    if node.type == "datatype_ctor":
        return [n for n in node.named_children if n.type == "datatype_formal"]
    formals = next((n for n in node.named_children if n.type == "formals"), None)
    return [n for n in formals.named_children if n.type == "formal"] if formals else []


def parameters(node: Node) -> tuple[Parameter, ...]:
    return tuple(_parameter(formal) for formal in _formals(node))


def _parameter(formal: Node) -> Parameter:
    name = formal.child_by_field_name("name")
    typed = next((n for n in formal.named_children if n != name and n.type not in SKIP), None)
    default = next(
        (n for n in formal.named_children if n != name and n != typed and n.type not in SKIP), None
    )
    return Parameter(
        node_text(name) if name else "_",
        TypeRef(node_text(typed)) if typed else None,
        node_text(default) if default else None,
    )


def ghost_parameters(node: Node) -> list[str]:
    return [
        node_text(name)
        for formal in _formals(node)
        if any(c.type == "ghost" for c in formal.children)
        if (name := formal.child_by_field_name("name")) is not None
    ]


def _pattern_names(root: Node, constructors: frozenset[str] = frozenset()) -> frozenset[str]:
    result = set()
    for node in walk(root):
        if node.type not in {"case_pattern", "id_pattern"}:
            continue
        nested = [n for n in node.named_children if n.type == "case_pattern"]
        if not nested and not any(c.type == "(" for c in node.children):
            result.update(node_text(n) for n in node.named_children if n.type == "identifier")
    return frozenset(result) - constructors


def _formal(node: Node) -> list[Scope]:
    name = node.child_by_field_name("name")
    if name is None and node.type == "type_parameter":
        name = next((n for n in node.named_children if n.type == "identifier"), None)
    parent = node.parent
    if name is None or parent is None:
        return []
    owner = parent.parent if parent.type in {"formals", "generic_parameters"} else parent
    if owner is None:
        return []
    return [Scope(frozenset({node_text(name)}), node.end_byte, owner.end_byte)]


def _local(node: Node) -> list[Scope]:
    name = node.child_by_field_name("name")
    statement = node.parent
    if name is None or statement is None or statement.parent is None:
        return []
    return [Scope(frozenset({node_text(name)}), statement.end_byte, statement.parent.end_byte)]


def _quantifier(node: Node) -> list[Scope]:
    name = next((n for n in node.named_children if n.type == "identifier"), None)
    domain = node.parent
    if name is None or domain is None or domain.parent is None:
        return []
    typed = next((n for n in node.named_children if n.type.endswith("_type")), None)
    return [
        Scope(
            frozenset({node_text(name)}),
            typed.end_byte if typed else name.end_byte,
            domain.parent.end_byte,
        )
    ]


def _pattern_scope(node: Node, constructors: frozenset[str] = frozenset()) -> list[Scope]:
    patterns = [n for n in node.named_children if n.type.endswith("_pattern")]
    if not patterns:
        return []
    names = frozenset().union(*(_pattern_names(p, constructors) for p in patterns))
    return [Scope(names, patterns[0].start_byte, node.end_byte)]


def _lambda(node: Node) -> list[Scope]:
    arrow = next((c for c in node.children if c.type == "=>"), None)
    if arrow is None:
        return []
    names = frozenset(
        node_text(n)
        for n in node.named_children
        if n.type == "identifier" and n.end_byte <= arrow.start_byte
    )
    return [Scope(names, arrow.end_byte, node.end_byte)]


def _argument(node: Node) -> list[Scope]:
    parent = node.parent
    head = parent.child_by_field_name("function") if parent else None
    if head is None or parent is None:
        return [Scope(frozenset(), node.start_byte, node.end_byte, context="argument")]
    arguments = [n for n in parent.named_children if n.type == "actual_binding"]
    selector: int | str = arguments.index(node)
    if any(c.type == ":=" for c in node.children):
        selector = node_text(node.named_children[0])
    return [
        Scope(
            frozenset(),
            node.start_byte,
            node.end_byte,
            context="argument",
            callee=head.start_byte,
            argument=selector,
        )
    ]


def _proof(node: Node) -> list[Scope]:
    tokens = {c.type for c in node.children if not c.is_named}
    if node.type == "function_body" and "by" in tokens:
        method = next((c for c in node.named_children if c.type == "block_statement"), None)
        if method is not None:
            return [Scope(frozenset(), node.start_byte, method.start_byte, context="contract")]
    if node.type.endswith("_clause") and node.type not in {
        "case_expr_clause",
        "case_stmt_clause",
        "fn_case_expr_clause",
    }:
        return [Scope(frozenset(), node.start_byte, node.end_byte, context="contract")]
    if node.type in _SPEC or (
        node.type == "forall_statement"
        and any(c.type == "ensures_clause" for c in node.named_children)
    ):
        end = node.named_children[-1].start_byte if node.type.startswith("fn_") else node.end_byte
        return [Scope(frozenset(), node.start_byte, end, context="proof")]
    if "ghost" not in tokens:
        return []
    result = (
        [Scope(frozenset(), node.start_byte, node.end_byte, context="proof")]
        if node.type == "var_decl_statement"
        else []
    )
    if node.type in {"let_expression", "fn_let_expression"}:
        return [
            Scope(frozenset(), child.start_byte, child.end_byte, context="proof")
            for child in node.named_children[:-1]
            if child.type != "case_pattern"
        ]
    return result


_HANDLERS = {
    "formal": _formal,
    "type_parameter": _formal,
    "local_var_decl": _local,
    "quantifier_var": _quantifier,
    "lambda_expression": _lambda,
    "actual_binding": _argument,
    **dict.fromkeys(
        (
            "case_expr_clause",
            "case_stmt_clause",
            "fn_case_expr_clause",
            "let_expression",
            "fn_let_expression",
        ),
        _pattern_scope,
    ),
}


def scopes(root: Node, constructors: frozenset[str] = frozenset()) -> tuple[Scope, ...]:
    result = []
    ancestor = root.parent
    while ancestor is not None:
        for child in ancestor.named_children:
            if child.type == "generic_parameters":
                result.extend(
                    scope for parameter in child.named_children for scope in _formal(parameter)
                )
        ancestor = ancestor.parent
    nodes = walk(root, SKIP)
    for node in nodes:
        handler = _HANDLERS.get(node.type)
        if handler == _pattern_scope:
            result.extend(_pattern_scope(node, constructors))
        elif handler:
            result.extend(handler(node))
        result.extend(_statement_scopes(node))
        result.extend(_proof(node))
    result.extend(_ghost_assignments(root, nodes))
    return tuple(result)


def _statement_scopes(node: Node) -> list[Scope]:
    result = []
    if node.type == "var_decl_statement" and node.parent is not None:
        names = _pattern_names(node)
        result.append(Scope(names, node.start_byte, node.parent.end_byte))
    elif node.type == "for_statement":
        name = next((n for n in node.named_children if n.type == "identifier"), None)
        body = next((n for n in node.named_children if n.type == "block_statement"), None)
        if name is not None and body is not None:
            result.append(Scope(frozenset({node_text(name)}), name.start_byte, node.end_byte))
    return result


def _assignment_bindings(root: Node, nodes: list[Node]) -> list[tuple[str, int, int, bool]]:
    bindings = []
    for node in nodes:
        if node.type not in {"var_decl_statement", "formal"}:
            continue
        ghost = any(c.type == "ghost" for c in node.children)
        if node.type == "formal":
            name = node.child_by_field_name("name")
            if name is not None:
                bindings.append((node_text(name), root.start_byte, root.end_byte, ghost))
        elif node.parent is not None:
            names = [
                c.child_by_field_name("name")
                for c in node.named_children
                if c.type == "local_var_decl"
            ]
            bindings.extend(
                (node_text(n), node.end_byte, node.parent.end_byte, ghost)
                for n in names
                if n is not None
            )
    return bindings


def _ghost_assignments(root: Node, nodes: list[Node]) -> list[Scope]:
    bindings = _assignment_bindings(root, nodes)
    result = []
    for node in nodes:
        if node.type != "assign_statement":
            continue
        mark = next((c for c in node.children if c.type == ":="), None)
        if mark is None:
            continue
        names = [node_text(n) for n in node.named_children if n.end_byte <= mark.start_byte]
        active = {
            name: ghost for name, start, end, ghost in bindings if start <= node.start_byte < end
        }
        if names and all(active.get(name, False) for name in names):
            result.append(Scope(frozenset(), mark.end_byte, node.end_byte, context="proof"))
    return result
