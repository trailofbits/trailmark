from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from trailmark.models.edges import EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.parse import parse_directory, parse_file

FIXTURE = Path(__file__).parent / "fixtures/kat/dafny/taxonomy.dfy"


def test_computational_and_specification_contexts() -> None:
    graph = parse_file(str(FIXTURE), strict=True)
    names = {n.id: n.name for n in graph.nodes.values()}
    calls = {
        (names[e.source_id], names[e.target_id]) for e in graph.edges if e.kind == EdgeKind.CALLS
    }
    assert calls == {("Normalize", "Clamp")}
    refs = {
        (names[e.source_id], names[e.target_id])
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES
    }
    assert {
        ("ClampWithin", "Within"),
        ("ClampWithin", "Clamp"),
        ("Normalize", "ClampWithin"),
    } <= refs
    assert not any(a == "Apply" for a, _ in refs)
    trusted = next(n for n in graph.nodes.values() if n.name == "Trusted")
    assert dict(trusted.attributes)["assumed"] is True


def test_named_ghost_fields_and_allocation_arguments(tmp_path: Path) -> None:
    path = tmp_path / "Fields.dfy"
    path.write_text(
        "datatype Envelope = Envelope(nameonly ghost witness: int)\n"
        "class Counter { constructor(n: int) {} }\n"
        "method Make() returns (c: Counter) { c := new Counter(n := 0); }\n"
    )
    graph = parse_file(str(path), strict=True)
    witness = next(n for n in graph.nodes.values() if n.name == "witness")
    assert dict(witness.attributes)["ghost"] is True
    assert not graph.parse_diagnostics


def test_explicit_aliases_resolve_only_selected_modules(tmp_path: Path) -> None:
    (tmp_path / "Base.dfy").write_text("module Base { function F(x:int):int { x } }\n")
    (tmp_path / "Other.dfy").write_text("module Other { function F(x:int):int { x } }\n")
    (tmp_path / "Use.dfy").write_text(
        'include "Base.dfy"\nmodule Use { import B = Base\nfunction Run(x:int):int { B.F(x) } }\n'
    )
    graph = parse_directory(str(tmp_path), "dafny", strict=True)
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 1
    assert dict(graph.nodes[calls[0].target_id].attributes)["qualified_name"] == "Base.F"


def test_local_bindings_quantifiers_and_comments_do_not_capture_globals(tmp_path: Path) -> None:
    path = tmp_path / "Scopes.dfy"
    path.write_text(
        "function F(x:int):int { x }\n"
        "method Run() { var F := (x:int) => x; var y := F(1); }\n"
        "predicate P() { forall F:int :: F == F }\n"
        'method Note() { print "F(1)"; /* F(2) */ }\n'
    )
    graph = parse_file(str(path), strict=True)
    assert not any(e.kind in {EdgeKind.CALLS, EdgeKind.REFERENCES} for e in graph.edges)


def test_lemma_argument_evaluation_is_not_a_runtime_call(tmp_path: Path) -> None:
    path = tmp_path / "Erase.dfy"
    path.write_text("function f(x:int):int {x}\nlemma L(x:int) {}\nmethod Run() { L(f(0)); }\n")
    graph = parse_file(str(path), strict=True)
    assert any(
        e.kind == EdgeKind.REFERENCES and graph.nodes[e.target_id].name == "f" for e in graph.edges
    )
    assert not any(e.kind == EdgeKind.CALLS for e in graph.edges)


def _targets(graph: CodeGraph, kind: EdgeKind, owner: str) -> set[str]:
    return {
        graph.nodes[e.target_id].name
        for e in graph.edges
        if e.kind == kind and graph.nodes[e.source_id].name == owner
    }


def test_nested_computations_and_ghost_argument_positions(tmp_path: Path) -> None:
    path = tmp_path / "Arguments.dfy"
    path.write_text(
        "function F(ghost x:int,y:int):int {y}\n"
        "function G(x:int):int{x}\n"
        "function H(x:int):int{x}\n"
        "method Run(){ var r := F(x:=G(0),y:=H(0)); }\n"
    )
    graph = parse_file(str(path), strict=True)
    assert _targets(graph, EdgeKind.CALLS, "Run") == {"F", "H"}
    assert _targets(graph, EdgeKind.REFERENCES, "Run") == {"F", "G", "H"}


def test_nested_calls_and_executable_forall_updates(tmp_path: Path) -> None:
    path = tmp_path / "Nested.dfy"
    path.write_text(
        "function F(x:int):int{x}\n"
        "function G(x:int):int{x}\n"
        "method Run(a:array<int>){ forall i:int | 0<=i<a.Length { a[i] := F(G(i)); } }\n"
    )
    graph = parse_file(str(path), strict=True)
    assert _targets(graph, EdgeKind.CALLS, "Run") == {"F", "G"}


def test_logical_function_modifiers_are_not_executable(tmp_path: Path) -> None:
    path = tmp_path / "Logical.dfy"
    path.write_text(
        "least predicate P(x:int) {true}\n"
        "greatest predicate Q(x:int) {true}\n"
        "twostate function T(x:int):int{x}\n"
        "method Run(){var a:=P(0);var b:=Q(0);var c:=T(0);}\n"
    )
    graph = parse_file(str(path), strict=True)
    assert not _targets(graph, EdgeKind.CALLS, "Run")
    assert _targets(graph, EdgeKind.REFERENCES, "Run") == {"P", "Q", "T"}
    assert all(
        dict(n.attributes)["executable"] is False
        for n in graph.nodes.values()
        if n.name in {"P", "Q", "T"}
    )


def test_quantifier_guards_keep_free_dependencies(tmp_path: Path) -> None:
    path = tmp_path / "Guard.dfy"
    path.write_text(
        "function Len(s:seq<int>):int{0}\n"
        "predicate P(s:seq<int>){forall i:int | 0<=i<Len(s) :: i==i}\n"
    )
    graph = parse_file(str(path), strict=True)
    assert _targets(graph, EdgeKind.REFERENCES, "P") == {"Len"}


def test_import_aliases_apply_before_the_import_statement(tmp_path: Path) -> None:
    (tmp_path / "Base.dfy").write_text("module Base {function F(x:int):int{x}}\n")
    (tmp_path / "Use.dfy").write_text(
        "module Use {function Run(x:int):int{B.F(x)} import B=Base}\n"
    )
    graph = parse_directory(str(tmp_path), "dafny", strict=True)
    assert _targets(graph, EdgeKind.CALLS, "Run") == {"F"}


def test_parameters_preserve_input_order_and_types(tmp_path: Path) -> None:
    path = tmp_path / "Parameters.dfy"
    path.write_text(
        "method Run(z:int,a:bool) returns (q:int) {q:=z;}\n"
        "iterator Items(z:int) yields (q:int) {}\n"
    )
    graph = parse_file(str(path), strict=True)
    units = {n.name: n for n in graph.nodes.values()}
    assert [(p.name, p.type_ref.name if p.type_ref else None) for p in units["Run"].parameters] == [
        ("z", "int"),
        ("a", "bool"),
    ]
    assert [p.name for p in units["Items"].parameters] == ["z"]
    assert dict(units["Items"].attributes)["declaration_role"] == "iterator"


def test_anonymous_constructor_identity_is_stable(tmp_path: Path) -> None:
    path = tmp_path / "Stable.dfy"
    text = "class C { constructor() {} }\n"
    path.write_text(text)
    before = parse_file(str(path), strict=True)
    path.write_text("// added comment\n" + text)
    after = parse_file(str(path), strict=True)
    old = next(n for n in before.nodes.values() if n.name == "<constructor>")
    new = next(n for n in after.nodes.values() if n.name == "<constructor>")
    assert old.id == new.id
    assert dict(new.attributes)["declaration_role"] == "constructor"


def test_datatype_constructor_aliases_and_constant_initializers(tmp_path: Path) -> None:
    path = tmp_path / "Constructors.dfy"
    path.write_text(
        "datatype Choice = None | Some(x:int)\n"
        "function Make():Choice{Some(0)}\n"
        "const initial:Choice := Some(0)\n"
    )
    graph = parse_file(str(path), strict=True)
    assert _targets(graph, EdgeKind.CALLS, "Make") == {"Some"}
    assert "Some" in _targets(graph, EdgeKind.REFERENCES, "initial")


@pytest.mark.parametrize(
    "body",
    [
        "var x := 0; var r := x;",
        "var r := (x:int) => x;",
        "assert forall x:int :: x == x;",
        "var r := var x := 0; x;",
    ],
)
def test_local_binding_forms(tmp_path: Path, body: str) -> None:
    path = tmp_path / "Bound.dfy"
    path.write_text(f"const x:int := 1\nmethod Run(){{{body}}}\n")
    graph = parse_file(str(path), strict=True)
    assert "x" not in _targets(graph, EdgeKind.REFERENCES, "Run")


def test_deep_generic_lookahead_is_bounded_in_native_scanner(tmp_path: Path) -> None:
    path = tmp_path / "Deep.dfy"
    path.write_text("method Run(){var y:=F<" + "A<" * 200_000 + "int" + ">" * 200_000 + ">(0);}")
    subprocess.run(  # noqa: S603 -- fixed interpreter and source; only a test path varies.
        [
            sys.executable,
            "-c",
            "from trailmark.parse import parse_file; import sys; "
            "assert parse_file(sys.argv[1]).parse_diagnostics",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )


def test_ghost_constructor_arguments_remain_erased(tmp_path: Path) -> None:
    path = tmp_path / "GhostConstructor.dfy"
    path.write_text(
        "datatype Choice = Some(ghost witness:int,value:int)\n"
        "function f():int{0}\nfunction Make():Choice{Some(f(),f())}\n"
    )
    graph = parse_file(str(path), strict=True)
    calls = [
        e for e in graph.edges if e.kind == EdgeKind.CALLS and graph.nodes[e.target_id].name == "f"
    ]
    assert len(calls) == 1
    assert calls[0].location and calls[0].location.start_col == 32
