from pathlib import Path

import pytest

from trailmark.models.edges import EdgeKind
from trailmark.parse import parse_directory, parse_file


@pytest.mark.mutation_fast
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


@pytest.mark.mutation_fast
def test_fact_arity_and_bare_constants(tmp_path: Path) -> None:
    path = tmp_path / "arity.spthy"
    path.write_text(
        "theory Arity begin\nfunctions: x/0\n"
        "rule R: [ In(x) ] --[ F(x), F(x,x) ]-> [ Out(x) ]\nend\n"
    )
    graph = parse_file(str(path), strict=True)
    assert {"F/1", "F/2"} <= {n.name for n in graph.nodes.values()}
    assert (
        sum(
            e.kind == EdgeKind.REFERENCES and graph.nodes[e.target_id].name == "x"
            for e in graph.edges
        )
        == 5
    )


@pytest.mark.mutation_fast
@pytest.mark.parametrize("operator", ["^", "*", "+", "XOR"])
def test_operator_let_values_and_macro_bodies_keep_dependencies(
    tmp_path: Path, operator: str
) -> None:
    path = tmp_path / "Terms.spthy"
    path.write_text(
        "theory Terms begin\nbuiltins: diffie-hellman, xor, multiset\n"
        "functions: k/0, f/1\n"
        f"macros: m(x) = x {operator} f(k)\n"
        f"rule R: let x = y {operator} f(k) in [In(y)] --> [Out(m(x))]\nend\n"
    )
    graph = parse_directory(str(tmp_path), language="tamarin", strict=True)
    pairs = {
        (graph.nodes[e.source_id].name, graph.nodes[e.target_id].name)
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES
    }
    assert {("m", "f"), ("m", "k"), ("R", "f"), ("R", "k")} <= pairs
    assert ("m", operator) in pairs and ("R", operator) in pairs


def test_explicit_all_traces_quantifier(tmp_path: Path) -> None:
    path = tmp_path / "Traces.spthy"
    path.write_text(
        "theory Traces begin\nrule R: [In(x)] --[Seen(x)]-> [Out(x)]\n"
        'lemma seen: all-traces "All x #i. Seen(x) @ #i ==> Ex #j. K(x) @ #j"\nend\n'
    )
    graph = parse_file(str(path), strict=True)
    assert any(n.name == "seen" for n in graph.nodes.values())


@pytest.mark.mutation_fast
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


@pytest.mark.mutation_fast
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


def test_unsupported_language_boundaries_are_targeted(tmp_path: Path) -> None:
    line = "process: 0"
    path = tmp_path / "unsupported.spthy"
    path.write_text(f"theory Unsupported begin\n{line}\nend\n")
    assert any(
        d.kind == "unsupported" and "Tamarin" in d.message
        for d in parse_file(str(path)).parse_diagnostics
    )


@pytest.mark.mutation_fast
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_conditionals_preserve_all_branches_and_the_next_rule(tmp_path: Path, newline: str) -> None:
    path = tmp_path / "conditional.spthy"
    text = (
        "theory Conditional begin\n#define A\n#ifdef (A & B) | not C\n"
        "rule First: [] --> []\n#ifdef NESTED\nrule Nested: [] --> []\n"
        "#else\nrule Alternative: [] --> []\n#endif\n#else\n"
        "rule Other: [] --> []\n#endif\nrule After: [] --> []\nend\n"
    )
    path.write_bytes(text.replace("\n", newline).encode())
    graph = parse_file(str(path), strict=True)
    rules = {
        n.name: dict(n.attributes)
        for n in graph.nodes.values()
        if dict(n.attributes).get("declaration_role") == "rule"
    }
    assert set(rules) == {"First", "Nested", "Alternative", "Other", "After"}
    assert all(
        attrs.get("conditional", False) is (name != "After") for name, attrs in rules.items()
    )


@pytest.mark.mutation_fast
def test_macros_predicates_and_constant_dependencies(tmp_path: Path) -> None:
    path = tmp_path / "reusable.spthy"
    path.write_text(
        "theory Reusable begin\nfunctions: h/1, c/0, x/0\n"
        "macros: inner(x) = h(x), outer(x) = inner(<x,c>), unit() = c\n"
        "predicates: Seen(x) <=> Ex #i. Sent(h(x)) @ i, Both(x) <=> Seen(x) & x = c\n"
        "rule Send: [] --[ Sent(outer(c)), _restrict(Both(c)) ]-> [ Out(unit()) ]\n"
        'restriction Allowed: "All x. Both(x) ==> x = c"\n'
        'lemma Witness: "Both(c)"\nend\n'
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
        ("outer", "c"),
        ("unit", "c"),
        ("Send", "outer"),
        ("Send", "unit"),
        ("Send", "Both"),
        ("Seen", "Sent/1"),
        ("Seen", "h"),
        ("Both", "Seen"),
        ("Both", "c"),
        ("Allowed", "Both"),
        ("Witness", "Both"),
    } <= refs
    assert not any(target == "x" for _, target in refs)
    assert not any(e.kind == EdgeKind.CALLS for e in graph.edges)
    assert next(n for n in graph.nodes.values() if n.name == "outer").parameters[0].name == "x"
    assert not any(n.name == "Both/1" for n in graph.nodes.values())


@pytest.mark.mutation_fast
def test_constant_aliases_respect_sorts_and_local_bindings(tmp_path: Path) -> None:
    path = tmp_path / "constants.spthy"
    path.write_text(
        "theory Constants begin\nfunctions: c/0, f/1\n"
        "rule Bare: [] --> [Out(<c,c(),f>)]\n"
        "rule Sorted: [Fr(~c)] --> [Out(<~c,$c,'c',c.1>)]\n"
        "rule Local: let c = f('value') in [] --> [Out(c)]\n"
        'lemma Bound: "All c #i. Event(c) @ i ==> c = c"\nend\n'
    )
    graph = parse_file(str(path), strict=True)
    refs = [
        (graph.nodes[e.source_id].name, graph.nodes[e.target_id].name)
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES
    ]
    assert refs.count(("Bare", "c")) == 2
    assert ("Bare", "f") not in refs
    assert ("Local", "f") in refs
    assert not any(
        source in {"Sorted", "Local", "Bound"} and target == "c" for source, target in refs
    )


@pytest.mark.mutation_fast
def test_included_fragments_link_transitively_by_path(tmp_path: Path) -> None:
    for directory in ("left", "right"):
        (tmp_path / directory).mkdir()
        (tmp_path / directory / "defs.spthy").write_text("functions: secret/0 [private]\n")
    (tmp_path / "left" / "proofs.spthy").write_text(
        '#include "defs.spthy"\nmacros: message() = secret\n'
        'predicate: Good(x) <=> x = secret\nlemma Included: "Good(secret)"\n'
    )
    path = tmp_path / "model.spthy"
    path.write_text(
        'theory Model begin\n#include "left/proofs.spthy"\n'
        "rule Send: [] --> [Out(message())]\nend\n"
    )
    graph = parse_directory(str(tmp_path), language="tamarin", strict=True)
    refs = [
        (graph.nodes[e.source_id], graph.nodes[e.target_id])
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES
    ]
    assert any(s.name == "Send" and t.name == "message" for s, t in refs)
    assert any(s.name == "Included" and t.name == "Good" for s, t in refs)
    constants = [
        t for s, t in refs if s.name in {"message", "Good", "Included"} and t.name == "secret"
    ]
    assert len(constants) == 3
    assert all(Path(n.location.file_path).parent.name == "left" for n in constants)
    assert all(dict(n.attributes)["attacker_private"] for n in constants)
    imports = [e for e in graph.edges if e.kind == EdgeKind.IMPORTS]
    assert len(imports) == 2 and all(not e.target_id.startswith("@import:") for e in imports)
    # Single-file analysis reports the dependency but does not read other files.
    single = parse_file(str(path), strict=True)
    assert any(
        e.target_id.startswith("@import:") for e in single.edges if e.kind == EdgeKind.IMPORTS
    )


@pytest.mark.parametrize("directive", ['#include "missing.spthy"', "#define FLAG", "process:"])
def test_directives_do_not_consume_the_next_line(tmp_path: Path, directive: str) -> None:
    path = tmp_path / "directive.spthy"
    path.write_text(f"theory Directives begin\n{directive}\nrule Retained: [] --> []\nend\n")
    graph = parse_file(str(path))
    assert any(n.name == "Retained" for n in graph.nodes.values())


@pytest.mark.parametrize("body", ["#ifdef A\nrule R: [] --> []", "#endif\nrule R: [] --> []"])
def test_unbalanced_conditionals_remain_diagnostics(tmp_path: Path, body: str) -> None:
    path = tmp_path / "unbalanced.spthy"
    path.write_text(f"theory Unbalanced begin\n{body}\nend\n")
    with pytest.raises(ValueError, match="diagnostic"):
        parse_file(str(path), strict=True)


def test_parenthesized_quantifiers_do_not_shadow_following_constants(tmp_path: Path) -> None:
    path = tmp_path / "quantifiers.spthy"
    path.write_text(
        "theory Quantifiers begin\nfunctions: c/0\n"
        'lemma L: "(All c #i. F(c) @ i ==> c = c) & c = c"\nend\n'
    )
    graph = parse_file(str(path), strict=True)
    assert (
        sum(
            e.kind == EdgeKind.REFERENCES and graph.nodes[e.target_id].name == "c"
            for e in graph.edges
        )
        == 2
    )


@pytest.mark.mutation_fast
def test_fact_roles_persistence_and_common_attributes(tmp_path: Path) -> None:
    path = tmp_path / "Roles.spthy"
    path.write_text(
        "theory Roles begin\nbuiltins: reliable-channel, hashing, dest-symmetric-encryption\n"
        "functions: d/1 [destructor]\n"
        "rule R [no_derivcheck,colour=ffffff]: [!State(x)] --[ Seen(x), ]-> [Out(h(x))]\n"
        'axiom A: "All x #i. Seen(x)@i ==> Seen(x)@i"\n'
        'lemma L: all-traces "All x #i. Seen(x)@i ==> Seen(x)@i"\nend\n'
    )
    graph = parse_file(str(path), strict=True)
    refs = {
        (graph.nodes[e.target_id].name, dict(e.attributes).get("reference_context"))
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES and graph.nodes[e.source_id].name == "R"
    }
    assert {("State/1", "premise"), ("Seen/1", "action"), ("Out/1", "conclusion")} <= refs
    state = next(n for n in graph.nodes.values() if n.name == "State/1")
    assert dict(state.attributes)["persistent"] is True
    assert {"senc", "sdec"} <= {n.name for n in graph.nodes.values()}


@pytest.mark.mutation_fast
def test_include_fragments_share_theory_symbols(tmp_path: Path) -> None:
    (tmp_path / "main.spthy").write_text(
        'theory Main begin\nfunctions: k/0\n#include "rules.spthy"\n'
        'lemma L: "All x #i. Sent(x)@i ==> Sent(x)@i"\nend\n'
    )
    (tmp_path / "rules.spthy").write_text("rule Send: [] --> [Sent(k)]\n")
    graph = parse_directory(str(tmp_path), "tamarin", strict=True)
    assert len([n for n in graph.nodes.values() if n.name == "Sent/1"]) == 1
    refs = {
        (graph.nodes[e.source_id].name, graph.nodes[e.target_id].name)
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES
    }
    assert {("Send", "k"), ("Send", "Sent/1"), ("L", "Sent/1")} <= refs
