"""Tests for parser utilities shared across language implementations."""

from __future__ import annotations

from pathlib import Path

import pytest

from trailmark.models.edges import CodeEdge, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit, NodeKind, SourceLocation
from trailmark.parsers._common import module_id_from_path, parse_directory


def _fake_parse_file(file_path: str) -> CodeGraph:
    """Build a tiny graph using the shared module ID helper."""
    module_id = module_id_from_path(file_path)
    function_id = f"{module_id}:helper"
    location = SourceLocation(file_path=file_path, start_line=1, end_line=1)
    return CodeGraph(
        nodes={
            module_id: CodeUnit(
                id=module_id,
                name=module_id,
                kind=NodeKind.MODULE,
                location=location,
            ),
            function_id: CodeUnit(
                id=function_id,
                name="helper",
                kind=NodeKind.FUNCTION,
                location=location,
            ),
        },
        edges=[
            CodeEdge(
                source_id=module_id,
                target_id=function_id,
                kind=EdgeKind.CONTAINS,
            )
        ],
        language="test",
        root_path=file_path,
    )


def test_module_id_from_path_preserves_single_file_stem_behavior(tmp_path: Path) -> None:
    file_path = tmp_path / "src" / "compat.py"
    file_path.parent.mkdir()
    file_path.write_text("")

    assert module_id_from_path(str(file_path)) == "compat"


def test_parse_directory_uses_root_relative_module_ids(tmp_path: Path) -> None:
    src = tmp_path / "src"
    tests = tmp_path / "tests"
    src.mkdir()
    tests.mkdir()
    (src / "compat.py").write_text("")
    (tests / "compat.py").write_text("")

    graph = parse_directory(
        _fake_parse_file,
        language="test",
        dir_path=str(tmp_path),
        extensions=(".py",),
    )

    assert set(graph.nodes) == {
        "src.compat",
        "src.compat:helper",
        "tests.compat",
        "tests.compat:helper",
    }
    assert {(edge.source_id, edge.target_id) for edge in graph.edges} == {
        ("src.compat", "src.compat:helper"),
        ("tests.compat", "tests.compat:helper"),
    }


def test_parse_directory_escapes_dotted_path_components(tmp_path: Path) -> None:
    dotted_dir = tmp_path / "a.b"
    dotted_file_parent = tmp_path / "a"
    dotted_dir.mkdir()
    dotted_file_parent.mkdir()
    (dotted_dir / "c.py").write_text("")
    (dotted_file_parent / "b.c.py").write_text("")

    graph = parse_directory(
        _fake_parse_file,
        language="test",
        dir_path=str(tmp_path),
        extensions=(".py",),
    )

    assert set(graph.nodes) == {
        r"a\.b.c",
        r"a\.b.c:helper",
        r"a.b\.c",
        r"a.b\.c:helper",
    }


def test_parse_directory_uses_package_path_for_init_files(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    subpkg = pkg / "subpkg"
    subpkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (subpkg / "__init__.py").write_text("")

    graph = parse_directory(
        _fake_parse_file,
        language="test",
        dir_path=str(tmp_path),
        extensions=(".py",),
    )

    assert set(graph.nodes) == {
        "pkg",
        "pkg:helper",
        "pkg.subpkg",
        "pkg.subpkg:helper",
    }


def test_parse_directory_uses_lexical_path_for_symlinked_files(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    src = root / "src"
    tests = root / "tests"
    outside.mkdir()
    src.mkdir(parents=True)
    tests.mkdir()
    (outside / "compat.py").write_text("")
    (tests / "compat.py").write_text("")
    try:
        (src / "compat.py").symlink_to(outside / "compat.py")
    except OSError:
        pytest.skip("symlink creation is not supported on this platform")

    graph = parse_directory(
        _fake_parse_file,
        language="test",
        dir_path=str(root),
        extensions=(".py",),
    )

    assert set(graph.nodes) == {
        "src.compat",
        "src.compat:helper",
        "tests.compat",
        "tests.compat:helper",
    }
