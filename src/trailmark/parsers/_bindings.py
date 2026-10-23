"""Lexical binding queries with explicit language-supplied scope policies."""

from __future__ import annotations

import os
import re
from dataclasses import replace
from pathlib import Path

from tree_sitter import Node

from trailmark.models.edges import CodeEdge, EdgeConfidence
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit
from trailmark.parsers._binding_index import (
    cached_import_target,
    call_parameter,
    local_unit,
    pattern_names,
    scope_index,
)
from trailmark.parsers._common import module_id_from_path

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
_SCOPE_POLICIES = {
    "python": frozenset({"function_definition", "lambda"}),
    **dict.fromkeys(
        ("kotlin", "swift", "dart"), _SCOPES | {"class_body", "enum_class_body", "enum_body"}
    ),
    **dict.fromkeys(
        ("javascript", "typescript"),
        _SCOPES
        | {
            "program",
            "method_definition",
            "function",
            "generator_function",
            "generator_function_declaration",
        },
    ),
}

# Name/pattern fields are grammar policies, not guesses based on declaration text.
_DECLARATIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "python": (
        ("assignment", "left"),
        ("for_statement", "left"),
        ("function_definition", "name"),
        ("class_definition", "name"),
    ),
    "javascript": (
        ("formal_parameters", ""),
        ("variable_declarator", "name"),
        ("function_declaration", "name"),
        ("assignment_expression", "left"),
    ),
    "typescript": (
        ("formal_parameters", ""),
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
    if policy is None or (graph.language, occurrence.type) == ("objc", "message_expression"):
        return edge
    name = _bare_name(edge, occurrence)
    if name is None:
        return edge
    scopes = _SCOPE_POLICIES.get(graph.language, _SCOPES)
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
    if graph.language == "python" and declaration is None and index.local_binding(occurrence, name):
        return _blocked(edge, name)
    return _bound_call(edge, declaration, name, source, graph)


def _bound_call(
    edge: CodeEdge,
    declaration: Node | None,
    name: str,
    source: CodeUnit | None,
    graph: CodeGraph,
) -> CodeEdge:
    local = _local_callable(graph, declaration, name, source)
    parameter = call_parameter(source, name)
    if parameter and local is None:
        return _blocked(edge, name)
    imported = _require_target(declaration, name, source)
    if imported is not None:
        return replace(
            edge,
            target_id=imported,
            confidence=EdgeConfidence.INFERRED,
            attributes=(*edge.attributes, ("import_binding", True)),
        )
    if declaration is None:
        return edge
    if local is not None:
        return _local_edge(edge, local)
    if declaration is not None and declaration.type == "function_declaration":
        # A later member body may not be extracted yet; lexical linking runs
        # after the complete class is available. Callable fields remain blocked.
        return edge
    return _blocked(edge, name)


def _local_edge(edge: CodeEdge, local: CodeUnit) -> CodeEdge:
    attributes = tuple(
        (key, value)
        for key, value in edge.attributes
        if key not in {"binding_blocked", "binding_reason"}
    )
    if (edge.target_id, edge.confidence, edge.attributes) == (
        local.id,
        EdgeConfidence.CERTAIN,
        attributes,
    ):
        return edge
    return replace(
        edge, target_id=local.id, confidence=EdgeConfidence.CERTAIN, attributes=attributes
    )


def _require_target(declaration: Node | None, name: str, source: CodeUnit | None) -> str | None:
    if declaration is None or source is None or declaration.type != "variable_declarator":
        return None
    key = (declaration.id, name, source.location.file_path)
    return cached_import_target(key, lambda: _resolve_require(declaration, name, source))


def _resolve_require(declaration: Node, name: str, source: CodeUnit) -> str | None:
    path = _require_path(declaration)
    export = _require_export(declaration, name)
    if path is None or export is None:
        return None
    if path.startswith("."):
        target = Path(os.path.abspath(Path(source.location.file_path).parent / path))
        if not target.suffix:
            file = target.with_suffix(Path(source.location.file_path).suffix)
            target = (
                file if file.is_file() or not target.is_dir() else target / ("index" + file.suffix)
            )
        module = module_id_from_path(str(target))
    else:
        module = path.replace(":", ".")
    return f"{module}:{export}"


def _require_export(declaration: Node, name: str) -> str | None:
    pattern = declaration.child_by_field_name("name")
    if pattern is None or pattern.type != "object_pattern":
        return None
    for member in pattern.named_children:
        if name not in pattern_names(member):
            continue
        key = member.child_by_field_name("key")
        return key.text.decode() if key is not None and key.text else name
    return None


def _require_path(declaration: Node) -> str | None:
    value = declaration.child_by_field_name("value")
    if value is None or value.type != "call_expression":
        return None
    function = value.child_by_field_name("function")
    if function is None or function.text != b"require":
        return None
    arguments = value.child_by_field_name("arguments")
    return _string_argument(arguments) if arguments is not None else None


def _string_argument(arguments: Node) -> str | None:
    argument = next(iter(arguments.named_children), None)
    if argument is None or argument.type != "string" or argument.text is None:
        return None
    return argument.text.decode()[1:-1]


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
    if declaration is not None and declaration.type == "variable_declarator":
        value = declaration.child_by_field_name("value")
        if value is not None and value.type in {
            "arrow_function",
            "function_expression",
            "function",
        }:
            declaration = value
    if (
        declaration is None
        or source is None
        or declaration.type
        not in {
            "function_definition",
            "function_declaration",
            "local_function_statement",
            "local_function_declaration",
            "arrow_function",
            "function_expression",
            "function",
            "class_definition",
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

    Rust, Go and Kotlin bind after the declaration; C-family names are visible
    after the declarator, including within their own initializer.
    """
    return scope_index(occurrence, scopes, declarations).declaration(
        occurrence, name, declaration_order
    )
