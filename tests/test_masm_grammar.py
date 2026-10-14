"""The bundled masm grammar must load through the shared native compiler."""

from tree_sitter import Language, Parser

from trailmark.tree_sitter_custom.masm import language


def test_language_capsule_parses_masm_syntax() -> None:
    tree = Parser(Language(language())).parse(b"proc main\n    push.1\nend")
    assert not tree.root_node.has_error
    assert tree.root_node.named_children
