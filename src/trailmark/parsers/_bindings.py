"""Lexical binding queries with explicit language-supplied scope policies."""

from __future__ import annotations

import re
from dataclasses import replace

from tree_sitter import Node

from trailmark.models.edges import CodeEdge, EdgeConfidence
from trailmark.models.graph import CodeGraph
from trailmark.parsers._walk import walk_owned

_SCOPES = frozenset(
    {
        "function_definition",
        "function_declaration",
        "function_item",
        "method_declaration",
        "constructor_declaration",
        "local_function_statement",
        "lambda_expression",
        "lambda",
        "arrow_function",
        "function_expression",
        "lambda_literal",
        "anonymous_function",
        "closure_expression",
        "block",
        "compound_statement",
        "statement_block",
        "function_body",
        "class_body",
        "contract_body",
    }
)

# Name/pattern fields are grammar policies, not guesses based on declaration text.
_DECLARATIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "python": (("assignment", "left"), ("for_statement", "left"), ("function_definition", "name")),
    "javascript": (
        ("variable_declarator", "name"),
        ("function_declaration", "name"),
        ("assignment_expression", "left"),
    ),
    "typescript": (
        ("variable_declarator", "name"),
        ("function_declaration", "name"),
        ("assignment_expression", "left"),
    ),
    "c": (("init_declarator", "declarator"), ("declaration", "declarator")),
    "cpp": (
        ("init_declarator", "declarator"),
        ("declaration", "declarator"),
        ("field_declaration", "declarator"),
    ),
    "csharp": (("variable_declarator", "name"), ("local_function_statement", "name")),
    "java": (("variable_declarator", "name"), ("enhanced_for_statement", "name")),
    "go": (("short_var_declaration", "left"), ("var_spec", "name")),
    "rust": (("let_declaration", "pattern"),),
    "solidity": (
        ("variable_declaration", "name"),
        ("parameter", "name"),
        ("state_variable_declaration", "name"),
    ),
    "swift": (("property_declaration", "name"), ("function_declaration", "name")),
    "kotlin": (("variable_declaration", "name"), ("function_declaration", "name")),
    "dart": (("initialized_variable_definition", "name"), ("initialized_identifier", "name")),
    "objc": (("init_declarator", "declarator"), ("declaration", "declarator")),
}


def guard_call(edge: CodeEdge, occurrence: Node, *, graph: CodeGraph) -> CodeEdge:
    """Keep a lexical callable from binding to an unrelated named declaration."""
    policy = _DECLARATIONS.get(graph.language)
    if policy is None:
        return edge
    if graph.language == "objc" and occurrence.type == "message_expression":
        return edge
    if graph.language == "java" and occurrence.child_by_field_name("object") is not None:
        return edge
    function = occurrence.child_by_field_name("function")
    if function is None:
        function = next(
            (
                child
                for child in occurrence.named_children
                if child.type in {"identifier", "simple_identifier"}
            ),
            None,
        )
    explicit_name = dict(edge.attributes).get("bare_name")
    if isinstance(explicit_name, str):
        name = explicit_name
    elif function is None or function.text is None:
        name = explicit_name
        if not isinstance(name, str):
            return edge
    else:
        name = function.text.decode()
    if re.fullmatch(r"[A-Za-z_$][\w$]*", name) is None:
        return edge
    source = graph.nodes.get(edge.source_id)
    if graph.language == "python":
        # global/nonlocal apply throughout the nearest function, including to
        # assignments after the call. A nonlocal value remains indirect.
        scope = occurrence.parent
        while scope is not None and scope.type not in {"function_definition", "lambda"}:
            scope = scope.parent
        if scope is not None:
            directives = {
                item.type
                for item in walk_owned(
                    scope, boundaries=frozenset({"function_definition", "lambda"})
                )
                if item.type in {"global_statement", "nonlocal_statement"}
                and any(
                    child.text is not None and child.text.decode() == name
                    for child in item.named_children
                )
            }
            if "global_statement" in directives:
                return edge
            if "nonlocal_statement" in directives:
                return _blocked(edge, name)
    parameter = source is not None and any(param.name == name for param in source.parameters)
    scopes = frozenset({"function_definition", "lambda"}) if graph.language == "python" else _SCOPES
    declaration = lexical_declaration(
        occurrence,
        name,
        scopes=scopes,
        declarations=policy,
        declaration_order=graph.language not in {"python", "javascript", "typescript"},
    )
    if not parameter and declaration is None:
        return edge
    # A nested named declaration can already have its own extracted body.
    if declaration is not None and declaration.type in {
        "function_definition",
        "function_declaration",
        "local_function_statement",
        "local_function_declaration",
    }:
        local = [
            unit
            for unit in graph.nodes.values()
            if unit.name == name
            and unit.location.start_line == declaration.start_point.row + 1
            and source is not None
            and unit.location.file_path == source.location.file_path
        ]
        if len(local) == 1:
            return replace(
                edge,
                target_id=local[0].id,
                confidence=EdgeConfidence.CERTAIN,
                attributes=tuple(
                    (key, value)
                    for key, value in edge.attributes
                    if key not in {"binding_blocked", "binding_reason"}
                ),
            )
    return _blocked(edge, name)


def _blocked(edge: CodeEdge, name: str) -> CodeEdge:
    attributes = dict(edge.attributes)
    attributes.update(binding_blocked=True, binding_reason="lexical_callable")
    return replace(
        edge,
        target_id=f"{edge.source_id}.$binding.{name}",
        confidence=EdgeConfidence.UNCERTAIN,
        attributes=tuple(attributes.items()),
    )


def lexical_declaration(
    occurrence: Node,
    name: str,
    *,
    scopes: frozenset[str],
    declarations: tuple[tuple[str, str], ...],
    declaration_order: bool = True,
) -> Node | None:
    """Find the nearest visible declaration without entering sibling scopes.

    Grammars specify both scope boundaries and the field containing a binding
    pattern. Function-scoped languages may include declarations appearing later
    in source by setting ``declaration_order=False``. This helper establishes
    shadowing only; it does not infer the value of an indirect callable.
    """
    ancestor = occurrence.parent
    fields = dict(declarations)
    while ancestor is not None:
        if ancestor.type in scopes:
            matches = []
            for node in walk_owned(ancestor, boundaries=scopes):
                field = fields.get(node.type)
                if field is None or (
                    declaration_order and node.start_byte >= occurrence.start_byte
                ):
                    continue
                patterns = node.children_by_field_name(field)
                if node.type in {"function_declaration", "function_definition"}:
                    patterns = patterns[:1]
                if not patterns and node.type == "local_function_declaration":
                    for wrapper in node.named_children:
                        parameters = wrapper.child_by_field_name("parameters")
                        if parameters is not None:
                            name_node = parameters.child_by_field_name("name")
                            if name_node is not None:
                                patterns.append(name_node)
                if not patterns and node.type in {
                    "variable_declaration",
                    "function_declaration",
                    "initialized_variable_definition",
                    "initialized_identifier",
                }:
                    patterns = [
                        child
                        for child in node.named_children
                        if child.type in {"simple_identifier", "identifier"}
                    ][:1]
                if any(
                    (pattern.text == name.encode() or name in _pattern_names(pattern))
                    for pattern in patterns
                ):
                    matches.append(node)
            if matches:
                return max(matches, key=lambda node: node.start_byte)
        ancestor = ancestor.parent
    return None


def _pattern_names(node: Node) -> set[str]:
    """Read declaration patterns without treating initializers or types as names."""
    if node.type in {
        "identifier",
        "simple_identifier",
        "field_identifier",
        "shorthand_property_identifier_pattern",
    }:
        return {node.text.decode()} if node.text is not None else set()
    if node.type in {"pair_pattern", "object_assignment_pattern"}:
        target = node.child_by_field_name("value") or node.child_by_field_name("left")
        return _pattern_names(target) if target is not None else set()
    declarator = node.child_by_field_name("declarator")
    if declarator is not None:
        return _pattern_names(declarator)
    if node.type in {
        "parenthesized_declarator",
        "pointer_declarator",
        "reference_declarator",
        "array_declarator",
        "tuple_pattern",
        "list_pattern",
        "pattern_list",
        "object_pattern",
        "array_pattern",
        "expression_list",
        "tuple_struct_pattern",
        "slice_pattern",
        "ref_pattern",
        "mut_pattern",
        "typed_parameter",
        "typed_default_parameter",
        "list_splat_pattern",
        "dictionary_splat_pattern",
        "pattern",
        "rest_pattern",
        "assignment_pattern",
    }:
        children = node.named_children
        if node.type in {"typed_parameter", "typed_default_parameter", "assignment_pattern"}:
            children = children[:1]
        return set().union(*(_pattern_names(child) for child in children))
    return set()
