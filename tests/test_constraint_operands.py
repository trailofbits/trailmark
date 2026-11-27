"""Call evidence retains the actual operands, receivers, labels, and byte spans."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
from tree_sitter_language_pack import get_parser

from trailmark.models import CodeEdge, CodeUnit, EdgeKind, NodeKind, SourceLocation
from trailmark.parse import parse_file
from trailmark.parsers._call_sites import collect_call_sites

pytestmark = pytest.mark.mutation_fast

_POSITIONAL = (("positional", None, "7"), ("positional", None, "9"))
_NAMED = (("positional", None, "7"), ("named", "label", "9"))


def _source_at(data: bytes, location: SourceLocation) -> bytes:
    assert location.start_col is not None and location.end_col is not None
    lines = data.splitlines(keepends=True)
    start = sum(map(len, lines[: location.start_line - 1])) + location.start_col
    end = sum(map(len, lines[: location.end_line - 1])) + location.end_col
    return data[start:end]


@pytest.mark.parametrize(
    ("language", "extension", "source", "syntax", "receiver", "operands"),
    [
        (
            "python",
            "py",
            "def caller(obj, xs, kw): return obj.target(7, label=9, *xs, **kw)\n",
            "obj.target(7, label=9, *xs, **kw)",
            "obj",
            (*_NAMED, ("unpacked", None, "*xs"), ("unpacked", None, "**kw")),
        ),
        (
            "python",
            "py",
            "def caller(π): return target('λ', π)\n",
            "target('λ', π)",
            None,
            (("positional", None, "'λ'"), ("positional", None, "π")),
        ),
        (
            "php",
            "php",
            "<?php function caller($obj, $xs) { return $obj->target(7, label: 9, ...$xs); }",
            "$obj->target(7, label: 9, ...$xs)",
            "$obj",
            (*_NAMED, ("unpacked", None, "...$xs")),
        ),
        (
            "javascript",
            "js",
            "function caller(obj,xs) { return obj.target(7,...xs); }",
            "obj.target(7,...xs)",
            "obj",
            (("positional", None, "7"), ("unpacked", None, "...xs")),
        ),
        (
            "c_sharp",
            "cs",
            "class App { void Caller(Target obj) { obj.Target(7, label: 9); } }",
            "obj.Target(7, label: 9)",
            "obj",
            _NAMED,
        ),
        (
            "c_sharp",
            "cs",
            "class App { void Caller(Target obj) { obj.Target(7, label: /*value*/ 9); } }",
            "obj.Target(7, label: /*value*/ 9)",
            "obj",
            _NAMED,
        ),
        (
            "go",
            "go",
            "package app\nfunc caller(obj Target) { obj.Target(7,9) }",
            "obj.Target(7,9)",
            "obj",
            _POSITIONAL,
        ),
        (
            "cpp",
            "cpp",
            "void caller(Target obj) { obj.target(7,9); }",
            "obj.target(7,9)",
            "obj",
            _POSITIONAL,
        ),
        (
            "rust",
            "rs",
            "fn caller(obj: Target) { obj.target(7,9); }",
            "obj.target(7,9)",
            "obj",
            _POSITIONAL,
        ),
        (
            "java",
            "java",
            "class App { void caller(Target obj) { obj.target(7,9); } }",
            "obj.target(7,9)",
            "obj",
            _POSITIONAL,
        ),
        (
            "kotlin",
            "kt",
            "fun caller(obj: Target) { obj.target(7, label = 9) }",
            "obj.target(7, label = 9)",
            "obj",
            _NAMED,
        ),
        (
            "dart",
            "dart",
            "void caller(Target obj) { obj.target(7, label: 9); }",
            "obj.target(7, label: 9)",
            "obj",
            _NAMED,
        ),
        (
            "dart",
            "dart",
            "void caller(Target obj) { obj /*receiver*/ .target(7, label: /*value*/ 9); }",
            "obj /*receiver*/ .target(7, label: /*value*/ 9)",
            "obj",
            _NAMED,
        ),
        (
            "swift",
            "swift",
            "func caller(obj: Target) { obj.target(7, label: 9) }",
            "obj.target(7, label: 9)",
            "obj",
            _NAMED,
        ),
        (
            "objc",
            "m",
            "void caller(id obj) { [obj target:7 label:9]; }",
            "[obj target:7 label:9]",
            "obj",
            _POSITIONAL,
        ),
        (
            "ruby",
            "rb",
            "def caller(obj)\n obj.target(7, label: 9)\nend",
            "obj.target(7, label: 9)",
            "obj",
            _NAMED,
        ),
        (
            "erlang",
            "erl",
            "-module(app).\ncaller() -> remote:target(7,9).",
            "remote:target(7,9)",
            None,
            _POSITIONAL,
        ),
        ("perl", "pl", "sub caller { target(7,9); }", "target(7,9)", None, _POSITIONAL),
        (
            "perl",
            "pl",
            "sub caller { my $obj = $_[0]; $obj->target(7,9); }",
            "$obj->target(7,9)",
            "$obj",
            _POSITIONAL,
        ),
        (
            "perl",
            "pl",
            "sub caller { target((7,9)); }",
            "target((7,9))",
            None,
            (("positional", None, "(7,9)"),),
        ),
        (
            "solidity",
            "sol",
            "contract App { function caller() public { target(7,9); } }",
            "target(7,9)",
            None,
            _POSITIONAL,
        ),
        ("zig", "zig", "pub fn caller() void { target(7,9); }", "target(7,9)", None, _POSITIONAL),
        ("perl", "pl", "sub caller { target 7; }", "target 7", None, (("positional", None, "7"),)),
        (
            "haskell",
            "hs",
            "module App where\ntarget a b = a\ncaller x = target 7 9\n",
            "target 7 9",
            None,
            _POSITIONAL,
        ),
        (
            "fstar",
            "fst",
            "module App\nlet target (a:int) (b:int) : Tot int = a\n"
            "let caller (x:int) : Tot int = target 7 9\n",
            "target 7 9",
            None,
            _POSITIONAL,
        ),
        (
            "whyml",
            "mlw",
            "module App\n let target (a:int) (b:int) : int = a\n"
            " let caller (x:int) : int = target 7 9\nend\n",
            "target 7 9",
            None,
            _POSITIONAL,
        ),
        (
            "dafny",
            "dfy",
            "module App { function Target(a:int,b:int): int { a } "
            "function Caller(x:int): int { Target(7,9) } }",
            "Target(7,9)",
            None,
            _POSITIONAL,
        ),
    ],
)
def test_explicit_operands_and_receivers_match_the_original_source(
    tmp_path: Path,
    language: str,
    extension: str,
    source: str,
    syntax: str,
    receiver: str | None,
    operands: tuple[tuple[str, str | None, str], ...],
) -> None:
    path = tmp_path / f"app.{extension}"
    path.write_text(source)
    graph = parse_file(str(path), language)
    (site,) = graph.call_sites.values()
    assert site.syntax == syntax
    assert (site.receiver.expression if site.receiver else None) == receiver
    assert tuple((arg.kind, arg.name, arg.expression) for arg in site.arguments) == operands
    assert ("unpacked_argument_mapping_unknown" in site.gaps) == any(
        kind == "unpacked" for kind, _, _ in operands
    )
    assert "argument_syntax_unavailable" not in site.gaps
    assert "call_prefix_unavailable" not in site.gaps
    assert site.source_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    for operand in (*site.arguments, *((site.receiver,) if site.receiver else ())):
        assert _source_at(path.read_bytes(), operand.location) == operand.expression.encode()


def test_dart_sibling_chains_keep_each_invocation_and_receiver(tmp_path: Path) -> None:
    path = tmp_path / "app.dart"
    path.write_text("void caller(Target obj) { final value = obj.first(7).target(9); }")
    graph = parse_file(str(path), "dart")
    sites = sorted(graph.call_sites.values(), key=lambda site: site.location.start_col or 0)
    assert [site.syntax for site in sites] == ["obj.first(7)", "obj.first(7).target(9)"]
    assert [site.receiver.expression if site.receiver else None for site in sites] == [
        "obj",
        "obj.first(7)",
    ]
    assert [[argument.expression for argument in site.arguments] for site in sites] == [
        ["7"],
        ["9"],
    ]
    for site in sites:
        assert site.receiver is not None
        assert (
            _source_at(path.read_bytes(), site.receiver.location)
            == site.receiver.expression.encode()
        )


@pytest.mark.parametrize("expression", ["(obj)", "'text'", "[7, 9]"])
def test_dart_primary_expressions_keep_their_receiver(tmp_path: Path, expression: str) -> None:
    path = tmp_path / "app.dart"
    path.write_text(f"void caller(dynamic obj) {{ {expression}.target(7); }}")
    graph = parse_file(str(path), "dart")
    (site,) = graph.call_sites.values()
    assert site.syntax == f"{expression}.target(7)"
    assert site.receiver is not None and site.receiver.expression == expression
    assert _source_at(path.read_bytes(), site.receiver.location) == expression.encode()


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [("()", []), ("(7)", ["7"]), ("(7,9)", ["7", "9"]), ("((7,9))", ["(7,9)"])],
)
@pytest.mark.parametrize("callee", ["target", "value~target"])
def test_func_argument_groups_do_not_hide_or_invent_operands(
    tmp_path: Path, arguments: str, expected: list[str], callee: str
) -> None:
    path = tmp_path / "app.fc"
    path.write_text(f"() caller() {{ {callee}{arguments}; }}")
    graph = parse_file(str(path), "func")
    (site,) = graph.call_sites.values()
    assert [argument.expression for argument in site.arguments] == expected
    assert "argument_syntax_unavailable" not in site.gaps


def test_statement_locations_retain_the_invocations_operands(tmp_path: Path) -> None:
    from trailmark.parsers._common import make_location

    path = tmp_path / "app.cpp"
    data = b"void caller() { target(7,9); }"
    path.write_bytes(data)
    graph = parse_file(str(path), "cpp")
    graph.call_sites.clear()
    root = get_parser("cpp").parse(data).root_node
    pending = [root]
    while (node := pending.pop()).type != "expression_statement":
        pending.extend(node.named_children)
    graph.edges = [
        replace(edge, location=make_location(node, str(path)))
        if edge.kind == EdgeKind.CALLS
        else edge
        for edge in graph.edges
    ]
    collect_call_sites(root, str(path), graph, source=data)
    (site,) = graph.call_sites.values()
    assert site.syntax == "target(7,9);"
    assert [argument.expression for argument in site.arguments] == ["7", "9"]
    assert "argument_syntax_unavailable" not in site.gaps


@pytest.mark.parametrize(
    ("language", "extension", "source"),
    [
        (
            "fstar",
            "fst",
            "module App\nlet positive (x:int) : prop = x >= 0\n"
            "let proof (x:nat) : Lemma (positive x) = ()\n",
        ),
        (
            "whyml",
            "mlw",
            "module App\n predicate positive (x:int) = x >= 0\n"
            " let run (x:int) : int requires { positive x } = x\nend\n",
        ),
        (
            "dafny",
            "dfy",
            "module App { predicate Positive(x:int) { x >= 0 } "
            "method Run(x:int) requires Positive(x) {} }",
        ),
    ],
)
def test_specification_applications_do_not_become_runtime_call_sites(
    tmp_path: Path, language: str, extension: str, source: str
) -> None:
    path = tmp_path / f"app.{extension}"
    path.write_text(source)
    graph = parse_file(str(path), language)
    assert any(edge.kind == EdgeKind.REFERENCES for edge in graph.edges)
    assert graph.call_sites == {}


@pytest.mark.parametrize("bom", [False, True])
def test_collection_uses_the_complete_parse_time_source(tmp_path: Path, bom: bool) -> None:
    path = tmp_path / "app.py"
    data = (b"\xef\xbb\xbf" if bom else b"") + b"def caller(): return target(7)\n"
    path.write_bytes(data)
    graph = parse_file(str(path), "python")
    graph.call_sites.clear()
    root = get_parser("python").parse(data).root_node
    if not bom:
        path.unlink()  # The tree already retains all bytes, so no file read is needed.
    collect_call_sites(root, str(path), graph)
    (site,) = graph.call_sites.values()
    assert site.source_sha256 == hashlib.sha256(data).hexdigest()
    assert site.arguments[0].expression == "7"


def test_unlocated_calls_retain_uncertainty_and_other_files_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "app.py"
    data = b"def caller(): return target(7)\n"
    path.write_bytes(data)
    graph = parse_file(str(path), "python")
    graph.call_sites.clear()
    graph.edges = [
        replace(edge, location=None) for edge in graph.edges if edge.kind == EdgeKind.CALLS
    ]
    other = SourceLocation(str(tmp_path / "other.py"), 1, 1)
    graph.nodes["other:caller"] = CodeUnit("other:caller", "caller", NodeKind.FUNCTION, other)
    graph.edges.extend(
        [
            CodeEdge("other:caller", "target", EdgeKind.CALLS),
            CodeEdge("app:caller", "target", EdgeKind.CALLS, location=other),
        ]
    )
    collect_call_sites(get_parser("python").parse(data).root_node, str(path), graph, source=data)
    (site,) = graph.call_sites.values()
    assert site.caller_id == "app:caller"
    assert site.execution == "unknown"
    assert site.gaps == ("call_syntax_unavailable",)


def test_legacy_csharp_spelling_gets_canonical_site_language(tmp_path: Path) -> None:
    path = tmp_path / "app.cs"
    data = b"class App { void Caller() { Target(7); } }"
    path.write_bytes(data)
    graph = parse_file(str(path), "c_sharp")
    graph.call_sites.clear()
    graph.language = "csharp"
    collect_call_sites(get_parser("c_sharp").parse(data).root_node, str(path), graph, source=data)
    assert {site.language for site in graph.call_sites.values()} == {"c_sharp"}
