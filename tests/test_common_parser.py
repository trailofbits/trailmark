"""Tests for parser utilities shared across language implementations."""

from __future__ import annotations

from pathlib import Path

import pytest

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
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

    assert "src.compat" in graph.nodes
    assert "tests.compat" in graph.nodes


# --- Cross-file linker tests ---


def _cross_file_parse(file_path: str) -> CodeGraph:
    """Build a graph with cross-file calls for linker testing."""
    module_id = module_id_from_path(file_path)
    location = SourceLocation(file_path=file_path, start_line=1, end_line=1)
    nodes: dict[str, CodeUnit] = {
        module_id: CodeUnit(
            id=module_id,
            name=module_id,
            kind=NodeKind.MODULE,
            location=location,
        ),
    }
    edges: list[CodeEdge] = []

    stem = Path(file_path).stem
    if stem == "caller":
        func_id = f"{module_id}:call_it"
        nodes[func_id] = CodeUnit(
            id=func_id,
            name="call_it",
            kind=NodeKind.FUNCTION,
            location=location,
        )
        edges.append(
            CodeEdge(
                source_id=func_id,
                target_id=f"{module_id}:do_work",
                kind=EdgeKind.CALLS,
            )
        )
    elif stem == "worker":
        func_id = f"{module_id}:do_work"
        nodes[func_id] = CodeUnit(
            id=func_id,
            name="do_work",
            kind=NodeKind.FUNCTION,
            location=location,
        )
    elif stem == "ambiguous1":
        func_id = f"{module_id}:shared_name"
        nodes[func_id] = CodeUnit(
            id=func_id,
            name="shared_name",
            kind=NodeKind.FUNCTION,
            location=location,
        )
    elif stem == "ambiguous2":
        func_id = f"{module_id}:shared_name"
        nodes[func_id] = CodeUnit(
            id=func_id,
            name="shared_name",
            kind=NodeKind.FUNCTION,
            location=location,
        )
        caller_id = f"{module_id}:invoke_shared"
        nodes[caller_id] = CodeUnit(
            id=caller_id,
            name="invoke_shared",
            kind=NodeKind.FUNCTION,
            location=location,
        )
        edges.append(
            CodeEdge(
                source_id=caller_id,
                target_id=f"{module_id}:shared_name",
                kind=EdgeKind.CALLS,
            )
        )
    elif stem == "ambiguous_caller":
        caller_id = f"{module_id}:invoke_shared"
        nodes[caller_id] = CodeUnit(
            id=caller_id,
            name="invoke_shared",
            kind=NodeKind.FUNCTION,
            location=location,
        )
        edges.append(
            CodeEdge(
                source_id=caller_id,
                target_id=f"{module_id}:shared_name",
                kind=EdgeKind.CALLS,
            )
        )

    return CodeGraph(nodes=nodes, edges=edges, language="test", root_path=file_path)


def test_cross_file_linker_resolves_unique_target(tmp_path: Path) -> None:
    """A dangling call to do_work in caller.c resolves to worker.c's definition."""
    (tmp_path / "caller.c").write_text("")
    (tmp_path / "worker.c").write_text("")

    graph = parse_directory(
        _cross_file_parse,
        language="test",
        dir_path=str(tmp_path),
        extensions=(".c",),
    )

    call_edges = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(call_edges) == 1
    assert call_edges[0].source_id == "caller:call_it"
    assert call_edges[0].target_id == "worker:do_work"
    assert call_edges[0].confidence == EdgeConfidence.CERTAIN


def test_cross_file_linker_keeps_same_module_when_target_exists(tmp_path: Path) -> None:
    """When the target exists in the caller's own module, leave the edge alone."""
    (tmp_path / "ambiguous1.c").write_text("")
    (tmp_path / "ambiguous2.c").write_text("")

    graph = parse_directory(
        _cross_file_parse,
        language="test",
        dir_path=str(tmp_path),
        extensions=(".c",),
    )

    call_edges = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(call_edges) == 1
    assert call_edges[0].source_id == "ambiguous2:invoke_shared"
    # Target already exists in the same module, so linker leaves it.
    assert call_edges[0].target_id == "ambiguous2:shared_name"
    assert call_edges[0].confidence == EdgeConfidence.CERTAIN


def test_cross_file_linker_leaves_unresolvable_calls(tmp_path: Path) -> None:
    """Calls to functions not defined anywhere are materialized as proxies."""
    (tmp_path / "caller.c").write_text("")

    graph = parse_directory(
        _cross_file_parse,
        language="test",
        dir_path=str(tmp_path),
        extensions=(".c",),
    )

    call_edges = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(call_edges) == 1
    assert call_edges[0].target_id == "proxy.unresolved:caller:do_work"
    assert "proxy.unresolved:caller:do_work" in graph.nodes


def test_cross_file_linker_keeps_ambiguous_cross_file_calls_unresolved(
    tmp_path: Path,
) -> None:
    """Multiple cross-file definitions should not produce an arbitrary concrete target."""
    (tmp_path / "ambiguous1.c").write_text("")
    (tmp_path / "ambiguous2.c").write_text("")
    (tmp_path / "ambiguous_caller.c").write_text("")

    graph = parse_directory(
        _cross_file_parse,
        language="test",
        dir_path=str(tmp_path),
        extensions=(".c",),
    )

    call_edges = [
        e
        for e in graph.edges
        if e.kind == EdgeKind.CALLS and e.source_id == "ambiguous_caller:invoke_shared"
    ]
    assert len(call_edges) == 1
    assert call_edges[0].target_id == "proxy.unresolved:ambiguous_caller:shared_name"
    assert call_edges[0].confidence == EdgeConfidence.UNCERTAIN
