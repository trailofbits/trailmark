"""The bundled circom grammar must load through the shared native compiler."""

from tree_sitter import Language, Parser

from trailmark.tree_sitter_custom.circom import language


def test_language_capsule_parses_circom_syntax() -> None:
    tree = Parser(Language(language())).parse(b"template Main() { signal input x; }")
    assert not tree.root_node.has_error
    assert tree.root_node.named_children
