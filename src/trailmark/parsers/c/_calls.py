"""Call-site syntax context for C extraction and callback binding."""

from tree_sitter import Node

from trailmark.parsers._binding_index import inside_error


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
