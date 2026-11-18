from __future__ import annotations

from pathlib import Path

import pytest

from trailmark.models.edges import EdgeKind
from trailmark.parse import parse_directory, parse_file
from trailmark.query.api import QueryEngine

FIXTURE = Path(__file__).parent / "fixtures/kat/fstar/taxonomy.fst"


def test_roles_references_calls_and_shadowing() -> None:
    graph = parse_file(str(FIXTURE), strict=True)
    names = {n.id: n.name for n in graph.nodes.values()}
    edges = {(names[e.source_id], names[e.target_id], e.kind) for e in graph.edges}
    assert ("run", "identity", EdgeKind.CALLS) in edges
    assert ("proof", "positive", EdgeKind.REFERENCES) in edges
    assert ("proof", "identity", EdgeKind.REFERENCES) in edges
    assert not any(a in {"proof", "apply"} and kind == EdgeKind.CALLS for a, _, kind in edges)
    assert not any(a == "apply" and b == "identity" for a, b, _ in edges)
    trusted = next(n for n in graph.nodes.values() if n.name == "trusted")
    assert dict(trusted.attributes)["assumed"] is True
    engine = QueryEngine.from_graph(graph)
    assert {n["name"] for n in engine.reachable_from("proof", edge_kinds=("references",))} == {
        "positive",
        "identity",
    }
    assert engine.reachable_from("proof") == []


def test_interfaces_keep_both_locations_and_resolve_imported_signatures(tmp_path: Path) -> None:
    (tmp_path / "Base.fst").write_text("module Base\nlet f (x:int) : Tot int = x\n")
    (tmp_path / "Base.fsti").write_text("module Base\nval f : int -> Tot int\n")
    (tmp_path / "Use.fst").write_text(
        "module Use\nmodule B = Base\nlet run (x:int) : Tot int = B.f x\n"
    )
    graph = parse_directory(str(tmp_path), "fstar", strict=True)
    functions = [n for n in graph.nodes.values() if n.name == "f"]
    assert len(functions) == 2
    assert {Path(n.location.file_path).suffix for n in functions} == {".fst", ".fsti"}
    assert any(e.kind == EdgeKind.CORRESPONDS_TO for e in graph.edges)
    references = [e for e in graph.edges if e.kind == EdgeKind.REFERENCES]
    assert len(references) == 1
    assert dict(graph.nodes[references[0].target_id].attributes)["signature"] is True


def test_local_binders_and_local_opens(tmp_path: Path) -> None:
    (tmp_path / "Base.fst").write_text("module Base\nlet f (x:int) : Tot int = x\n")
    path = tmp_path / "Use.fst"
    path.write_text(
        "module Use\nopen Base\nlet a (x:int) = let f = x in f\n"
        "let b (x:int) = let open Base in f x\n"
        "let c (x:int) = (fun f -> f x) (fun y -> y)\n"
    )
    graph = parse_directory(str(tmp_path), "fstar", strict=True)
    refs = [e for e in graph.edges if e.kind == EdgeKind.REFERENCES]
    assert [(graph.nodes[e.source_id].name, graph.nodes[e.target_id].name) for e in refs] == [
        ("b", "f")
    ]


def test_opaque_pulse_is_diagnosed_instead_of_silently_counted(tmp_path: Path) -> None:
    path = tmp_path / "Embedded.fst"
    path.write_text("module Embedded\n#lang-pulse\nfn hidden() { () }\n")
    graph = parse_file(str(path))
    assert any(d.kind == "unsupported" for d in graph.parse_diagnostics)
    assert not any(n.name == "hidden" for n in graph.nodes.values())
    with pytest.raises(ValueError, match="diagnostic"):
        parse_file(str(path), strict=True)


def test_computational_expression_in_lemma_argument_is_not_a_call(tmp_path: Path) -> None:
    path = tmp_path / "Erase.fst"
    path.write_text(
        "module Erase\nlet f (x:int) : Tot int = x\n"
        "let p (x:int) : Lemma True = ()\n"
        "let run (x:int) : Tot int = let _ = p (f x) in x\n"
    )
    graph = parse_file(str(path), strict=True)
    assert any(
        e.kind == EdgeKind.REFERENCES and graph.nodes[e.target_id].name == "f" for e in graph.edges
    )
    assert not any(e.kind == EdgeKind.CALLS for e in graph.edges)


def test_nested_computation_types_private_and_directives(tmp_path: Path) -> None:
    path = tmp_path / "Scopes.fst"
    path.write_text(
        'module Scopes\n#set-options "--fuel 0"\n#push-options\n#pop-options\n'
        "type t = int\nlet p (x:int) : Tot bool = true\n"
        "private let f (x:t{p x}) : Tot t = x\n"
        "let run (x:int) : Tot int = f (f x)\n"
    )
    graph = parse_file(str(path), strict=True)
    f = next(n for n in graph.nodes.values() if n.name == "f")
    assert dict(f.attributes)["private"] is True
    assert f.parameters[0].name == "x"
    assert {
        graph.nodes[e.target_id].name
        for e in graph.edges
        if e.source_id == f.id and e.kind == EdgeKind.REFERENCES
    } == {"t", "p"}
    assert len([e for e in graph.edges if e.kind == EdgeKind.CALLS]) == 2


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("let rec x (y:int) : Tot int = x y in x 0", 0),
        ("match 0 with | x when x > 0 -> x | _ -> 0", 0),
        ("let f (x:int) = x in x", 1),
        ("let (x,y) = (0,1) in x", 0),
        ("(fun x -> x) 0", 0),
        ("let x = x in x", 1),
    ],
)
def test_binding_scope_boundaries(tmp_path: Path, expression: str, expected: int) -> None:
    path = tmp_path / "Bind.fst"
    path.write_text(f"module Bind\nlet x = 3\nlet run = {expression}\n")
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


def test_labels_pattern_bindings_and_deep_patterns(tmp_path: Path) -> None:
    path = tmp_path / "Patterns.fst"
    path.write_text(
        "module Patterns\nlet field = 1\n"
        "type r = { field:int }\nlet record = { field = 0 }\n"
        "let (a,b) = (0,1)\n"
        "let deep = let " + "(" * 650 + "x" + ")" * 650 + " = 0 in x\n"
    )
    graph = parse_file(str(path), strict=True)
    assert {"a", "b", "deep"} <= {n.name for n in graph.nodes.values()}
    assert not any(
        e.kind == EdgeKind.REFERENCES and graph.nodes[e.source_id].name == "record"
        for e in graph.edges
    )


def test_private_declarations_stay_in_their_module(tmp_path: Path) -> None:
    (tmp_path / "Base.fst").write_text(
        "module Base\n"
        "private let hidden (x:int) : Tot int = x\n"
        "let public (x:int) : Tot int = hidden x\n"
    )
    (tmp_path / "Use.fst").write_text(
        "module Use\nopen Base\nlet run (x:int) : Tot int = hidden x\n"
    )
    graph = parse_directory(str(tmp_path), "fstar", strict=True)
    assert {
        (graph.nodes[e.source_id].name, graph.nodes[e.target_id].name)
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES
    } == {("public", "hidden")}


def test_mutual_binding_parameters_do_not_leak(tmp_path: Path) -> None:
    path = tmp_path / "Mutual.fst"
    path.write_text(
        "module Mutual\nlet x = 0\nlet rec f (x:int) : Tot int = x and g (y:int) : Tot int = x\n"
    )
    graph = parse_file(str(path), strict=True)
    refs = [
        (graph.nodes[e.source_id].name, graph.nodes[e.target_id].name)
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES
    ]
    assert refs == [("g", "x")]


@pytest.mark.parametrize("directive,expected", [("include", {"c", "p"}), ("open", set())])
def test_reexports_retain_constants_and_reusable_predicates(
    tmp_path: Path, directive: str, expected: set[str]
) -> None:
    (tmp_path / "Base.fst").write_text("module Base\nlet c = 1\nlet p (x:int) = x = c\n")
    (tmp_path / "Second.fst").write_text("module Second\nlet extra = 2\n")
    (tmp_path / "Middle.fst").write_text(f"module Middle\n{directive} Base\n{directive} Second\n")
    (tmp_path / "Use.fst").write_text("module Use\nopen Middle\nlet run = p c\n")
    graph = parse_directory(str(tmp_path), "fstar", strict=True)
    assert {
        graph.nodes[e.target_id].name
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES and graph.nodes[e.source_id].name == "run"
    } == expected


@pytest.mark.parametrize("directive", ["#pop-options", "#show-options", "#restart-solver"])
def test_empty_directives_preserve_the_following_declaration(
    tmp_path: Path, directive: str
) -> None:
    path = tmp_path / "Directives.fst"
    path.write_text(f"module Directives\n#push-options\n{directive}\nlet retained = 1\n")
    assert any(n.name == "retained" for n in parse_file(str(path), strict=True).nodes.values())


@pytest.mark.parametrize("directive", ["include Base {c}", "open Base {c as renamed}"])
def test_restricted_imports_are_not_treated_as_unrestricted(tmp_path: Path, directive: str) -> None:
    (tmp_path / "Base.fst").write_text("module Base\nlet c = 1\nlet hidden = 2\n")
    (tmp_path / "Use.fst").write_text(f"module Use\n{directive}\nlet retained = hidden\n")
    graph = parse_directory(str(tmp_path), "fstar")
    assert any("restricted imports" in d.message for d in graph.parse_diagnostics)
    assert any(n.name == "retained" for n in graph.nodes.values())
    assert not any(e.kind == EdgeKind.REFERENCES for e in graph.edges)
