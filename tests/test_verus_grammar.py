"""The bundled Verus grammar must parse actual Verus declarations."""

from tree_sitter import Language, Parser

from trailmark.tree_sitter_custom.verus import language


def test_language_capsule_parses_verus_syntax() -> None:
    tree = Parser(Language(language())).parse(b"verus! { proof fn verified() {} }")
    assert not tree.root_node.has_error
    assert tree.root_node.named_children[0].type == "verus_block"
