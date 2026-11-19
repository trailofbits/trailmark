from pathlib import Path

import pytest

from trailmark.models.edges import EdgeKind
from trailmark.parse import parse_file


def test_protocol_references_and_query_roles() -> None:
    graph = parse_file(
        str(Path(__file__).parent / "fixtures/kat/proverif/taxonomy.pv"), strict=True
    )
    refs = {
        (graph.nodes[e.source_id].name, graph.nodes[e.target_id].name)
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES
    }
    assert {
        ("sender", "h"),
        ("sender", "sent"),
        ("sender", "c"),
        ("query@5", "sent"),
        ("process@6", "sender"),
    } <= refs
    assert not any(e.kind == EdgeKind.CALLS for e in graph.edges)


def test_input_new_let_and_query_variables_shadow_globals(tmp_path: Path) -> None:
    path = tmp_path / "scope.pv"
    path.write_text(
        "free c:channel.\nfree x:bitstring.\n"
        "let p(x:bitstring) = out(c,x).\n"
        "query x:bitstring; attacker(x).\n"
        "process new x:bitstring; in(c,y:bitstring); let x = y in out(c,x) else out(c,y)\n"
    )
    graph = parse_file(str(path), strict=True)
    assert not any(
        e.kind == EdgeKind.REFERENCES and graph.nodes[e.target_id].name == "x" for e in graph.edges
    )


def test_nested_comments_do_not_add_protocol_actions(tmp_path: Path) -> None:
    path = tmp_path / "comments.pv"
    path.write_text("(* (* fun hidden(bitstring):bitstring. *) event fake. *)\nprocess 0\n")
    graph = parse_file(str(path), strict=True)
    assert not any(n.name in {"hidden", "fake"} for n in graph.nodes.values())
    path.write_text("(* unfinished (* nested *)\nprocess 0")
    with pytest.raises(ValueError, match="diagnostic"):
        parse_file(str(path), strict=True)


def test_table_get_binders_and_continuations(tmp_path: Path) -> None:
    path = tmp_path / "tables.pv"
    path.write_text(
        "table data(bitstring).\nfree c:channel.\nfree x:bitstring.\n"
        "process get data(x:bitstring) in out(c,x) | out(c,x) else out(c,x)\n"
    )
    graph = parse_file(str(path), strict=True)
    refs = [graph.nodes[e.target_id].name for e in graph.edges if e.kind == EdgeKind.REFERENCES]
    assert refs.count("x") == 1
    assert "data" in refs and refs.count("c") == 3


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("let x = x in out(c,x) else out(c,x)", 2),
        ("let x:bitstring suchthat p(x) in out(c,x) else out(c,x)", 1),
        ("new k[x]:bitstring; out(c,k)", 1),
        ("new x:bitstring; out(c,x)", 0),
        ("in(c,x:bitstring); out(c,x)", 0),
        ("in(c,=x); out(c,x)", 2),
        ("x <-R bitstring; out(c,x)", 0),
        ("x:bitstring <- x; out(c,x)", 1),
        ("!i <= x out(c,i) | out(c,x)", 2),
        ("(in(c,x:bitstring); out(c,x)) | out(c,x)", 1),
    ],
)
def test_process_binding_matrix(tmp_path: Path, body: str, expected: int) -> None:
    path = tmp_path / "matrix.pv"
    path.write_text(f"free c:channel.\nfree x:bitstring.\npred p(bitstring).\nprocess {body}\n")
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


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("let x = x in x else x", 2),
        ("let x:bitstring suchthat p(x) in x else x", 1),
        ("new k[x]:bitstring; x", 2),
        ("x <-R bitstring; x", 0),
        ("x:bitstring <- x; x", 1),
    ],
)
def test_term_binding_matrix(tmp_path: Path, body: str, expected: int) -> None:
    path = tmp_path / "terms.pv"
    path.write_text(f"free x:bitstring.\npred p(bitstring).\nletfun f = {body}.\nprocess 0\n")
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


def test_configuration_and_security_directives(tmp_path: Path) -> None:
    path = tmp_path / "directives.pv"
    path.write_text(
        "free x:bitstring [private].\nset traceDisplay = short.\nparam N.\n"
        "weaksecret x.\nnoninterf x.\nnounif attacker(x).\nprocess 0\n"
    )
    graph = parse_file(str(path), strict=True)
    assert {
        dict(graph.nodes[e.source_id].attributes)["declaration_role"]
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES and graph.nodes[e.target_id].name == "x"
    } == {"weaksecret", "noninterf", "nounif"}


def test_deep_patterns_do_not_recurse_in_python(tmp_path: Path) -> None:
    path = tmp_path / "deep.pv"
    path.write_text(
        "free c:channel.\nfree x:bitstring.\nprocess in(c,"
        + "(" * 650
        + "x:bitstring"
        + ")" * 650
        + "); out(c,x)\n"
    )
    graph = parse_file(str(path), strict=True)
    assert not any(
        e.kind == EdgeKind.REFERENCES and graph.nodes[e.target_id].name == "x" for e in graph.edges
    )


@pytest.mark.parametrize(
    "text",
    [
        "proba p.\nprocess 0",
        "clauses p(x).\nprocess 0",
    ],
)
def test_opaque_boundaries_have_one_diagnostic(tmp_path: Path, text: str) -> None:
    path = tmp_path / "opaque.pv"
    path.write_text(text)
    graph = parse_file(str(path))
    assert len([d for d in graph.parse_diagnostics if d.kind == "unsupported"]) == 1
    with pytest.raises(ValueError, match="diagnostic"):
        parse_file(str(path), strict=True)


def test_macros_predicates_and_bare_constants_keep_dependencies(tmp_path: Path) -> None:
    path = tmp_path / "reusable.pv"
    path.write_text(
        "const unit:bitstring.\nfun h(bitstring):bitstring.\n"
        "pred valid(bitstring).\nfree c:channel.\n"
        "letfun inner(x:bitstring) = h(x).\nletfun outer = inner(unit).\n"
        "let send = if valid(unit) then out(c,outer).\n"
        "process send\n"
    )
    graph = parse_file(str(path), strict=True)
    refs = {
        (graph.nodes[e.source_id].name, graph.nodes[e.target_id].name)
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES
    }
    assert {
        ("inner", "h"),
        ("outer", "inner"),
        ("outer", "unit"),
        ("send", "valid"),
        ("send", "unit"),
        ("send", "outer"),
        ("process@8", "send"),
    } <= refs
    assert not any(e.kind == EdgeKind.CALLS for e in graph.edges)


def test_destructors_and_declaration_macros(tmp_path: Path) -> None:
    path = tmp_path / "library.pv"
    path.write_text(
        "fun enc(bitstring,bitstring):bitstring.\n"
        "reduc forall x:bitstring,k:bitstring; dec(enc(x,k),k) = x.\n"
        "def Helpers(T) { fun wrap(T):T. }\nexpand Helpers(bitstring).\n"
        "free c:channel.\nfree k:bitstring.\nprocess out(c,dec(enc(k,k),k))\n"
    )
    graph = parse_file(str(path), strict=True)
    units = {n.name: n for n in graph.nodes.values()}
    assert dict(units["dec"].attributes)["declaration_role"] == "destructor"
    assert dict(units["wrap"].attributes)["qualified_name"] == "Helpers.wrap"
    refs = {
        (graph.nodes[e.source_id].name, graph.nodes[e.target_id].name)
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES
    }
    assert ("dec", "enc") in refs
    assert any(target == "dec" and source.startswith("process@") for source, target in refs)
    assert not any(source == target == "dec" for source, target in refs)


def test_library_fallback_is_local_and_ambiguity_is_reported(tmp_path: Path) -> None:
    from trailmark.parse import parse_directory

    (tmp_path / "library.pvl").write_text("free key:bitstring.\n")
    (tmp_path / "model.pv").write_text("query attacker(key).\nprocess 0\n")
    graph = parse_directory(str(tmp_path), "proverif", strict=True)
    refs = [e for e in graph.edges if e.kind == EdgeKind.REFERENCES]
    assert len(refs) == 1 and dict(refs[0].attributes)["library_resolution"] == "directory"
    (tmp_path / "alternative.pvl").write_text("free key:bitstring.\n")
    graph = parse_directory(str(tmp_path), "proverif")
    assert not any(e.kind == EdgeKind.REFERENCES for e in graph.edges)
    assert any(
        "Ambiguous ProVerif directory libraries" in d.message for d in graph.parse_diagnostics
    )
