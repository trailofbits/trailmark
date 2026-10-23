"""C-family static visibility and deterministic include traversal."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from tree_sitter import Parser
from tree_sitter_language_pack import get_language

from trailmark.models import EdgeConfidence, EdgeKind
from trailmark.parse import parse_directory, parse_file
from trailmark.parsers.c._headers import HeaderSources


@pytest.mark.parametrize(
    ("language", "files", "source", "target"),
    [
        (
            "cpp",
            {
                "a.cpp": '#include "b.hpp"\nint run(){return helper();}',
                "b.hpp": "static inline int helper(){return 1;}",
            },
            "a:run",
            "b:helper",
        ),
        (
            "c,cpp",
            {
                "a.cpp": '#include "b.h"\nint run(){return helper();}',
                "b.h": "static inline int helper(){return 1;}",
            },
            "a:run",
            "b:helper",
        ),
        (
            "c",
            {"a.h": "int run(){return helper();}", "b.h": "static inline int helper(){return 1;}"},
            "a:run",
            "b:helper",
        ),
        (
            "c",
            {
                "a.c": '#include "mid.h"\nstatic int helper(){return 1;}',
                "mid.h": '#include "b.h"',
                "b.h": "int run(){return helper();}",
            },
            "b:run",
            "a:helper",
        ),
        (
            "cpp",
            {
                "a.cpp": '#include "mid.hpp"\nstatic int helper(){return 1;}',
                "mid.hpp": '#include "b.hpp"',
                "b.hpp": "int run(){return helper();}",
            },
            "b:run",
            "a:helper",
        ),
    ],
)
def test_static_visibility(
    tmp_path: Path, language: str, files: dict[str, str], source: str, target: str
) -> None:
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    graph = parse_directory(str(tmp_path), language)
    edge = next(e for e in graph.edges if e.source_id == source and e.kind == EdgeKind.CALLS)
    assert edge.target_id == target
    assert edge.confidence == EdgeConfidence.INFERRED


@pytest.mark.parametrize("extension", ["c", "cpp"])
def test_unrelated_source_statics_are_private(tmp_path: Path, extension: str) -> None:
    (tmp_path / f"a.{extension}").write_text("int run(){return helper();}")
    (tmp_path / f"b.{extension}").write_text("static int helper(){return 1;}")
    graph = parse_directory(str(tmp_path), "auto")
    edge = next(e for e in graph.edges if e.source_id == "a:run" and e.kind == EdgeKind.CALLS)
    assert edge.target_id.startswith("proxy.")


def test_duplicate_header_statics_stay_ambiguous(tmp_path: Path) -> None:
    (tmp_path / "a.c").write_text("int run(){return helper();}")
    for name in ("b.h", "c.h"):
        (tmp_path / name).write_text("static int helper(){return 1;}")
    graph = parse_directory(str(tmp_path), "c")
    edge = next(e for e in graph.edges if e.source_id == "a:run" and e.kind == EdgeKind.CALLS)
    assert edge.target_id.startswith("proxy.")
    assert edge.confidence == EdgeConfidence.UNCERTAIN


def test_include_closure_uses_shallowest_path_and_strict_cap(tmp_path: Path) -> None:
    (tmp_path / "a.c").write_text('#include "deep1.h"\n#include "mid.h"')
    for i in range(1, 17):
        target = f"deep{i + 1}.h" if i < 16 else "x.h"
        (tmp_path / f"deep{i}.h").write_text(f'#include "{target}"')
    (tmp_path / "mid.h").write_text('#include "x.h"')
    (tmp_path / "x.h").write_text('#include "y.h"')
    (tmp_path / "y.h").write_text("int helper();")
    headers = HeaderSources(tmp_path, Parser(get_language("c")))
    assert str(tmp_path / "y.h") in headers.included_files(str(tmp_path / "a.c"))
    (tmp_path / "wide.c").write_text("\n".join(f'#include "h{i}.h"' for i in range(140)))
    for i in range(140):
        (tmp_path / f"h{i}.h").touch()
    assert len(headers.included_files(str(tmp_path / "wide.c"))) == 128
    script = """
import sys
from pathlib import Path
from tree_sitter import Parser
from tree_sitter_language_pack import get_language
from trailmark.parsers.c._headers import HeaderSources
root=Path(sys.argv[1])
headers=HeaderSources(root, Parser(get_language("c")))
assert str(root / "y.h") in headers.included_files(str(root / "a.c"))
assert len(headers.included_files(str(root / "wide.c"))) == 128
"""
    for seed in range(5):
        subprocess.run(  # noqa: S603
            [sys.executable, "-c", script, str(tmp_path)],
            env=os.environ | {"PYTHONHASHSEED": str(seed)},
            check=True,
            capture_output=True,
            text=True,
        )


def test_later_declarator_does_not_shadow_earlier_initializer(tmp_path: Path) -> None:
    path = tmp_path / "a.c"
    path.write_text("int fib(int x){return x;} int run(){int a=1,b=fib(2),fib=3;return b;}")
    graph = parse_file(str(path))
    edge = next(e for e in graph.edges if e.source_id == "a:run" and e.kind == EdgeKind.CALLS)
    assert edge.target_id == "a:fib"
    assert edge.confidence == EdgeConfidence.CERTAIN


@pytest.mark.parametrize("comment", ["/* c */", "// c\n"])
def test_comment_before_macro_block_does_not_disable_recovery(tmp_path: Path, comment: str) -> None:
    from tests.test_c_source_recovery import BLOCKS

    path = tmp_path / "a.c"
    path.write_text(BLOCKS.replace("BLOCK_BEGIN {", f"BLOCK_BEGIN {comment} {{"))
    graph = parse_file(str(path))
    unit = graph.nodes["a:action"]
    assert dict(unit.attributes)["syntax_recovered"]
    assert {
        e.target_id for e in graph.edges if e.source_id == unit.id and e.kind == EdgeKind.CALLS
    } >= {"a:first", "a:second", "a:third", "a:fourth", "a:final_step"}


def test_anonymous_namespace_in_header_does_not_leak(tmp_path: Path) -> None:
    (tmp_path / "a.cpp").write_text("int run(){return helper();}")
    (tmp_path / "b.hpp").write_text("namespace { int helper(){return 1;} }")
    graph = parse_directory(str(tmp_path), "cpp")
    edge = next(e for e in graph.edges if e.source_id == "a:run" and e.kind == EdgeKind.CALLS)
    assert edge.target_id.startswith("proxy.")


def test_cpp_source_includes_survive_single_file_graph_merge(tmp_path: Path) -> None:
    from trailmark.parsers._common import link_cross_file_calls
    from trailmark.parsers.cpp.parser import CppParser

    (tmp_path / "a.cpp").write_text('#include "b.cpp"\nint run(){return helper();}')
    (tmp_path / "b.cpp").write_text("static int helper(){return 1;}")
    parser = CppParser()
    graph = parser.parse_file(str(tmp_path / "a.cpp"))
    graph.merge(parser.parse_file(str(tmp_path / "b.cpp")))
    link_cross_file_calls(graph)
    edge = next(e for e in graph.edges if e.source_id == "a:run" and e.kind == EdgeKind.CALLS)
    assert edge.target_id == "b:helper"
    assert edge.confidence == EdgeConfidence.INFERRED


def test_same_file_anonymous_namespace_is_visible(tmp_path: Path) -> None:
    (tmp_path / "a.cpp").write_text(
        "namespace { int helper(){return 1;} } int run(){return helper();}"
    )
    graph = parse_directory(str(tmp_path), "cpp")
    edge = next(e for e in graph.edges if e.source_id == "a:run" and e.kind == EdgeKind.CALLS)
    assert edge.target_id == "a:$anonymous.helper"
    assert edge.confidence == EdgeConfidence.INFERRED


def test_dangling_call_source_is_not_cross_linked(tmp_path: Path) -> None:
    from trailmark.models import CodeEdge
    from trailmark.parsers._common import link_cross_file_calls
    from trailmark.parsers.cpp.parser import CppParser

    (tmp_path / "a.cpp").write_text("int helper(){return 1;}")
    graph = CppParser().parse_file(str(tmp_path / "a.cpp"))
    graph.edges.append(CodeEdge("missing:caller", "other:helper", EdgeKind.CALLS))
    link_cross_file_calls(graph)
    assert graph.edges[-1].target_id == "other:helper"
