"""Generic string values from data files, decoded exactly and never executed."""

from pathlib import Path

import pytest

from trailmark.analysis import _reference_literals
from trailmark.analysis._reference_literals import LiteralReference, reference_literals
from trailmark.models.nodes import SourceLocation


def _write(tmp_path: Path, name: str, text: str) -> str:
    file = tmp_path / name
    file.write_bytes(text.encode("utf-8"))
    return str(file)


def _values(tmp_path: Path, name: str, text: str) -> list[str]:
    return [ref.value for ref in reference_literals(_write(tmp_path, name, text))]


_GRAPHQL = r'''# comment "App\\Comment::nope" App\Hidden::nope
"""Type description "App\\Doc::nope" with \""" inside."""
type Query {
  "Field description App\\Desc::nope"
  user(id: ID = "default.value"): User @wire(
    target: "App\\GraphQL\\Users::resolve"
    extra: ["pkg.mod.fn", {inner: "src/views/user.html.twig"}]
  )
  other: Int @glue(code: """
      App\Block::raw \"""quoted\"""
  """)
  esc: Int @glue(code: "App\/x \"q\"")
  unsupported: Int @glue(code: "\u{41}")
}
'''


@pytest.mark.parametrize("suffix", [".graphql", ".gql", ".graphqls"])
def test_graphql_argument_values_under_any_directive(tmp_path: Path, suffix: str) -> None:
    assert _values(tmp_path, f"schema{suffix}", _GRAPHQL) == [
        "default.value",
        "App\\GraphQL\\Users::resolve",
        "pkg.mod.fn",
        "src/views/user.html.twig",
        'App\\Block::raw """quoted"""',
        'App/x "q"',
        "A",
    ]


def test_graphql_spans_cover_the_original_escaped_token(tmp_path: Path) -> None:
    path = _write(tmp_path, "op.graphql", 'query { f(a: "App\\\\X::y") }\n')
    [ref] = reference_literals(path)
    assert ref == LiteralReference("App\\X::y", SourceLocation(path, 1, 1, 13, 24))


def test_json_values_exclude_keys_and_decode_escapes(tmp_path: Path) -> None:
    text = r"""{
  "App\\Key::nope": "App\\Http\\Controller::index",
  "glorp": ["pkg.module:function", "src/app.module.ts#AppModule.handle", 1, true, null],
  "nested": {"x": "App\/y", "empty": "", "double": "App\\\\Double"},
  "zz": "resources/views/home.blade.php"
}
"""
    assert _values(tmp_path, "config.json", text) == [
        "App\\Http\\Controller::index",
        "pkg.module:function",
        "src/app.module.ts#AppModule.handle",
        "App/y",
        "App\\\\Double",
        "resources/views/home.blade.php",
    ]


def test_json_span_includes_quotes(tmp_path: Path) -> None:
    path = _write(tmp_path, "span.json", '{\n  "k": "App\\\\X"\n}\n')
    [ref] = reference_literals(path)
    location = ref.location
    assert (location.start_line, location.start_col, location.end_line, location.end_col) == (
        2,
        7,
        2,
        15,
    )


_YAML = r"""# comment: App\Commented::nope
plain: App\Http\Ctl::run
"App\\QuotedKey::nope": "App\\Escaped::run"
single: 'App\Single::run'
apostrophe: 'it''s'
? App\ComplexKey::nope
: complex.value
flow: {implicit.key.nope, k: flow.value}
list:
  - pkg.mod.fn
  - "true"
  - true
  - 12
  - ~
folded_plain: first
  second
quoted_fold: "one \
  two
  three"
literal: |
  line one
  line two
folded: >-
  a
  b
tagged: !custom tagged.value
explicit_indent: |2
   unsupported
---
next: second.document
"""


@pytest.mark.parametrize("suffix", [".yaml", ".yml"])
def test_yaml_quoted_and_plain_values(tmp_path: Path, suffix: str) -> None:
    assert _values(tmp_path, f"conf{suffix}", _YAML) == [
        "App\\Http\\Ctl::run",
        "App\\Escaped::run",
        "App\\Single::run",
        "it's",
        "complex.value",
        "flow.value",
        "pkg.mod.fn",
        "true",
        "first second",
        "one two three",
        "line one\nline two\n",
        "a b",
        "tagged.value",
        "second.document",
    ]


def test_yaml_block_chomping_and_more_indented_lines(tmp_path: Path) -> None:
    text = "keep: |+\n  a\n\n\nfold: >\n  x\n    y\n  z\n\n  w\nend: e\n"
    assert _values(tmp_path, "block.yaml", text) == ["a\n\n\n", "x\n  y\nz\nw\n", "e"]


def test_yaml_tags_are_not_constructed(tmp_path: Path) -> None:
    sentinel = tmp_path / "executed"
    text = f'payload: !!python/object/apply:os.system ["touch {sentinel}"]\n'
    assert _values(tmp_path, "evil.yaml", text) == [f"touch {sentinel}"]
    assert not sentinel.exists()


def test_toml_values_in_tables_and_arrays(tmp_path: Path) -> None:
    lines = [
        r'# comment = "App\\Comment::nope"',
        r'title = "App\\Toml::run"',
        r"literal = 'App\Literal::run'",
        r'["App\\Table::nope".sub]',
        r'"App\\QuotedKey::nope" = "quoted.key.value"',
        r"""items = ["pkg.mod.fn", 'src/x.html.twig', 3, true]""",
        r'multi = """',
        "App\\\\Multi::\\",
        '  run"""',
        "raw = '''",
        r"App\Raw::run'''",
        r'inline = { handler = "inline.value" }',
        "[[jobs]]",
        r'target = "array.table.value"',
    ]
    text = "\n".join(lines) + "\n"
    assert _values(tmp_path, "pyproject.toml", text) == [
        "App\\Toml::run",
        "App\\Literal::run",
        "quoted.key.value",
        "pkg.mod.fn",
        "src/x.html.twig",
        "App\\Multi::run",
        "App\\Raw::run",
        "inline.value",
        "array.table.value",
    ]


def test_xml_attributes_and_text_but_not_comments_or_names(tmp_path: Path) -> None:
    text = """<?xml version="1.0"?>
<!-- <bean handler="App\\Commented::nope"/> -->
<config xmlns:x="urn:example" glorp="App\\Attr::run" quoted='a &amp; b &#x41;&#66;'>
  <x:item>  pkg.mod.fn  </x:item>
  <cdata><![CDATA[App\\\\Raw <b>]]></cdata>
  <?target App\\Instruction::nope?>
  <empty/>
  <multi attr="one
two">tail.value<!-- split -->after.comment</multi>
</config>
"""
    path = _write(tmp_path, "beans.xml", text)
    refs = reference_literals(path)
    assert [ref.value for ref in refs] == [
        "urn:example",
        "App\\Attr::run",
        "a & b AB",
        "pkg.mod.fn",
        "App\\\\Raw <b>",
        "one two",
        "tail.value",
        "after.comment",
    ]
    item = refs[3].location
    assert (item.start_line, item.start_col, item.end_col) == (4, 12, 22)


def test_xml_entities_are_never_expanded(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("SECRET")
    text = f"""<?xml version="1.0"?>
<!DOCTYPE r [
  <!ENTITY xxe SYSTEM "file://{secret}">
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;&lol;">
]>
<r a="&lol2;">&xxe;<k>kept.value</k></r>
"""
    assert _values(tmp_path, "xxe.xml", text) == ["kept.value"]


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("bad.json", '{"a": "unterminated}\n'),
        ("bad.yaml", "a: [b, c\n"),
        ("escape.yaml", 'ok: fine\nbad: "\\q"\n'),
        ("bad.toml", 'a = "x\n'),
        ("bad.xml", "<a><b></a>\n"),
        ("bad.graphql", 'query { f(a: "x") \n'),
        ("bad.graphql", 'query { f(a: "x\\") }\n'),
    ],
)
def test_malformed_files_yield_nothing(tmp_path: Path, name: str, text: str) -> None:
    assert _values(tmp_path, name, text) == []


def test_unreadable_unsupported_and_oversized_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "dir.json").mkdir()
    assert reference_literals(str(tmp_path / "missing.json")) == []
    assert reference_literals(str(tmp_path / "dir.json")) == []
    assert _values(tmp_path, "settings.ini", "key = App\\X::y\n") == []
    invalid = tmp_path / "latin1.json"
    invalid.write_bytes(b'["\xff", "kept"]')
    assert [ref.value for ref in reference_literals(str(invalid))] == ["kept"]
    monkeypatch.setattr(_reference_literals, "_MAX_BYTES", 8)
    assert _values(tmp_path, "large.json", '["App\\\\X::y"]') == []


def test_scan_failures_are_visible_to_discovery(tmp_path: Path) -> None:
    from trailmark.analysis._reference_literals import scan_reference_literals

    file = tmp_path / "invalid.json"
    file.write_text('{"broken":')
    assert scan_reference_literals(str(file)).issues == ("invalid_or_unsupported_syntax",)
    assert scan_reference_literals(str(tmp_path / "absent.json")).issues == (
        "unreadable_or_over_8_mib",
    )


@pytest.mark.parametrize("value", [r"App\\Handler", r"\u{41}", r"\u0041", r"\uD83D\uDCA9"])
def test_graphql_escape_recovery_preserves_valid_schema(value: str, tmp_path: Path) -> None:
    from trailmark.parse import parse_file

    file = tmp_path / "schema.graphqls"
    file.write_text('type Query { item: String @tag(value: "' + value + '") }')
    graph = parse_file(str(file), strict=True)
    assert "schema:Query.item" in graph.nodes


@pytest.mark.parametrize("value", [r"\q", r"\uDEAD", r"\u{110000}"])
def test_graphql_escape_recovery_retains_invalid_syntax(value: str, tmp_path: Path) -> None:
    from trailmark.parse import parse_file

    file = tmp_path / "schema.graphqls"
    file.write_text('type Query { item: String @tag(value: "' + value + '") }')
    assert parse_file(str(file)).parse_diagnostics


def test_graphql_defaults_use_original_source_after_grammar_recovery(tmp_path: Path) -> None:
    from trailmark.parse import parse_file

    file = tmp_path / "schema.graphqls"
    file.write_text(r'type Query { item(value: String = "a\nb"): String }')
    graph = parse_file(str(file), strict=True)
    assert graph.nodes["schema:Query.item"].parameters[0].default == r'"a\nb"'
