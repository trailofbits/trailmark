"""Tests for parser utilities shared across language implementations."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest
from tree_sitter import Range
from tree_sitter_language_pack import get_parser

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import (
    BranchInfo,
    CodeUnit,
    NodeKind,
    NodeOrigin,
    SourceLocation,
    TypeRef,
)
from trailmark.parsers._common import (
    _generic_parameter_block,
    _parse_type_parameter,
    _split_top_level,
    add_module_node,
    collect_body_info,
    directory_sources,
    extract_call_name,
    link_cross_file_calls,
    link_type_relationships,
    module_id_from_path,
    parse_directory,
    walk_source_files,
)


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    "header,expected",
    [
        ("public <T extends Base> T run() {}", "T extends Base"),
        ("<T>(value: T) => value", "T"),
        ("[T](value) => value", "T"),
        ("public <T T run() {}", None),
        ("class Box<T> {}", "T"),
        ("fn run<T>(value: T) {}", "T"),
        ("public T run<T>(T value) {}", "T"),
    ],
)
def test_generic_header_scanning_preserves_parameter_text(
    header: str, expected: str | None
) -> None:
    assert _generic_parameter_block(header) == expected


@pytest.mark.mutation_fast
def test_in_memory_module_fingerprints_do_not_require_a_source_file(tmp_path: Path) -> None:
    source = b"# comment\n"
    root = get_parser("powershell").parse(source).root_node
    path = str(tmp_path / "virtual.ps1")
    graph = CodeGraph(language="powershell")
    assert root.has_error
    add_module_node(root, path, "virtual", graph)
    assert graph.source_digests == {path: sha256(source).hexdigest()}
    assert graph.parse_diagnostics == []


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    "language,body,has_diagnostics",
    [("powershell", b"# comment\n", False), ("cpp", b"// first\n??? // second\n", True)],
)
def test_included_ranges_keep_full_fingerprints_and_relative_comment_offsets(
    tmp_path: Path, language: str, body: bytes, has_diagnostics: bool
) -> None:
    prefix = b"ignored\n"
    source = prefix + body
    path = tmp_path / "fragment.source"
    path.write_bytes(source)
    parser = get_parser(language)
    parser.included_ranges = [Range((1, 0), (source.count(b"\n"), 0), len(prefix), len(source))]
    root = parser.parse(source).root_node
    assert root.start_byte == len(prefix)
    assert root.has_error
    graph = CodeGraph(language=language)
    add_module_node(root, str(path), "fragment", graph)
    assert graph.source_digests == {str(path): sha256(source).hexdigest()}
    assert bool(graph.parse_diagnostics) is has_diagnostics


@pytest.mark.mutation_fast
def test_boolean_branch_collection_preserves_the_operator_kind() -> None:
    root = get_parser("python").parse(b"def choose(a, b):\n    return a and b\n").root_node
    branches: list[BranchInfo] = []
    collect_body_info(
        root, "branch.py", frozenset({"boolean_operator"}), "call", frozenset(), branches, [], []
    )
    assert [branch.condition for branch in branches] == ["boolean_operator"]
    assert branches[0].location.start_line == 2


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    "language,source,call,expected",
    [
        ("c", b"\nvoid run(void) { factory()(); }\n", b"factory()()", "$returned@2_17_29"),
        ("python", b"\ndef run():\n    table[0]()\n", b"table[0]()", "$indirect@3_4_26"),
        ("c", b"\nvoid run(void) { (*callback)(); }\n", b"(*callback)()", "$indirect@2_17_31"),
        ("javascript", b"(function named(){})()", b"(function named(){})()", "named"),
    ],
)
def test_compound_callees_keep_their_own_identity_and_coordinates(
    language: str, source: bytes, call: bytes, expected: str
) -> None:
    pending = [get_parser(language).parse(source).root_node]
    while pending:
        node = pending.pop()
        if node.type in {"call", "call_expression"} and node.text == call:
            assert extract_call_name(node) == expected
            return
        pending.extend(node.named_children)
    pytest.fail(f"Fixture has no call expression for {call!r}")


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    "text,expected",
    [
        ("A(B, C), D", ["A(B, C)", "D"]),
        ("A<B, C>, D[E, F]", ["A<B, C>", "D[E, F]"]),
        ("A), B", ["A)", "B"]),
        ("A], B>, C", ["A]", "B>", "C"]),
    ],
)
def test_type_parameter_splitting_handles_nested_and_unmatched_delimiters(
    text: str, expected: list[str]
) -> None:
    assert _split_top_level(text) == expected


@pytest.mark.mutation_fast
def test_type_parameter_bounds_survive_defaults_and_unhandled_modifiers() -> None:
    parameter = _parse_type_parameter("T any")
    assert parameter is not None
    assert parameter.name == "T"
    assert parameter.constraints == (TypeRef("any"),)
    modified = _parse_type_parameter("reified T : Comparable<T>")
    assert modified is not None
    assert modified.constraints == (TypeRef("Comparable<T>"),)


@pytest.mark.mutation_fast
@pytest.mark.parametrize("operand,expected", [("new Bad()", "Bad"), ("problem", "problem")])
def test_legacy_body_collection_preserves_throw_operands(operand: str, expected: str) -> None:
    root = get_parser("javascript").parse(f"throw {operand};".encode()).root_node
    exceptions: list[TypeRef] = []
    collect_body_info(
        root,
        "sample.js",
        frozenset(),
        "call_expression",
        frozenset({"throw_statement"}),
        [],
        exceptions,
        [],
    )
    assert exceptions == [TypeRef(expected)]


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    "language,source",
    [
        ("javascript", "(()=>{})()"),
        ("swift", "func run(){ ({})() }"),
    ],
)
def test_anonymous_call_without_a_name_returns_empty_string(language: str, source: str) -> None:
    root = get_parser(language).parse(source.encode()).root_node
    pending = [root]
    while pending:
        node = pending.pop()
        if node.type == "call_expression":
            assert extract_call_name(node) == ""
            break
        pending.extend(node.named_children)
    else:
        pytest.fail("Fixture must contain an invocation")


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


@pytest.mark.mutation_fast
def test_module_id_from_path_preserves_single_file_stem_behavior(tmp_path: Path) -> None:
    file_path = tmp_path / "src" / "compat.py"
    file_path.parent.mkdir()
    file_path.write_text("")

    assert module_id_from_path(str(file_path)) == "compat"


@pytest.mark.mutation_fast
@pytest.mark.parametrize("language,extension", [("c", "c"), ("cpp", "cpp"), ("objc", "m")])
def test_c_family_directory_ids_do_not_change_when_a_header_is_added(
    tmp_path: Path, language: str, extension: str
) -> None:
    from trailmark.parse import parse_directory as parse_sources

    source = tmp_path / f"lib.{extension}"
    source.write_text("void Run(void) {}\n")
    before = parse_sources(str(tmp_path), language)
    expected = rf"lib\.{extension}:Run"
    assert expected in before.nodes
    (tmp_path / "lib.h").write_text("")
    after = parse_sources(str(tmp_path), language)
    assert {n.id for n in after.nodes.values() if n.location.file_path == str(source)} == set(
        before.nodes
    )


@pytest.mark.mutation_fast
def test_directory_recursion_failure_retains_diagnostics_and_later_files(tmp_path: Path) -> None:
    deep = tmp_path / "a_deep.py"
    deep.write_text("deep source")
    (tmp_path / "b_ok.py").write_text("valid source")

    def parse(path: str) -> CodeGraph:
        if Path(path) == deep:
            raise RecursionError("bounded test failure")
        return _fake_parse_file(path)

    graph = parse_directory(parse, "test", str(tmp_path), (".py",))
    assert graph.source_file_counts == {"test": 2}
    assert set(graph.nodes) == {"a_deep", "b_ok", "b_ok:helper"}
    assert graph.nodes["a_deep"].kind == NodeKind.MODULE
    (diagnostic,) = graph.parse_diagnostics
    assert (diagnostic.language, diagnostic.grammar, diagnostic.kind) == (
        "test",
        "test",
        "unsupported_syntax",
    )
    assert diagnostic.location == SourceLocation(str(deep), 1, 1)
    assert "recursion limit" in diagnostic.message


@pytest.mark.mutation_fast
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


def test_parse_directory_matches_extensions_case_insensitively(tmp_path: Path) -> None:
    (tmp_path / "upper.PY").write_text("")
    (tmp_path / "mixed.pY").write_text("")
    (tmp_path / "ignored.txt").write_text("")

    graph = parse_directory(
        _fake_parse_file,
        language="test",
        dir_path=str(tmp_path),
        extensions=(".py",),
    )

    assert set(graph.nodes) == {"upper", "upper:helper", "mixed", "mixed:helper"}


@pytest.mark.mutation_fast
def test_legacy_directory_walk_skips_hidden_and_dependency_directories(tmp_path: Path) -> None:
    for name in (".hidden", "node_modules", "vendor", "visible"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "code.py").write_text("")
    graph = parse_directory(_fake_parse_file, "test", str(tmp_path), (".py",))
    assert set(graph.nodes) == {"visible.code", "visible.code:helper"}
    assert graph.source_file_counts == {"test": 1}


@pytest.mark.mutation_fast
def test_planned_walk_is_limited_to_its_own_root(tmp_path: Path) -> None:
    selected = tmp_path / "selected.py"
    ignored = tmp_path / "ignored.py"
    nested = tmp_path / "nested"
    nested.mkdir()
    local = nested / "local.py"
    for path in (selected, ignored, local):
        path.write_text("")
    with directory_sources(str(tmp_path), {str(selected): "test"}):
        assert list(walk_source_files(str(tmp_path), (".py",))) == [str(selected)]
        assert list(walk_source_files(str(nested), (".py",))) == [str(local)]


@pytest.mark.mutation_fast
@pytest.mark.parametrize("origin", list(NodeOrigin))
def test_type_name_inference_only_indexes_source_declarations(origin: NodeOrigin) -> None:
    location = SourceLocation("types.java", 1, 1)
    base = CodeUnit("types:Base", "Base", NodeKind.CLASS, location, origin=origin)
    child = CodeUnit("app:Child", "Child", NodeKind.CLASS, SourceLocation("app.java", 1, 1))
    edge = CodeEdge(child.id, "Base", EdgeKind.INHERITS)
    graph = CodeGraph(nodes={base.id: base, child.id: child}, edges=[edge])
    link_type_relationships(graph)
    assert graph.edges[0].target_id == (base.id if origin == NodeOrigin.SOURCE else "Base")
    assert graph.edges[0].confidence == (
        EdgeConfidence.INFERRED if origin == NodeOrigin.SOURCE else EdgeConfidence.CERTAIN
    )


@pytest.mark.mutation_fast
def test_parse_directory_does_not_traverse_directory_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "local.py").write_text("")
    (outside / "external.py").write_text("")
    try:
        (root / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not supported on this platform")

    graph = parse_directory(
        _fake_parse_file,
        language="test",
        dir_path=str(root),
        extensions=(".py",),
    )

    assert set(graph.nodes) == {"local", "local:helper"}


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


@pytest.mark.mutation_fast
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


@pytest.mark.mutation_fast
@pytest.mark.parametrize("kind", list(EdgeKind))
@pytest.mark.parametrize("literal_callable", [None, False, True])
def test_cross_file_linking_only_rebinds_calls_and_literal_callable_references(
    tmp_path: Path, kind: EdgeKind, literal_callable: bool | None
) -> None:
    """Other source relationships retain their own resolution policy."""
    caller = CodeUnit(
        "caller:install",
        "install",
        NodeKind.FUNCTION,
        SourceLocation(str(tmp_path / "caller.c"), 1, 1),
    )
    target = CodeUnit(
        "library:helper",
        "helper",
        NodeKind.FUNCTION,
        SourceLocation(str(tmp_path / "library.c"), 1, 1),
    )
    attributes = () if literal_callable is None else (("literal_callable", literal_callable),)
    edge = CodeEdge(caller.id, "helper", kind, location=caller.location, attributes=attributes)
    graph = CodeGraph(
        nodes={caller.id: caller, target.id: target},
        edges=[edge],
        language="c",
        root_path=str(tmp_path),
    )

    link_cross_file_calls(graph)

    should_link = kind == EdgeKind.CALLS or (
        kind == EdgeKind.REFERENCES and literal_callable is True
    )
    assert len(graph.edges) == 1
    actual = graph.edges[0]
    assert actual.target_id == (target.id if should_link else edge.target_id)
    assert actual.confidence == (EdgeConfidence.INFERRED if should_link else edge.confidence)
    assert (actual.source_id, actual.kind, actual.location, actual.attributes) == (
        edge.source_id,
        edge.kind,
        edge.location,
        edge.attributes,
    )


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
    assert call_edges[0].confidence == EdgeConfidence.INFERRED


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


@pytest.mark.mutation_fast
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


@pytest.mark.mutation_fast
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
