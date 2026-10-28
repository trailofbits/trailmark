"""Review regressions for potentially active arms and native exception blocks."""

from pathlib import Path

import pytest

from trailmark.models import EdgeConfidence, EdgeKind, NodeKind
from trailmark.parse import parse_file
from trailmark.parsers.c import _recovery


@pytest.mark.parametrize("branch", [b"elifdef", b"elifndef"])
@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
@pytest.mark.parametrize("enclosed", [False, True])
def test_c23_conditional_tokens_resume_only_outside_disabled_parents(
    branch: bytes, newline: bytes, enclosed: bool
) -> None:
    source = b"#if 0\nskip\n#" + branch + b" FEATURE\nlive\n#endif\nend"
    expected = [b"#if 0", b"#" + branch + b" FEATURE", b"live", b"#endif", b"end"]
    if enclosed:
        source = b"#if 0\n" + source + b"\n#endif\nafter"
        expected = [b"#if 0", b"#if 0", b"#" + branch + b" FEATURE", b"#endif", b"#endif", b"after"]
    assert [
        token.group().rstrip(b"\r\n")
        for token in _recovery._source_tokens(source.replace(b"\n", newline))
    ] == expected


@pytest.mark.parametrize("branch", [b"elifdef", b"elifndef"])
def test_c23_alternative_cannot_hide_a_brace_depth_change(branch: bytes) -> None:
    source = b"{\n#if OPTION\n{\n#" + branch + b" FEATURE\n}\n#endif\n}"
    assert _recovery._physical_end(_recovery._source_tokens(source), 0) is None


@pytest.mark.parametrize("branch", ["elifdef", "elifndef"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_c23_potentially_active_calls_survive_disabled_prose(
    tmp_path: Path, branch: str, newline: str
) -> None:
    path = tmp_path / "sample.c"
    path.write_bytes(
        (
            "#define BLOCK if (1)\nvoid helper(void) {}\nvoid action(void) { BLOCK {\n"
            "#if 0\ndisabled prose {\n#" + branch + " FEATURE\n"
            "helper(); helper();\n#endif\n} }\nvoid after(void) { helper(); }\n"
        )
        .replace("\n", newline)
        .encode()
    )
    graph = parse_file(str(path), "c")
    assert {unit.id for unit in graph.nodes.values() if unit.kind == NodeKind.FUNCTION} == {
        "sample:helper",
        "sample:action",
        "sample:after",
    }
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    assert [(edge.source_id, edge.target_id, edge.confidence) for edge in calls] == [
        ("sample:action", "sample:helper", EdgeConfidence.UNCERTAIN),
        ("sample:action", "sample:helper", EdgeConfidence.UNCERTAIN),
        ("sample:after", "sample:helper", EdgeConfidence.CERTAIN),
    ]
    assert [
        (edge.location.start_line, edge.location.start_col, edge.location.end_col)
        for edge in calls
        if edge.location
    ] == [(7, 0, 8), (7, 10, 18), (10, 19, 27)]
    assert dict(graph.nodes["sample:action"].attributes)["syntax_recovered"] is True
    assert graph.parse_diagnostics


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("macro", [False, True], ids=["native-only", "with-block-macro"])
def test_native_exception_blocks_are_not_recovery_macros(
    tmp_path: Path, newline: str, macro: bool
) -> None:
    statement = "BLOCK { helper(); }" if macro else "helper();"
    path = tmp_path / "sample.c"
    path.write_bytes(
        (
            "#define BLOCK if (1)\nvoid helper(void) {}\nvoid action(void) {\n"
            "#define LOCAL(x) \\\n (x)\n"
            "__try { " + statement + " } __finally { helper(); }\n}\n"
            "void after(void) { helper(); }\n"
        )
        .replace("\n", newline)
        .encode()
    )
    graph = parse_file(str(path), "c")
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    confidence = EdgeConfidence.UNCERTAIN if macro else EdgeConfidence.CERTAIN
    assert [(edge.source_id, edge.target_id, edge.confidence) for edge in calls] == [
        ("sample:action", "sample:helper", confidence),
        ("sample:action", "sample:helper", confidence),
        ("sample:after", "sample:helper", EdgeConfidence.CERTAIN),
    ]
    assert bool(dict(graph.nodes["sample:action"].attributes).get("syntax_recovered")) == macro
    assert bool(graph.parse_diagnostics) == macro
