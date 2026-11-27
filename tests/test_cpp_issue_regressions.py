"""Regression cases from the C++ parser reports in issues #221–#227."""

from pathlib import Path

import pytest

from trailmark.models.edges import EdgeKind
from trailmark.models.nodes import NodeKind
from trailmark.parse import parse_directory, parse_file
from trailmark.query.api import QueryEngine


@pytest.mark.mutation_fast
@pytest.mark.parametrize("tag", ["class", "struct"])
def test_forward_declarations_do_not_compete_with_method_owners(tmp_path: Path, tag: str) -> None:
    (tmp_path / "engine.h").write_text(
        f"{tag} Engine {{ public: void Run(int a); bool Ready(int a); }};\n"
    )
    (tmp_path / "engine.cpp").write_text(
        '#include "engine.h"\n'
        "bool Engine::Ready(int a) { return a > 0; }\n"
        "void Engine::Run(int a) { if (Ready(a)) return; }\n"
    )
    (tmp_path / "other.h").write_text(f"{tag} Engine;\nvoid Use(Engine& e);\n")
    engine = QueryEngine.from_directory(str(tmp_path), language="cpp")
    assert [n["id"] for n in engine.callees_of(r"engine\.cpp:Engine.Run")] == [
        r"engine\.cpp:Engine.Ready"
    ]


@pytest.mark.mutation_fast
@pytest.mark.parametrize("forward_first", [False, True])
@pytest.mark.parametrize("tag", ["class", "struct"])
def test_forward_declaration_keeps_same_file_definition(
    tmp_path: Path, forward_first: bool, tag: str
) -> None:
    source = tmp_path / "engine.cpp"
    definition = f"{tag} Engine {{ public: void Run(); void Ready() {{}} }};\n"
    forward = f"{tag} Engine;\n"
    source.write_text(
        (forward + definition if forward_first else definition + forward)
        + "void Engine::Run() { Ready(); }\n"
    )
    graph = parse_file(str(source), "cpp")
    engine = QueryEngine.from_graph(graph)
    assert [n["id"] for n in engine.callees_of("engine:Engine.Run")] == ["engine:Engine.Ready"]
    assert (
        "Ready"
        in source.read_text().splitlines()[graph.nodes["engine:Engine"].location.start_line - 1]
    )


@pytest.mark.mutation_fast
@pytest.mark.parametrize("namespace", ["", "namespace lib {"])
def test_anonymous_namespace_method_owner_is_visible_in_enclosing_scope(
    tmp_path: Path, namespace: str
) -> None:
    source = tmp_path / "handler.cpp"
    source.write_text(
        namespace + "\nnamespace {\n"
        "class Handler { public: void Dispatch(int t); void OnRead(int t); };\n"
        "}\nvoid Handler::OnRead(int t) {}\n"
        "void Handler::Dispatch(int t) { if (t == 1) OnRead(t); }\n" + ("}\n" if namespace else "")
    )
    graph = parse_file(str(source), "cpp")
    prefix = "handler:lib." if namespace else "handler:"
    dispatch = graph.nodes[prefix + "Handler.Dispatch"]
    assert dispatch.kind == NodeKind.METHOD
    assert any(
        e.kind == EdgeKind.CONTAINS
        and e.source_id == prefix + "$anonymous.Handler"
        and e.target_id == dispatch.id
        for e in graph.edges
    )
    assert [n["id"] for n in QueryEngine.from_graph(graph).callees_of(dispatch.id)] == [
        prefix + "Handler.OnRead"
    ]


@pytest.mark.mutation_fast
def test_anonymous_namespace_owner_remains_ambiguous_with_a_named_owner(tmp_path: Path) -> None:
    source = tmp_path / "handler.cpp"
    source.write_text(
        "namespace { class Handler {}; }\nclass Handler {};\nvoid Handler::Dispatch() {}\n"
    )
    graph = parse_file(str(source), "cpp")
    assert graph.nodes["handler:Handler.Dispatch"].kind == NodeKind.FUNCTION


@pytest.mark.mutation_fast
def test_anonymous_namespace_owner_does_not_leak_between_files(tmp_path: Path) -> None:
    (tmp_path / "hidden.h").write_text("namespace { class Handler {}; }\n")
    (tmp_path / "handler.cpp").write_text("void Handler::Dispatch() { OnRead(); }\n")
    graph = parse_directory(str(tmp_path), "cpp")
    assert graph.nodes[r"handler\.cpp:Handler.Dispatch"].kind == NodeKind.FUNCTION
    assert not any(
        e.kind == EdgeKind.CONTAINS
        and e.source_id.endswith("$anonymous.Handler")
        and e.target_id == r"handler\.cpp:Handler.Dispatch"
        for e in graph.edges
    )


@pytest.mark.mutation_fast
def test_inline_and_out_of_line_overload_bodies_remain_distinct(tmp_path: Path) -> None:
    path = tmp_path / "box.cpp"
    path.write_text(
        "void first() {} void second() {}\n"
        "class Box { public: void Run() { first(); } void Run(int n); };\n"
        "void Box::Run(int n) { second(); }\n"
    )
    graph = parse_file(str(path), "cpp")
    overloads = [n for n in graph.nodes.values() if n.kind == NodeKind.METHOD and n.name == "Run"]
    assert len(overloads) == 2
    engine = QueryEngine.from_graph(graph)
    assert {
        (n.location.start_line, tuple(c["id"] for c in engine.callees_of(n.id))) for n in overloads
    } == {
        (2, ("box:first",)),
        (3, ("box:second",)),
    }


@pytest.mark.mutation_fast
@pytest.mark.parametrize("access", ["", "public ", "protected ", "private "])
def test_struct_base_classes_have_inheritance_edges(tmp_path: Path, access: str) -> None:
    source = tmp_path / "shapes.cpp"
    source.write_text(
        "struct Shape { virtual void Draw(int h) = 0; };\n"
        "class Circle : public Shape {};\n"
        f"struct Square final : {access}Shape {{}};\n"
        f"struct Triangle : {access}Shape {{}};\n"
    )
    graph = parse_file(str(source), "cpp")
    assert {(e.source_id, e.target_id) for e in graph.edges if e.kind == EdgeKind.INHERITS} == {
        ("shapes:Circle", "shapes:Shape"),
        ("shapes:Square", "shapes:Shape"),
        ("shapes:Triangle", "shapes:Shape"),
    }


@pytest.mark.mutation_fast
@pytest.mark.parametrize("language", ["cpp", "auto", "cpp,python"])
def test_cpp_directory_ids_are_stable_when_files_are_added(tmp_path: Path, language: str) -> None:
    source = tmp_path / "lib.cpp"
    source.write_text(
        "namespace lib {\n"
        "class Box { public: void Inline() {} void OutOfLine(); };\n"
        "void Box::OutOfLine() { Inline(); }\n"
        "namespace { class Hidden { public: void Inline() {} }; }\n}\n"
    )
    before = parse_directory(str(tmp_path), language)
    expected = {
        r"lib\.cpp:lib.Box.Inline",
        r"lib\.cpp:lib.Box.OutOfLine",
        r"lib\.cpp:lib.$anonymous.Hidden.Inline",
    }
    assert {n.id for n in before.nodes.values() if n.kind == NodeKind.METHOD} == expected
    for name in ("lib.h", "lib.cc", "lib.cpp.py"):
        (tmp_path / name).write_text("")
        after = parse_directory(str(tmp_path), language)
        assert {n.id for n in after.nodes.values() if n.location.file_path == str(source)} == set(
            before.nodes
        )
        assert [
            n["id"] for n in QueryEngine.from_graph(after).callees_of(r"lib\.cpp:lib.Box.OutOfLine")
        ] == [r"lib\.cpp:lib.Box.Inline"]


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    "source",
    [
        "#define REQUIRES(...) __attribute__((requires_capability(__VA_ARGS__)))\n"
        "class Widget { int mu; public: bool Read(int n) REQUIRES(!mu) { return n > 0; } };\n",
        "void Setup(int x) {\n#ifdef _WIN32\nif (x) {\n#else\nif (!x) {\n#endif\n}\n}\n",
        "#define APP_MAIN int main(int argc, char* argv[])\n"
        "void Init() {}\nAPP_MAIN\n{ Init(); return 0; }\n",
    ],
    ids=["trailing_annotation", "split_braces", "macro_signature"],
)
def test_cpp_preprocessing_limits_are_reported(tmp_path: Path, source: str) -> None:
    path = tmp_path / "app.cpp"
    path.write_text(source)
    graph = parse_file(str(path), "cpp")
    assert graph.parse_diagnostics
    with pytest.raises(ValueError, match="Incomplete source analysis"):
        parse_file(str(path), "cpp", strict=True)
