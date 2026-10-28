"""C recovery contracts for conditional tokens and physical function spans."""

from __future__ import annotations

from pathlib import Path

import pytest
from tree_sitter import Node, Parser
from tree_sitter_language_pack import get_language

from trailmark.models import EdgeConfidence, EdgeKind, NodeKind
from trailmark.parse import parse_file
from trailmark.parsers.c import _recovery


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            b"#if 0\nskip\n#else\nlive\n#endif\nend",
            [b"#if 0", b"#else", b"live", b"#endif", b"end"],
        ),
        (
            b"#if 0\nskip\n#elif OPTION\nlive\n#endif\nend",
            [b"#if 0", b"#elif OPTION", b"live", b"#endif", b"end"],
        ),
        (
            b"#if 0\n#if INNER\nskip\n#endif\nalso_skip\n#endif\nend",
            [b"#if 0", b"#if INNER", b"#endif", b"#endif", b"end"],
        ),
        (
            b"#if 0\n#if INNER\nskip\n#else\nalso_skip\n#endif\nstill_skip\n#endif\nend",
            [b"#if 0", b"#if INNER", b"#else", b"#endif", b"#endif", b"end"],
        ),
        (
            b"#if 0\n#if INNER\nskip\n#elif OPTION\nalso_skip\n#endif\nstill_skip\n#endif\nend",
            [b"#if 0", b"#if INNER", b"#elif OPTION", b"#endif", b"#endif", b"end"],
        ),
        (
            b'/* { */ "}" // }\n#define VALUE \\\n { }\n{ live; }',
            [b'"}"', b"#define VALUE \\\n { }", b"{", b"live", b";", b"}"],
        ),
    ],
)
def test_conditional_tokens_keep_directives_and_potentially_active_code(
    source: bytes, expected: list[bytes], newline: bytes
) -> None:
    source = source.replace(b"\n", newline)
    expected = [text.replace(b"\n", newline) for text in expected]
    assert [token.group().rstrip(b"\r\n") for token in _recovery._source_tokens(source)] == expected


@pytest.mark.parametrize(
    "body",
    [
        b"{ helper(); }",
        b'{ "}"; /* } */ { helper(); } }',
        b"{\n#if A\nhelper();\n#else\nother();\n#endif\n}",
        b"{\n#if A\n#if B\n{ helper(); }\n#endif\n#elif C\nother();\n#endif\n}",
    ],
)
def test_physical_span_ends_at_the_matching_body_brace(body: bytes) -> None:
    source = body + b"\nvoid after(void) {}"
    tokens = _recovery._source_tokens(source)
    end = _recovery._physical_end(tokens, 0)
    assert end is not None
    assert tokens[end].end() == len(body)


@pytest.mark.parametrize(
    "body",
    [
        b"{\n#if A\n{\n#else\n{\n#endif\n}\n}",
        b"{\n#if A\n{\n#endif\n}\n}",
        b"{\n#if A\n}\n#endif\n",
        b"{\n#else\nhelper();\n}",
        b"{\n#elif A\nhelper();\n}",
        b"{\n#endif\nhelper();\n}",
        b"{\n#if A\nhelper();\n}",
        b"{ helper();",
    ],
)
def test_unsafe_conditional_spans_are_rejected(body: bytes) -> None:
    assert _recovery._physical_end(_recovery._source_tokens(body), 0) is None


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize(
    "endif", ["#endif // WRAPPER", "#endif /* WRAPPER */", "# endif", "#\tendif"]
)
def test_namespace_directive_trivia_preserves_real_functions(
    tmp_path: Path, endif: str, newline: str
) -> None:
    source = newline.join(
        [
            "#ifdef WRAPPER",
            "namespace WRAPPER {",
            endif,
            "void helper(void) {}",
            "void action(void) { helper(); helper(); }",
            "#ifdef WRAPPER",
            "}",
            "#endif",
            "",
        ]
    ).encode()
    parser = Parser(get_language("c"))
    original = parser.parse(source)
    masked, tree = _recovery._unwrap_cpp_namespace(source, original, parser)
    expected = source.replace(b"namespace WRAPPER {", b" " * len(b"namespace WRAPPER {"))
    closing = expected.rindex(b"}")
    expected = expected[:closing] + b" " + expected[closing + 1 :]
    assert masked == expected
    assert not tree.root_node.has_error
    path = tmp_path / "sample.c"
    path.write_bytes(source)
    graph = parse_file(str(path), "c")
    assert {n.id for n in graph.nodes.values() if n.kind == NodeKind.FUNCTION} == {
        "sample:helper",
        "sample:action",
    }
    unit = graph.nodes["sample:action"]
    assert (unit.location.start_line, unit.location.end_line) == (5, 5)
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    assert [(edge.source_id, edge.target_id) for edge in calls] == [
        ("sample:action", "sample:helper"),
        ("sample:action", "sample:helper"),
    ]
    assert all(edge.confidence == EdgeConfidence.CERTAIN for edge in calls)
    assert [edge.location.start_col for edge in calls if edge.location is not None] == [20, 30]
    assert graph.parse_diagnostics
    with pytest.raises(ValueError, match="diagnostic"):
        parse_file(str(path), "c", strict=True)


@pytest.mark.parametrize("directive", [b"#endif extra", b"#else", b"#elif OPTION"])
def test_namespace_separator_requires_an_endif_without_arguments(directive: bytes) -> None:
    source = (
        b"#ifdef WRAPPER\nnamespace WRAPPER {\n" + directive + b"\n"
        b"void helper(void) {}\n#ifdef WRAPPER\n}\n#endif\n"
    )
    parser = Parser(get_language("c"))
    original = parser.parse(source)
    masked, _ = _recovery._unwrap_cpp_namespace(source, original, parser)
    assert masked == source


_DISABLED_ARMS = [
    "#if 0\nignored {\n#else\nhelper();\n#endif",
    "#if 0\nignored {\n#elif OPTION\nhelper();\n#endif",
    "#if 0\nignored {\n#elif 0\nignored { { {\n#else\nhelper();\n#endif",
    "#if 0\nignored {\n#elif 0\nignored { { {\n#endif",
    "#if 0\n#if INNER\nignored {\n#else\nignored { { {\n#endif\nmore } } }\n#endif",
    "#if 0\n#if INNER\nignored {\n#endif\nmore } } }\n#endif",
    "#if 0 /* disabled */\nignored {\n#endif",
    "#if OPTION\nhelper();\n#elif 0 // disabled\nignored { { {\n#else\nhelper();\n#endif",
]


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("disabled", _DISABLED_ARMS)
def test_disabled_prose_preserves_recovered_calls_and_neighbors(
    tmp_path: Path, disabled: str, newline: str
) -> None:
    source = (
        "#define BLOCK do\n"
        "void helper(void) {}\n"
        "void tail(void) {}\n"
        "void action(void) {\n" + disabled + "\n"
        " BLOCK { helper(); helper(); }\n"
        " tail();\n"
        "} /* action ends */\n"
        "void after(void) { tail(); }\n"
    ).replace("\n", newline)
    path = tmp_path / "sample.c"
    path.write_bytes(source.encode())
    graph = parse_file(str(path), "c")
    assert {n.id for n in graph.nodes.values() if n.kind == NodeKind.FUNCTION} == {
        "sample:helper",
        "sample:tail",
        "sample:action",
        "sample:after",
    }
    unit = graph.nodes["sample:action"]
    lines = source.splitlines()
    end_line = lines.index("} /* action ends */") + 1
    assert (unit.location.start_line, unit.location.end_line) == (4, end_line)
    assert dict(unit.attributes)["parse_incomplete"]
    assert dict(unit.attributes)["syntax_recovered"]
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    expected = []
    for line_number, line in enumerate(lines, 1):
        for name in ("helper", "tail"):
            if line.startswith("void") and "after" not in line:
                continue
            start = 0
            while (column := line.find(f"{name}();", start)) != -1:
                expected.append(
                    (
                        "sample:after" if line.startswith("void after") else "sample:action",
                        f"sample:{name}",
                        line_number,
                        column,
                    )
                )
                start = column + len(name)
    assert [
        (edge.source_id, edge.target_id, edge.location.start_line, edge.location.start_col)
        for edge in calls
        if edge.location is not None
    ] == expected
    for edge in calls:
        if edge.source_id == "sample:action":
            assert edge.confidence == EdgeConfidence.UNCERTAIN
            assert dict(edge.attributes)["syntax_recovered"]
        else:
            assert edge.confidence == EdgeConfidence.CERTAIN
            assert not dict(edge.attributes).get("syntax_recovered")
    assert any(d.kind in {"syntax_error", "missing_syntax"} for d in graph.parse_diagnostics)
    recovery = [d for d in graph.parse_diagnostics if d.kind == "syntax_recovery"]
    assert [d.location for d in recovery] == [unit.location]
    with pytest.raises(ValueError, match="diagnostic"):
        parse_file(str(path), "c", strict=True)


@pytest.mark.parametrize("directive", [b"#if OPTION", b"#if (0)", b"#if 0L", b"#if 0 + OPTION"])
def test_nonliteral_conditions_keep_all_potential_code(directive: bytes) -> None:
    source = directive + b"\nfirst\n#elif OTHER\nsecond\n#else\nthird\n#endif\n"
    assert [token.group().strip() for token in _recovery._source_tokens(source)] == [
        directive,
        b"first",
        b"#elif OTHER",
        b"second",
        b"#else",
        b"third",
        b"#endif",
    ]


def test_disabled_masks_preserve_directives_offsets_and_continued_line_breaks() -> None:
    source = b"{\r\n#if 0\r\nignored {\r\n#define X \\\r\n }\r\n#endif\r\nBLOCK { live(); }\r\n}"
    disabled: list[tuple[int, int]] = []
    tokens = _recovery._source_tokens(source, disabled)
    assert _recovery._masked_source(source, disabled) == (
        b"{\r\n#if 0\r\n         \r\n           \r\n  \r\n#endif\r\nBLOCK { live(); }\r\n}"
    )
    assert [(token.group(), token.start()) for token in tokens if token.group() == b"BLOCK"] == [
        (b"BLOCK", source.index(b"BLOCK"))
    ]


@pytest.mark.parametrize(
    "opaque",
    [
        b"#if 0\nignored {\n#endif\n",
        b"/\\\n* ignored { */\n",
        b'const char *text = "ignored \\\r\n{";\n',
        b'#define TEXT "ignored /*"\n',
    ],
)
def test_failed_candidate_rolls_back_its_opaque_and_macro_masks(
    monkeypatch: pytest.MonkeyPatch,
    opaque: bytes,
) -> None:
    source = (
        b"#define BLOCK do\nvoid helper(void) {}\n"
        b"void action(void) {\n" + opaque + b"BLOCK { helper(); }\n}\n"
        b"void bad(void) {\n" + opaque + b"BLOCK { helper(); }\n}\n"
    )
    parser = Parser(get_language("c"))
    original = parser.parse(source)
    verify = _recovery._verified
    first, second = source.index(b"void action"), source.index(b"void bad")
    attempts = 0

    def reject_second_once(root: Node, candidates: dict[int, _recovery._Candidate]) -> set[int]:
        nonlocal attempts
        verified = verify(root, candidates)
        attempts += 1
        return verified - {second} if attempts == 1 else verified

    monkeypatch.setattr(_recovery, "_verified", reject_second_once)
    recovered, starts = _recovery.recover_bodies(source, original, parser)
    assert starts == {first}
    text = recovered.root_node.text
    assert text is not None
    assert b"ignored" not in text[:second]
    assert b"BLOCK {" not in text[:second]
    assert text[second:] == source[second:]


@pytest.mark.parametrize(
    "opaque",
    [
        b"#if 0\nignored {\n#endif\n",
        b"/\\\n* ignored { */\n",
        b'const char *text = "ignored \\\r\n{";\n',
        b'#define TEXT "ignored /*"\n',
    ],
)
def test_provisional_masks_are_discarded_without_a_block_macro_candidate(opaque: bytes) -> None:
    source = b"void action(void) {\n" + opaque + b"helper();\n}\n"
    parser = Parser(get_language("c"))
    original = parser.parse(source)
    recovered, starts = _recovery.recover_bodies(source, original, parser)
    assert starts == frozenset()
    assert recovered.root_node.text == source


@pytest.mark.parametrize(
    "source",
    [
        b"void clean(void) { struct Item { int field; }; }\nint broken = ;",
        b"void broken(void) { int value = ; }",
        b"while (1) { BLOCK { helper(); } }",
    ],
)
def test_discovery_excludes_clean_functions_and_other_syntax_errors(source: bytes) -> None:
    parser = Parser(get_language("c"))
    tree = parser.parse(source)
    assert tree.root_node.has_error
    assert not _recovery._recovery_functions(tree.root_node, source)


def test_candidates_reject_a_body_inside_disabled_tokens() -> None:
    source = b"#if 0\nvoid action(void) { BLOCK { helper(); } }\n#endif\n"
    parser = Parser(get_language("c"))
    tree = parser.parse(source)
    functions = _recovery._recovery_functions(tree.root_node, source)
    assert len(functions) == 1
    assert _recovery._candidates(functions, _recovery._source_tokens(source)) == {}


def test_identifier_in_a_declaration_is_not_masked_as_a_block_macro() -> None:
    source = b"void action(void) { struct Item { int field; }; BLOCK { helper(); } }\n"
    parser = Parser(get_language("c"))
    tree = parser.parse(source)
    candidates = _recovery._candidates(
        _recovery._recovery_functions(tree.root_node, source), _recovery._source_tokens(source)
    )
    assert len(candidates) == 1
    recovered = _recovery._reparse(source, parser, candidates)
    assert recovered.root_node.text == source.replace(b"BLOCK", b"     ")
