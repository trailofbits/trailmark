from pathlib import Path

import pytest

from trailmark.models.edges import EdgeKind
from trailmark.parse import parse_directory, parse_file


def test_records_parameters_and_private_local_operators(tmp_path: Path) -> None:
    (tmp_path / "Base.tla").write_text(
        "---- MODULE Base ----\nVARIABLES field, records\n"
        "LOCAL Helper(x, y) == x = y\n"
        "Use == Helper(records, records)\n"
        "Read == \\E r \\in records: r.field = 0\n"
        "Write == [records EXCEPT !.field = 1]\n"
        "F[n \\in records] == n\n====\n"
    )
    (tmp_path / "Use.tla").write_text(
        "---- MODULE Use ----\nEXTENDS Base\nHidden == Helper(1, 1)\n====\n"
    )
    graph = parse_directory(str(tmp_path), language="tlaplus", strict=True)
    helper = next(n for n in graph.nodes.values() if n.name == "Helper")
    assert [p.name for p in helper.parameters] == ["x", "y"]
    assert dict(helper.attributes)["private"] is True
    function = next(n for n in graph.nodes.values() if n.name == "F")
    assert [p.name for p in function.parameters] == ["n"]
    pairs = {
        (graph.nodes[e.source_id].name, graph.nodes[e.target_id].name)
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES
    }
    assert {("Use", "Helper"), ("Read", "records"), ("Write", "records")} <= pairs
    assert not any(target == "field" or source == "Hidden" for source, target in pairs)


def test_specification_dependencies_are_not_calls() -> None:
    path = Path(__file__).parent / "fixtures/kat/tlaplus/Counter.tla"
    graph = parse_file(str(path), strict=True)
    edges = {
        (graph.nodes[e.source_id].name, graph.nodes[e.target_id].name)
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES
    }
    assert {("Spec", "Init"), ("Spec", "Next"), ("Next", "n"), ("Inv", "Limit")} <= edges
    assert not any(e.kind == EdgeKind.CALLS for e in graph.edges)


def test_binders_do_not_resolve_to_globals(tmp_path: Path) -> None:
    path = tmp_path / "Scope.tla"
    path.write_text(
        "---- MODULE Scope ----\nCONSTANTS f, x\n"
        "Op(f(_), x) == f(x)\nQ == \\A x \\in {1,2}: x=x\n"
        "S == {x \\in {1,2}: x=x}\nC == CHOOSE x: x=x\n"
        "L == LET f(x) == x IN f(1)\n====\n"
    )
    graph = parse_file(str(path), strict=True)
    assert not any(e.kind == EdgeKind.REFERENCES for e in graph.edges)


def test_extends_limits_visibility_and_instances_are_diagnosed(tmp_path: Path) -> None:
    (tmp_path / "Base.tla").write_text("---- MODULE Base ----\nf == TRUE\n====\n")
    (tmp_path / "Other.tla").write_text("---- MODULE Other ----\nf == FALSE\n====\n")
    path = tmp_path / "Use.tla"
    path.write_text("---- MODULE Use ----\nEXTENDS Base\nUse == f\n====\n")
    graph = parse_directory(str(tmp_path), "tlaplus", strict=True)
    refs = [e for e in graph.edges if e.kind == EdgeKind.REFERENCES]
    assert len(refs) == 1
    assert Path(graph.nodes[refs[0].target_id].location.file_path).name == "Base.tla"
    path.write_text("---- MODULE Use ----\nI == INSTANCE Base WITH x <- 1\n====\n")
    assert parse_file(str(path)).parse_diagnostics
    with pytest.raises(ValueError, match="diagnostic"):
        parse_file(str(path), strict=True)


def test_theorem_proof_is_not_a_dependency_claim(tmp_path: Path) -> None:
    path = tmp_path / "Proof.tla"
    path.write_text(
        "---- MODULE Proof ----\nASSUME ax == TRUE\nTHEOREM Thm == ax\nPROOF OBVIOUS\n====\n"
    )
    graph = parse_file(str(path))
    assert any(d.kind == "unsupported" for d in graph.parse_diagnostics)
    refs = [e for e in graph.edges if e.kind == EdgeKind.REFERENCES]
    assert [(graph.nodes[e.source_id].name, graph.nodes[e.target_id].name) for e in refs] == [
        ("Thm", "ax")
    ]


def test_nested_namespaces_and_choose_scope_end_at_the_expression(tmp_path: Path) -> None:
    path = tmp_path / "Scope.tla"
    path.write_text(
        "---- MODULE Scope ----\nCONSTANT x\nOp == (CHOOSE x: x=x) = x\n"
        "---- MODULE Inner ----\nf == TRUE\n====\n====\n"
    )
    graph = parse_file(str(path), strict=True)
    unit = next(n for n in graph.nodes.values() if n.name == "f")
    assert dict(unit.attributes)["qualified_name"] == "Scope.Inner.f"
    refs = [e for e in graph.edges if e.kind == EdgeKind.REFERENCES]
    assert len(refs) == 1 and graph.nodes[refs[0].target_id].name == "x"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("{x+1 : x \\in S}", 0),
        ("{x+1 : x \\in x}", 1),
        ("F(LAMBDA x: x)", 0),
        ("\\A x \\in S: x=x", 0),
        ("CHOOSE x \\in S: x=x", 0),
        ("LET x == 1 IN x", 0),
    ],
)
def test_binding_matrix(tmp_path: Path, body: str, expected: int) -> None:
    path = tmp_path / "Matrix.tla"
    path.write_text(f"---- MODULE Matrix ----\nCONSTANTS x, S, F(_)\nOp == {body}\n====\n")
    graph = parse_file(str(path), strict=True)
    assert (
        len(
            [
                e
                for e in graph.edges
                if e.kind == EdgeKind.REFERENCES and graph.nodes[e.target_id].name == "x"
            ]
        )
        == expected
    )


def test_transitive_and_enclosing_extends(tmp_path: Path) -> None:
    (tmp_path / "C.tla").write_text("---- MODULE C ----\nf == TRUE\n====\n")
    (tmp_path / "B.tla").write_text("---- MODULE B ----\nEXTENDS C\n====\n")
    (tmp_path / "A.tla").write_text(
        "---- MODULE A ----\nEXTENDS B\n----\nUse == f\n"
        "---- MODULE Inner ----\nNested == f\n====\n====\n"
    )
    graph = parse_directory(str(tmp_path), "tlaplus", strict=True)
    assert {
        (graph.nodes[e.source_id].name, graph.nodes[e.target_id].name)
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES
    } == {("Use", "f"), ("Nested", "f")}


def test_recursive_forward_declarations(tmp_path: Path) -> None:
    path = tmp_path / "Recursive.tla"
    path.write_text("---- MODULE Recursive ----\nRECURSIVE f(_)\nUse == f(0)\nf(x) == x\n====\n")
    graph = parse_file(str(path), strict=True)
    assert any(
        e.kind == EdgeKind.REFERENCES
        and graph.nodes[e.source_id].name == "Use"
        and graph.nodes[e.target_id].name == "f"
        for e in graph.edges
    )
    assert len([e for e in graph.edges if e.kind == EdgeKind.CORRESPONDS_TO]) == 1


def test_pluscal_and_nested_proofs_report_one_boundary(tmp_path: Path) -> None:
    path = tmp_path / "Opaque.tla"
    path.write_text(
        "---- MODULE Opaque ----\n"
        "(* --algorithm Example { begin Done: skip; end algorithm; } *)\n"
        "====\n"
    )
    graph = parse_file(str(path))
    assert len([d for d in graph.parse_diagnostics if "PlusCal" in d.message]) == 1
    path.write_text(
        "---- MODULE Opaque ----\nTHEOREM TRUE\nPROOF\n<1>1. TRUE OBVIOUS\n<1> QED BY <1>1\n====\n"
    )
    graph = parse_file(str(path))
    assert len([d for d in graph.parse_diagnostics if "proof steps" in d.message]) == 1


def test_reusable_local_operators_and_bare_constants_keep_dependencies(tmp_path: Path) -> None:
    (tmp_path / "Base.tla").write_text("---- MODULE Base ----\nCONSTANT c\nP(x) == x=c\n====\n")
    (tmp_path / "Middle.tla").write_text("---- MODULE Middle ----\nEXTENDS Base\n====\n")
    (tmp_path / "Use.tla").write_text(
        "---- MODULE Use ----\nEXTENDS Middle\nOp == LET Wrap(x) == P(x) IN Wrap(c)\n====\n"
    )
    graph = parse_directory(str(tmp_path), "tlaplus", strict=True)
    refs = {
        graph.nodes[e.target_id].name
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES and graph.nodes[e.source_id].name == "Op"
    }
    assert refs == {"P", "c"}
    assert not any(e.kind == EdgeKind.CALLS for e in graph.edges)


def test_named_instance_retains_template_and_argument_references(tmp_path: Path) -> None:
    (tmp_path / "Base.tla").write_text(
        "---- MODULE Base ----\nVARIABLE x\nSpec == x=x\nOp(n) == n\n====\n"
    )
    path = tmp_path / "MC.tla"
    path.write_text(
        "---- MODULE MC ----\nVARIABLE y\nI == INSTANCE Base WITH x <- y\n"
        "S == I!Spec\nU == I!Op(y)\n====\n"
    )
    graph = parse_directory(str(tmp_path), "tlaplus")
    refs = {
        (graph.nodes[e.source_id].name, graph.nodes[e.target_id].name)
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES
    }
    assert {("I", "y"), ("S", "I"), ("S", "Spec"), ("U", "I"), ("U", "Op"), ("U", "y")} <= refs
    assert any("uninstantiated template" in d.message for d in graph.parse_diagnostics)


@pytest.mark.parametrize(
    "language,extension,code", [("tlaplus", "tla", "---- MODULE M ----\n" * 140 + "====\n" * 140)]
)
def test_deep_declarations_have_a_bounded_diagnostic(
    tmp_path: Path, language: str, extension: str, code: str
) -> None:
    from trailmark.parse import parse_file

    path = tmp_path / f"deep.{extension}"
    path.write_text(code)
    graph = parse_file(str(path), language)
    assert any("depth of 128" in d.message for d in graph.parse_diagnostics)
