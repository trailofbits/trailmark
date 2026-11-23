"""Call-site syntax context for C extraction and callback binding."""

from tree_sitter import Node

from trailmark.parsers._binding_index import inside_error
from trailmark.parsers._bindings import lexical_declaration

_ORDINARY_SCOPES = frozenset(
    {"translation_unit", "function_definition", "compound_statement", "for_statement"}
)
_ORDINARY_DECLARATIONS = (
    ("type_definition", "declarator"),
    ("declaration", "declarator"),
    ("init_declarator", "declarator"),
    ("function_definition", "declarator"),
    ("enumerator", "name"),
)


def typedef_cast(function: Node, site: Node, parameters: frozenset[str]) -> bool:
    """Recognize a C typedef cast parsed as a parenthesized call."""
    if function.type != "parenthesized_expression" or function.named_child_count != 1:
        return False
    name = function.named_children[0]
    if name.type != "identifier" or not name.text or syntax_error_context(site):
        return False
    spelling = name.text.decode(errors="replace")
    if spelling in parameters:
        return False
    declaration = lexical_declaration(
        site,
        spelling,
        scopes=_ORDINARY_SCOPES,
        declarations=_ORDINARY_DECLARATIONS,
    )
    return declaration is not None and declaration.type == "type_definition"


def syntax_error_context(node: Node) -> bool:
    """Check the call itself and enclosing ERROR nodes, not sibling statements."""
    if node.has_error:
        return True
    indexed = inside_error(node)
    if indexed is not None:
        return indexed
    parent = node.parent
    while parent is not None:
        if parent.type == "ERROR":
            return True
        parent = parent.parent
    return False
