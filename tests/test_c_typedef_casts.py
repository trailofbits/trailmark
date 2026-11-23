"""C typedef casts and parenthesized callback calls share an AST form."""

from pathlib import Path

from trailmark.models.edges import EdgeKind
from trailmark.parsers.c import CParser


def test_typedef_casts_preserve_operand_calls_and_value_shadows(tmp_path: Path) -> None:
    source = tmp_path / "casts.c"
    source.write_text(
        """
typedef int Word;
typedef const char *Text;
int read_word(void);
#pragma GCC diagnostic push
int convert(void) { return (Word)(read_word()); }
#pragma GCC diagnostic pop
Text pointer_cast(Text text) { return (Text)(text); }
int parameter(int (*Word)(int)) { return (Word)(1); }
int nested(void) {
    { int (*Word)(int); return (Word)(1); }
}
""",
        encoding="utf-8",
    )
    graph = CParser().parse_file(str(source))
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    assert [(edge.source_id, edge.target_id) for edge in calls] == [
        ("casts:convert", "casts:read_word"),
        ("casts:parameter", "casts:parameter.$binding.Word"),
        ("casts:nested", "casts:nested.$binding.Word"),
    ]


def test_unexpanded_macros_leave_ambiguous_parenthesized_calls(tmp_path: Path) -> None:
    source = tmp_path / "casts.c"
    source.write_text(
        """
typedef int Word;
int callback(int x);
/* macro replacement follows this comment */ #define Word callback
int invoke(void) { return (Word)(1); }
""",
        encoding="utf-8",
    )
    graph = CParser().parse_file(str(source))
    assert any(
        edge.kind == EdgeKind.CALLS and edge.source_id == "casts:invoke" for edge in graph.edges
    )
