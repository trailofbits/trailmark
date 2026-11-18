from pathlib import Path

import pytest

from trailmark.models.edges import EdgeKind
from trailmark.parse import detect_languages, parse_directory, parse_file


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
