from pathlib import Path

import pytest

from tests.parse_work import parse_work
from trailmark.models.edges import EdgeKind
from trailmark.parse import detect_languages, parse_directory, parse_file


@pytest.mark.mutation_fast
def test_missing_import_stops_after_enclosing_namespaces(tmp_path: Path) -> None:
    source = tmp_path / "Scoped.v"
    source.write_text(
        "Module Outer.\nRequire Import Missing.\nDefinition value := 0.\nEnd Outer.\n"
    )
    graph, _ = parse_work(source, language="rocq", max_work=100_000)
    value = next(node for node in graph.nodes.values() if node.name == "value")
    assert dict(value.attributes)["qualified_name"] == "Scoped.Outer.value"


@pytest.mark.mutation_fast
def test_gallina_references_and_statement_roles() -> None:
    graph = parse_file(str(Path(__file__).parent / "fixtures/kat/rocq/taxonomy.v"), "rocq")
    names = {n.id: n.name for n in graph.nodes.values()}
    refs = {
        (names[e.source_id], names[e.target_id])
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES
    }
    assert {("use", "identity"), ("trusted", "identity"), ("identity_ok", "identity")} <= refs
    assert (
        dict(next(n for n in graph.nodes.values() if n.name == "trusted").attributes)["assumed"]
        is True
    )
    assert not any(e.kind == EdgeKind.CALLS for e in graph.edges)
    assert any(d.kind == "unsupported" and "tactics" in d.message for d in graph.parse_diagnostics)


@pytest.mark.mutation_fast
def test_nested_comments_and_lexical_binders(tmp_path: Path) -> None:
    path = tmp_path / "Scope.v"
    path.write_text(
        "Definition target (n:nat) := n.\n"
        "(* outer (* Definition fake := target. *) Definition hidden := target. *)\n"
        "Definition use (target:nat -> nat) := target 0.\n"
        "Definition lam := fun target:nat => target.\n"
        "Definition local := let target := 1 in target.\n"
        "Definition quant : Prop := forall target:nat, target = target.\n"
        "Section Context.\nVariable target : nat.\n"
        "Definition section_use := target.\nEnd Context.\n"
    )
    graph = parse_file(str(path), "rocq", strict=True)
    assert not any(n.name in {"fake", "hidden"} for n in graph.nodes.values())
    assert not any(e.kind == EdgeKind.REFERENCES for e in graph.edges)
    path.write_text("(* unfinished (* nested *)\nDefinition fake := 0.")
    with pytest.raises(ValueError, match="diagnostic"):
        parse_file(str(path), "rocq", strict=True)


@pytest.mark.mutation_fast
def test_imports_and_module_namespaces(tmp_path: Path) -> None:
    (tmp_path / "Base.v").write_text("Definition f := 0.\n")
    (tmp_path / "Other.v").write_text("Definition f := 1.\n")
    (tmp_path / "Use.v").write_text(
        "Require Import Base.\nModule Inner.\nDefinition use := f.\nEnd Inner.\n"
    )
    graph = parse_directory(str(tmp_path), "rocq", strict=True)
    refs = [e for e in graph.edges if e.kind == EdgeKind.REFERENCES]
    assert len(refs) == 1
    assert Path(graph.nodes[refs[0].target_id].location.file_path).name == "Base.v"
    assert dict(graph.nodes[refs[0].source_id].attributes)["qualified_name"] == "Use.Inner.use"


def test_fail_commands_do_not_create_declarations(tmp_path: Path) -> None:
    path = tmp_path / "Fail.v"
    path.write_text("Fail Definition bad : nat := true.\n")
    graph = parse_file(str(path), "rocq")
    assert graph.parse_diagnostics
    assert not any(n.name == "bad" for n in graph.nodes.values())


@pytest.mark.mutation_fast
def test_implicit_inductive_and_mutual_binders(tmp_path: Path) -> None:
    path = tmp_path / "Bindings.v"
    path.write_text(
        "Definition x := 0.\nDefinition imp {x:nat} := x.\n"
        "Inductive box (x:nat) : Type := C : x = x -> box x.\n"
        "Fixpoint f (x:nat) : nat := x with g (y:nat) : nat := x.\n"
    )
    graph = parse_file(str(path), "rocq", strict=True)
    units = {n.name: n for n in graph.nodes.values()}
    assert [p.name for p in units["imp"].parameters] == ["x"]
    assert [p.name for p in units["f"].parameters] == ["x"]
    assert [p.name for p in units["g"].parameters] == ["y"]
    refs = {
        (graph.nodes[e.source_id].name, graph.nodes[e.target_id].name)
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES
    }
    assert ("g", "x") in refs
    assert not refs & {("f", "x"), ("imp", "x"), ("C", "x")}


@pytest.mark.mutation_fast
def test_modules_aliases_sections_and_import_restoration(tmp_path: Path) -> None:
    path = tmp_path / "Modules.v"
    path.write_text(
        "Module A. Definition x := 0. End A.\n"
        "Module B := A.\nDefinition alias := B.x.\n"
        "Section S. Import A. Definition inside := x. End S.\n"
        "Definition outside := x.\nImport A. Definition opened := x.\n"
        "Module Import D. Variable value : nat. End D.\n"
        "Definition after := value.\n"
    )
    graph = parse_file(str(path), "rocq", strict=True)
    refs = {
        (graph.nodes[e.source_id].name, graph.nodes[e.target_id].name)
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES
    }
    assert refs == {("alias", "x"), ("inside", "x"), ("opened", "x"), ("after", "value")}
    assert (
        dict(next(n for n in graph.nodes.values() if n.name == "after").attributes)[
            "qualified_name"
        ]
        == "Modules.after"
    )


@pytest.mark.mutation_fast
def test_from_import_uses_directory_identity(tmp_path: Path) -> None:
    (tmp_path / "P").mkdir()
    (tmp_path / "P/M.v").write_text("Definition x := 0.\n")
    (tmp_path / "Use.v").write_text("From P Require Import M. Definition use := x.\n")
    graph = parse_directory(str(tmp_path), "rocq", strict=True)
    assert any(
        e.kind == EdgeKind.REFERENCES and graph.nodes[e.target_id].name == "x" for e in graph.edges
    )


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("fun {x:nat} => x", 0),
        ("forall x:nat, x = x", 0),
        ("let '(x,y) := (0,1) in x", 0),
        ("fun (y := x) => y", 1),
        ("f (x := x)", 1),
    ],
)
def test_binder_matrix(tmp_path: Path, expression: str, expected: int) -> None:
    path = tmp_path / "Matrix.v"
    path.write_text(f"Definition x := 0. Definition f := 0. Definition use := {expression}.\n")
    graph = parse_file(str(path), "rocq", strict=True)
    assert (
        len(
            [
                e
                for e in graph.edges
                if e.kind == EdgeKind.REFERENCES
                and graph.nodes[e.source_id].name == "use"
                and graph.nodes[e.target_id].name == "x"
            ]
        )
        == expected
    )


def test_v_extension_requires_explicit_language(tmp_path: Path) -> None:
    path = tmp_path / "hardware.v"
    path.write_text("module counter(input clk); endmodule\n")
    assert detect_languages(str(tmp_path)) == []
    with pytest.raises(ValueError, match="infer"):
        parse_file(str(path))


@pytest.mark.mutation_fast
@pytest.mark.parametrize("flag", ["-Q", "-R"])
def test_from_import_honors_explicit_project_load_paths(tmp_path: Path, flag: str) -> None:
    (tmp_path / "_CoqProject").write_text(f'{flag} "theories" P\n')
    (tmp_path / "theories").mkdir()
    (tmp_path / "theories/M.v").write_text("Definition x := 0.\n")
    (tmp_path / "Use.v").write_text("From P Require Import M. Definition use := x.\n")
    graph = parse_directory(str(tmp_path), "rocq", strict=True)
    assert any(
        e.kind == EdgeKind.REFERENCES and graph.nodes[e.target_id].name == "x" for e in graph.edges
    )


@pytest.mark.parametrize("mode,expected", [("Export", {"c", "p"}), ("Import", set())])
def test_reexported_constants_and_predicates(tmp_path: Path, mode: str, expected: set[str]) -> None:
    (tmp_path / "Base.v").write_text("Definition c := 1. Definition p (x:nat) := x = c.\n")
    (tmp_path / "Second.v").write_text("Definition extra := 2.\n")
    (tmp_path / "Middle.v").write_text(f"Require {mode} Base. Require {mode} Second.\n")
    (tmp_path / "Use.v").write_text("Require Import Middle. Definition use := p c.\n")
    graph = parse_directory(str(tmp_path), "rocq", strict=True)
    assert {
        graph.nodes[e.target_id].name
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES and graph.nodes[e.source_id].name == "use"
    } == expected


@pytest.mark.mutation_fast
def test_exported_nested_modules_keep_their_definitions(tmp_path: Path) -> None:
    path = tmp_path / "Nested.v"
    path.write_text("Module Export Inner. Definition c := 1. End Inner. Definition use := c.\n")
    graph = parse_file(str(path), language="rocq", strict=True)
    assert any(
        graph.nodes[e.source_id].name == "use" and graph.nodes[e.target_id].name == "c"
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES
    )


@pytest.mark.mutation_fast
def test_export_does_not_invent_qualified_members(tmp_path: Path) -> None:
    (tmp_path / "Base.v").write_text("Definition c := 1.\n")
    (tmp_path / "Middle.v").write_text("Require Export Base.\n")
    (tmp_path / "Use.v").write_text("Require Import Middle. Definition use := Middle.c.\n")
    graph = parse_directory(str(tmp_path), "rocq", strict=True)
    assert not any(e.kind == EdgeKind.REFERENCES for e in graph.edges)


@pytest.mark.mutation_fast
@pytest.mark.parametrize("command", ["Require Import Base (c)", "Require Export Base (c)"])
def test_selective_imports_do_not_open_every_name(tmp_path: Path, command: str) -> None:
    (tmp_path / "Base.v").write_text("Definition c := 1. Definition hidden := 2.\n")
    (tmp_path / "Use.v").write_text(f"{command}. Definition use := hidden.\n")
    graph = parse_directory(str(tmp_path), "rocq")
    assert any("selective imports" in d.message for d in graph.parse_diagnostics)
    assert any(n.name == "use" for n in graph.nodes.values())
    assert not any(e.kind == EdgeKind.REFERENCES for e in graph.edges)


def test_load_boundary_preserves_the_next_sentence(tmp_path: Path) -> None:
    path = tmp_path / "Load.v"
    path.write_text('Load "external.v".\nDefinition retained := 1.\n')
    graph = parse_file(str(path), language="rocq")
    assert graph.parse_diagnostics
    assert any(n.name == "retained" for n in graph.nodes.values())


@pytest.mark.parametrize(
    "body",
    [
        "Module Inner. Definition c := 1. End Inner. Export Inner.",
        "Require Base. Module Alias := Base. Export Alias.",
    ],
)
def test_exports_follow_local_modules_and_module_aliases(tmp_path: Path, body: str) -> None:
    (tmp_path / "Base.v").write_text("Definition c := 1.\n")
    (tmp_path / "Middle.v").write_text(body + "\n")
    (tmp_path / "Use.v").write_text("Require Import Middle. Definition use := c.\n")
    graph = parse_directory(str(tmp_path), "rocq", strict=True)
    refs = [
        graph.nodes[e.target_id]
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES and graph.nodes[e.source_id].name == "use"
    ]
    assert len(refs) == 1 and refs[0].name == "c"


@pytest.mark.mutation_fast
def test_records_sum_types_matches_and_sections(tmp_path: Path) -> None:
    path = tmp_path / "Records.v"
    path.write_text(
        "Set Implicit Arguments. Unset Strict Implicit.\n"
        "Record point := { px : nat }.\nDefinition after := 0.\n"
        "Lemma a (d : forall x y : nat, {x = y}+{x <> y}) : True.\n"
        "Proof. intros; [ exact I | idtac ]. Qed.\n"
        "Lemma b : True. Proof. exact I. Qed.\nDefinition c := 0.\n"
        "Definition use := match 0 with 0 => after | _ => 1 end.\n"
        "Section S. Variable A : Type. Hypothesis H : A = A.\n"
        "Definition uses_h : A := H. End S.\n"
    )
    graph = parse_file(str(path), "rocq")
    assert not any(d.kind == "syntax_error" for d in graph.parse_diagnostics)
    assert {"point", "px", "after", "a", "b", "c", "A", "H"} <= {
        n.name for n in graph.nodes.values()
    }
    refs = {
        (graph.nodes[e.source_id].name, graph.nodes[e.target_id].name)
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES
    }
    assert {("use", "after"), ("uses_h", "A"), ("uses_h", "H")} <= refs


@pytest.mark.mutation_fast
def test_module_types_and_term_namespaces(tmp_path: Path) -> None:
    (tmp_path / "Base.v").write_text("Inductive Z := Z0.\n")
    (tmp_path / "Use.v").write_text(
        "Require Import Base. Module Type SIG. Parameter t : Type. End SIG.\n"
        "Module Type OTHER. End OTHER.\n"
        "Module Nat <: SIG <: OTHER. Definition x := 0. End Nat.\nDefinition y := Nat.x.\n"
        "Module Typed : SIG. End Typed. Definition term := SIG.\n"
        "Module Z. Definition f (x : Z) : Z := x. End Z.\n"
    )
    graph = parse_directory(str(tmp_path), "rocq", strict=True)
    assert {"Use.SIG", "Use.SIG.t", "Use.Nat", "Use.Nat.x"} <= {
        dict(n.attributes).get("qualified_name") for n in graph.nodes.values()
    }
    assert any(
        graph.nodes[e.source_id].name == "f"
        and dict(graph.nodes[e.target_id].attributes).get("qualified_name") == "Base.Z"
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES
    )
    signatures = {
        (graph.nodes[e.source_id].name, graph.nodes[e.target_id].name)
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES and dict(e.attributes).get("reference_context") == "module"
    }
    assert signatures == {("Nat", "SIG"), ("Nat", "OTHER"), ("Typed", "SIG")}
    assert not any(
        graph.nodes[e.source_id].name == "term" and e.kind == EdgeKind.REFERENCES
        for e in graph.edges
    )


@pytest.mark.mutation_fast
def test_sentence_recovery_does_not_steal_later_declarations(tmp_path: Path) -> None:
    path = tmp_path / "Recover.v"
    path.write_text(
        "Definition x := 0.\nDefinition broken := { unsupported ??? }.\n"
        "Definition after := x.\nDefinition later := after.\n"
    )
    graph = parse_file(str(path), "rocq")
    assert graph.parse_diagnostics
    refs = {
        (graph.nodes[e.source_id].name, graph.nodes[e.target_id].name)
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES
    }
    assert {("after", "x"), ("later", "after")} <= refs
    assert ("broken", "after") not in refs


@pytest.mark.parametrize(
    ("language", "suffix", "declarations"),
    [("rocq", ".v", "Inductive T := MkT.\nDefinition id {T:Type} (x:T) : T := x.\n")],
)
def test_formal_type_uses_respect_lexical_binders(
    tmp_path: Path, language: str, suffix: str, declarations: str
) -> None:
    from trailmark.parse import parse_file

    path = tmp_path / ("Types" + suffix)
    path.write_text(declarations)
    graph = parse_file(str(path), language=language, strict=True)
    assert not any(e.kind == EdgeKind.TYPE_USES for e in graph.edges)
