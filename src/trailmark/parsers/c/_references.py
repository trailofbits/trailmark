"""Located C-family function values, separate from immediate invocation."""

from __future__ import annotations

from tree_sitter import Node

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind
from trailmark.parsers._binding_index import syntax_nodes
from trailmark.parsers._bindings import lexical_declaration
from trailmark.parsers._c_headers import function_prototype_name
from trailmark.parsers._common import make_location, node_text

_SCOPES = frozenset(
    {"translation_unit", "function_definition", "compound_statement", "for_statement"}
)
_DECLARATIONS = (
    ("declaration", "declarator"),
    ("init_declarator", "declarator"),
    ("function_definition", "declarator"),
    ("type_definition", "declarator"),
    ("enumerator", "name"),
)


def collect_function_references(root: Node, module: str, graph: CodeGraph) -> None:
    """Retain literal values in initializers, assignments, and call arguments.

    Unbound names are staged for the ordinary visibility-aware directory
    linker. Names without a represented function or a function declaration
    are discarded there; variables and callback parameters are never rebound.
    """
    units = [
        unit for unit in graph.nodes.values() if unit.kind in {NodeKind.FUNCTION, NodeKind.METHOD}
    ]
    local = {unit.name: unit.id for unit in units if unit.kind == NodeKind.FUNCTION}
    for node in syntax_nodes(root, frozenset({"identifier"})):
        context = _value_context(node)
        if context is None:
            continue
        location = make_location(node, graph.nodes[module].location.file_path)
        enclosing = [
            unit
            for unit in units
            if (
                (unit.location.start_line, unit.location.start_col or 0)
                <= (location.start_line, location.start_col or 0)
                <= (unit.location.end_line, unit.location.end_col or 0)
            )
        ]
        owner = min(
            enclosing,
            key=lambda unit: unit.location.end_line - unit.location.start_line,
            default=graph.nodes[module],
        )
        name = node_text(node)
        if name in {parameter.name for parameter in owner.parameters}:
            continue
        declaration = lexical_declaration(node, name, scopes=_SCOPES, declarations=_DECLARATIONS)
        prototype = declaration is not None and function_prototype_name(declaration) is not None
        if declaration is not None and declaration.type != "function_definition" and not prototype:
            continue
        attributes = [
            ("literal_callable", True),
            ("reference_context", context),
            ("symbol", name),
        ]
        if prototype:
            attributes.append(("literal_callable_declared", True))
        graph.edges.append(
            CodeEdge(
                owner.id,
                local.get(name, f"{module}:{name}"),
                EdgeKind.REFERENCES,
                EdgeConfidence.INFERRED,
                location,
                tuple(attributes),
            )
        )


def _value_context(node: Node) -> str | None:
    child, parent = node, node.parent
    while parent is not None:
        if parent.type == "argument_list":
            return "callback_argument"
        if parent.type == "init_declarator" and parent.child_by_field_name("value") == child:
            return "initializer"
        if parent.type == "assignment_expression" and parent.child_by_field_name("right") == child:
            return "assignment"
        if parent.type in {"parenthesized_expression", "initializer_list"}:
            pass
        elif (
            parent.type == "pointer_expression"
            and (operator := parent.child_by_field_name("operator")) is not None
        ):
            if operator.text != b"&":
                return None
        elif parent.type in {"initializer_pair", "cast_expression"}:
            if parent.child_by_field_name("value") != child:
                return None
        else:
            return None
        child, parent = parent, parent.parent
    return None
