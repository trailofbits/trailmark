from pathlib import Path

import pytest

from trailmark.models.edges import EdgeKind
from trailmark.parse import parse_directory, parse_file


def test_program_contract_and_proof_references() -> None:
    graph = parse_file(str(Path(__file__).parent / "fixtures/kat/whyml/taxonomy.mlw"), strict=True)
    names = {n.id: n.name for n in graph.nodes.values()}
    calls = {
        (names[e.source_id], names[e.target_id]) for e in graph.edges if e.kind == EdgeKind.CALLS
    }
    assert calls == {("run", "inc")}
    refs = {
        (names[e.source_id], names[e.target_id], dict(e.attributes)["reference_context"])
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES
    }
    assert {
        ("run", "positive", "contract"),
        ("run", "model", "contract"),
        ("proof", "inc", "proof"),
    } <= refs


def test_lexical_binders_types_and_nested_comments(tmp_path: Path) -> None:
    path = tmp_path / "scope.mlw"
    path.write_text(
        "module Scope\nuse int.Int\n"
        "type option 'a = None | Some 'a\n"
        "type state = { ghost model:int; mutable value:int }\n"
        "let f (x:int) : int = x\n"
        "(* outer (* let hidden () = f 0 *) let fake () = f 0 *)\n"
        "let apply (f:int -> int) (x:int) : int = f x\n"
        "let local (x:int) : int = let f = x in f\n"
        "predicate quant = forall f:int. f=f\n"
        "let pat (x:option int) : int = match x with | Some f -> f | None -> 0 end\n"
        "end\n"
    )
    graph = parse_file(str(path), strict=True)
    assert {"option", "Some", "None", "state", "value", "model"} <= {
        n.name for n in graph.nodes.values()
    }
    assert not any(n.name in {"hidden", "fake"} for n in graph.nodes.values())
    assert not any(
        e.kind == EdgeKind.REFERENCES and graph.nodes[e.target_id].name == "f" for e in graph.edges
    )
    assert (
        dict(next(n for n in graph.nodes.values() if n.name == "model").attributes)["ghost"] is True
    )


def test_use_visibility_aliases_and_clone_boundary(tmp_path: Path) -> None:
    (tmp_path / "base.mlw").write_text("module Base\nlet f (x:int):int = x\nend\n")
    (tmp_path / "other.mlw").write_text("module Other\nlet f (x:int):int = x\nend\n")
    path = tmp_path / "use.mlw"
    path.write_text("module Use\nuse base.Base as B\nlet run (x:int):int = B.f x\nend\n")
    graph = parse_directory(str(tmp_path), "whyml", strict=True)
    refs = [e for e in graph.edges if e.kind == EdgeKind.REFERENCES]
    assert len(refs) == 1
    assert Path(graph.nodes[refs[0].target_id].location.file_path).name == "base.mlw"
    path.write_text("module Use\nclone base.Base as C with val f = f\nend\n")
    assert any(d.kind == "unsupported" for d in parse_file(str(path)).parse_diagnostics)
    with pytest.raises(ValueError, match="diagnostic"):
        parse_file(str(path), strict=True)


def test_ghost_arguments_do_not_create_runtime_calls(tmp_path: Path) -> None:
    path = tmp_path / "ghost.mlw"
    path.write_text(
        "module Ghost\nlet f (x:int):int = x\n"
        "let ghost erase (x:int):unit = ()\nlet run () = erase (f 0)\nend\n"
    )
    graph = parse_file(str(path), strict=True)
    assert any(
        e.kind == EdgeKind.REFERENCES and graph.nodes[e.target_id].name == "f" for e in graph.edges
    )
    assert not any(e.kind == EdgeKind.CALLS for e in graph.edges)


def test_unsupported_syntax_and_unfinished_comments_are_diagnostics(tmp_path: Path) -> None:
    path = tmp_path / "unsupported.mlw"
    for text in ("module M\nmeta unknown\nend\n", "module M\n(* unterminated (* nested *)\nend\n"):
        path.write_text(text)
        assert parse_file(str(path)).parse_diagnostics
        with pytest.raises(ValueError, match="diagnostic"):
            parse_file(str(path), strict=True)


def test_nested_calls_ghost_initializers_and_ordered_parameters(tmp_path: Path) -> None:
    path = tmp_path / "Calls.mlw"
    path.write_text(
        "module Calls\nlet g (x:int):int = x\nlet f (z:int) (a:bool):int = z\n"
        "let run () = let ghost y = g 0 in f (g 1) true\nend\n"
    )
    graph = parse_file(str(path), strict=True)
    f = next(n for n in graph.nodes.values() if n.name == "f")
    assert [(p.name, p.type_ref.name if p.type_ref else None) for p in f.parameters] == [
        ("z", "int"),
        ("a", "bool"),
    ]
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert [graph.nodes[e.target_id].name for e in calls] == ["f", "g"]
    assert all(e.location and e.location.start_line == 4 for e in calls)


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("let rec x (y:int) = x y in x 0", 0),
        ("let f (x:int) = x in x", 1),
        ("match 0 with | (* comment *) x -> x end", 0),
        ("for x = 0 to 5 do invariant { x >= 0 } () done", 0),
        ("fun (x:int) -> x", 0),
        ("forall x:int. x=x", 0),
        ("let x = x in x", 1),
    ],
)
def test_binder_scope_matrix(tmp_path: Path, expression: str, expected: int) -> None:
    path = tmp_path / "Matrix.mlw"
    path.write_text(f"module M\nlet x = 0\nlet run () = {expression}\nend\n")
    graph = parse_file(str(path), strict=True)
    assert (
        len(
            [
                e
                for e in graph.edges
                if e.kind == EdgeKind.REFERENCES
                and graph.nodes[e.source_id].name == "run"
                and graph.nodes[e.target_id].name == "x"
            ]
        )
        == expected
    )


def test_scope_import_opens_members(tmp_path: Path) -> None:
    path = tmp_path / "Scopes.mlw"
    path.write_text("module M\nscope import S\nlet x = 0\nend\nlet run () = x\nend\n")
    graph = parse_file(str(path), strict=True)
    assert any(
        e.kind == EdgeKind.REFERENCES
        and graph.nodes[e.source_id].name == "run"
        and graph.nodes[e.target_id].name == "x"
        for e in graph.edges
    )


@pytest.mark.parametrize(
    "text", ["let rec f (x:int) = x with g (y:int) = y", "let f (a:array int) = a[0]"]
)
def test_documented_grammar_boundaries_diagnose(tmp_path: Path, text: str) -> None:
    path = tmp_path / "Boundary.mlw"
    path.write_text(f"module M\n{text}\nend\n")
    assert parse_file(str(path)).parse_diagnostics
    with pytest.raises(ValueError, match="diagnostic"):
        parse_file(str(path), strict=True)
