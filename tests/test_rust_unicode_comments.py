"""Unicode comment contents must not be mistaken for ASCII delimiters."""

import json
from pathlib import Path

import pytest
from tree_sitter import Language, Parser

from trailmark.query.api import QueryEngine
from trailmark.tree_sitter_custom import rust, verus


@pytest.mark.parametrize("wrapper", [False, True], ids=["rust", "verus"])
@pytest.mark.parametrize(
    "comment",
    [
        "/* start Ī/ ordinary content */",
        "/* start į* ordinary content */",
        "/* start įĪ ordinary content */",
        "/*Ī/ ordinary content */",
        "/*į* ordinary content */",
        "/** docs Ī/ ordinary content */",
        "/*! docs į* ordinary content */",
        "/* αβ /* real nesting Ī/ */ outside į* */",
    ],
)
def test_unicode_comments_preserve_declarations_and_strict_parsing(
    tmp_path: Path, wrapper: bool, comment: str
) -> None:
    body = f'{comment}\nfn target() {{ let text = r#"/* Ī/ į* */"#; }}\n'
    source = f"verus! {{\n{body}}}\n" if wrapper else body
    grammar = verus if wrapper else rust
    tree = Parser(Language(grammar.language())).parse(source.encode())
    assert not tree.root_node.has_error
    path = tmp_path / "unicode.rs"
    path.write_text(source)
    engine = QueryEngine.from_directory(str(tmp_path), language="rust", strict=True)
    result = json.loads(engine.to_json())["nodes"]["unicode:target"]
    assert result["location"]["start_line"] == (3 if wrapper else 2)


@pytest.mark.parametrize("wrapper", [False, True], ids=["rust", "verus"])
def test_unterminated_unicode_comment_remains_a_strict_diagnostic(
    tmp_path: Path, wrapper: bool
) -> None:
    body = "/* Ī/ still inside an unterminated comment\nfn hidden() {}"
    path = tmp_path / "broken.rs"
    path.write_text(f"verus! {{ {body} }}" if wrapper else body)
    with pytest.raises(ValueError, match="Incomplete source analysis"):
        QueryEngine.from_directory(str(tmp_path), language="rust", strict=True)
