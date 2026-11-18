from pathlib import Path

import pytest

from trailmark.models.edges import EdgeKind
from trailmark.parse import parse_file


def test_rule_and_formula_symbol_references() -> None:
    graph = parse_file(
        str(Path(__file__).parent / "fixtures/kat/tamarin/taxonomy.spthy"), strict=True
    )
    refs = {
        (graph.nodes[e.source_id].name, graph.nodes[e.target_id].name)
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES
    }
    assert {
        ("Hash", "h"),
        ("Hash", "In/1"),
        ("Hash", "Sent/1"),
        ("Hash", "Out/1"),
        ("sent_known", "Sent/1"),
        ("sent_known", "K/1"),
    } <= refs
    assert not any(e.kind == EdgeKind.CALLS for e in graph.edges)
    assert (
        dict(next(n for n in graph.nodes.values() if n.name == "Sent/1").attributes)["implicit"]
        is True
    )


def test_fact_arity_and_rule_variables(tmp_path: Path) -> None:
    path = tmp_path / "arity.spthy"
    path.write_text(
        "theory Arity begin\nfunctions: x/0\n"
        "rule R: [ In(x) ] --[ F(x), F(x,x) ]-> [ Out(x) ]\nend\n"
    )
    graph = parse_file(str(path), strict=True)
    assert {"F/1", "F/2"} <= {n.name for n in graph.nodes.values()}
    assert not any(
        e.kind == EdgeKind.REFERENCES and graph.nodes[e.target_id].name == "x" for e in graph.edges
    )


def test_nested_comments_and_unsupported_proofs(tmp_path: Path) -> None:
    path = tmp_path / "comments.spthy"
    path.write_text(
        "theory Comments begin\n"
        "/* outer /* rule Hidden: [] --> [] */ rule Fake: [] --> [] */\nrule R: [] --> []\nend\n"
    )
    graph = parse_file(str(path), strict=True)
    assert not any(n.name in {"Hidden", "Fake"} for n in graph.nodes.values())
    path.write_text('theory Proof begin\nlemma t: "T" by sorry\nend\n')
    assert parse_file(str(path)).parse_diagnostics
    with pytest.raises(ValueError, match="diagnostic"):
        parse_file(str(path), strict=True)


def test_encryption_shorthand_preserves_nested_function_references(tmp_path: Path) -> None:
    path = tmp_path / "shorthand.spthy"
    path.write_text(
        "theory Shorthand begin\nfunctions: enc/2, h/1, unit/0\n"
        "rule R: [ In(x) ] --> [ Out(enc{h(x),unit()}x) ]\nend\n"
    )
    graph = parse_file(str(path), strict=True)
    refs = {graph.nodes[e.target_id].name for e in graph.edges if e.kind == EdgeKind.REFERENCES}
    assert {"enc", "h", "unit"} <= refs


def test_compound_fact_arguments_and_truth_named_facts(tmp_path: Path) -> None:
    path = tmp_path / "compound.spthy"
    path.write_text(
        "theory Compound begin\nbuiltins: diffie-hellman, xor, multiset\n"
        "rule R: [ St('g'^~x), St(x*y), St(x XOR y), St(x+y) ] --[ F(x), T(x) ]-> [ St(x) ]\n"
        'lemma witness: "All x #i. F(x) @ i ==> T(x) @ i"\nend\n'
    )
    graph = parse_file(str(path), strict=True)
    assert {
        n.name
        for n in graph.nodes.values()
        if dict(n.attributes).get("declaration_role") == "fact_symbol"
    } == {"St/1", "F/1", "T/1"}
    refs = {graph.nodes[e.target_id].name for e in graph.edges if e.kind == EdgeKind.REFERENCES}
    assert {"^", "*", "XOR", "+", "F/1", "T/1"} <= refs


def test_function_rule_and_lemma_names_do_not_collide(tmp_path: Path) -> None:
    path = tmp_path / "names.spthy"
    path.write_text(
        "theory Names begin\n"
        "functions: h/1\n"
        "rule h: [ In(x) ] --> [ Out(h(x)) ]\n"
        'lemma h: "T"\n'
        "end\n"
    )
    graph = parse_file(str(path), strict=True)
    refs = [
        e
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES and graph.nodes[e.target_id].name == "h"
    ]
    assert len(refs) == 1
    assert dict(graph.nodes[refs[0].target_id].attributes)["declaration_role"] == "function"


def test_activated_builtin_symbols_are_retained(tmp_path: Path) -> None:
    path = tmp_path / "builtin.spthy"
    path.write_text(
        "theory Builtin begin\nbuiltins: hashing, symmetric-encryption, asymmetric-encryption\n"
        "rule R: [ In(x) ] --> [ Out(h(senc(aenc(x,pk(x)),x))) ]\nend\n"
    )
    graph = parse_file(str(path), strict=True)
    targets = [
        graph.nodes[e.target_id]
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES
        and dict(graph.nodes[e.target_id].attributes).get("declaration_role") == "function"
    ]
    assert {n.name for n in targets} == {"h", "senc", "aenc", "pk"}
    assert all(dict(n.attributes)["builtin"] is True for n in targets)


@pytest.mark.parametrize(
    "line", ["#ifdef TEST", "predicates: P(x) <=> T", "macros: f(x) = x", "process: 0"]
)
def test_unsupported_language_boundaries_are_targeted(tmp_path: Path, line: str) -> None:
    path = tmp_path / "unsupported.spthy"
    path.write_text(f"theory Unsupported begin\n{line}\nend\n")
    assert any(
        d.kind == "unsupported" and "Tamarin" in d.message
        for d in parse_file(str(path)).parse_diagnostics
    )
