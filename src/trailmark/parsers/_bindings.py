"""Lexical binding queries with explicit language-supplied scope policies."""

from __future__ import annotations

import re
from dataclasses import replace

from tree_sitter import Node

from trailmark.models.edges import CodeEdge, EdgeConfidence
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit
from trailmark.parsers._binding_index import local_unit, scope_index

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
        "func_literal",
        "lambda_literal",
        "anonymous_function",
        "closure_expression",
        "block",
        "compound_statement",
        "statement_block",
        "function_body",
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
    """Keep lexical values from binding to unrelated named declarations."""
    policy = _DECLARATIONS.get(graph.language)
    if policy is None or (graph.language == "objc" and occurrence.type == "message_expression"):
        return edge
    name = _bare_name(edge, occurrence)
    if name is None:
        return edge
    scopes = frozenset({"function_definition", "lambda"}) if graph.language == "python" else _SCOPES
    index = scope_index(occurrence, scopes, policy)
    if index.callback_parameter(occurrence, name):
        return _blocked(edge, name)
    directive = index.directive(occurrence, name) if graph.language == "python" else None
    if directive in {"global_statement", "nonlocal_statement"}:
        return edge if directive == "global_statement" else _blocked(edge, name)
    source = graph.nodes.get(edge.source_id)
    declaration = index.declaration(
        occurrence, name, graph.language not in {"python", "javascript", "typescript"}
    )
    parameter = source is not None and any(param.name == name for param in source.parameters)
    if not parameter and declaration is None:
        return edge
    local = _local_callable(graph, declaration, name, source)
    if local is not None:
        return replace(
            edge,
            target_id=local.id,
            confidence=EdgeConfidence.CERTAIN,
            attributes=tuple(
                (key, value)
                for key, value in edge.attributes
                if key not in {"binding_blocked", "binding_reason"}
            ),
        )
    return _blocked(edge, name)


def _bare_name(edge: CodeEdge, occurrence: Node) -> str | None:
    explicit = dict(edge.attributes).get("bare_name")
    if isinstance(explicit, str):
        name = explicit
    else:
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
        if function is None or function.text is None:
            return None
        name = function.text.decode()
    return name if re.fullmatch(r"[A-Za-z_$][\w$]*", name) else None


def _local_callable(
    graph: CodeGraph, declaration: Node | None, name: str, source: CodeUnit | None
) -> CodeUnit | None:
    if (
        declaration is None
        or source is None
        or declaration.type
        not in {
            "function_definition",
            "function_declaration",
            "local_function_statement",
            "local_function_declaration",
        }
    ):
        return None
    return local_unit(graph, declaration, name, source)


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
    """Find a visible declaration using one parse-local index per scope policy.

    Ordered languages bind a new name only after its declaration has finished,
    so an initializer can still call the previous binding with the same name.
    """
    return scope_index(occurrence, scopes, declarations).declaration(
        occurrence, name, declaration_order
    )
