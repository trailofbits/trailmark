"""Controls for syntax adapters' shared evidence and conservative linking."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from tree_sitter import Node
from tree_sitter_language_pack import get_parser

from trailmark.models.edges import EdgeKind
from trailmark.models.nodes import NodeKind, Parameter
from trailmark.parsers._formal import Scope, Source, lookup_tiers, walk
from trailmark.parsers._formal_linking import link_symbols
from trailmark.query.api import QueryEngine


def source(text: str, name: str = "sample") -> tuple[Source, Node]:
    root = get_parser("python").parse(text.encode()).root_node
    return Source(root, f"{name}.py", "fixture"), root


def test_roles_keep_specification_references_separate_from_calls() -> None:
    src, root = source("def target(): pass\ndef caller(): target()\n")
    target, caller = root.named_children
    target_id = src.declare(target, "target", "function", attributes=(("executable", True),))
    caller_id = src.declare(caller, "caller", "function", attributes=(("executable", True),))
    body = caller.child_by_field_name("body")
    assert body is not None
    name = next(n for n in walk(body) if n.type == "call").child_by_field_name("function")
    assert name is not None
    for context in ("body", "contract"):
        src.references(
            caller_id,
            body,
            frozenset({"identifier"}),
            context=context,
            application_heads=frozenset({name.start_byte}),
        )
    link_symbols(src.graph)
    calls = [e for e in src.graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 1 and calls[0].target_id == target_id
    assert dict(calls[0].attributes)["reference_context"] == "body"
    engine = QueryEngine.from_graph(src.graph)
    assert {n["id"] for n in engine.reachable_from("caller")} == {target_id}
    assert {n["id"] for n in engine.reachable_from("caller", edge_kinds=("references",))} == {
        target_id
    }


def test_binding_interval_blocks_qualified_and_bare_uses_only_inside_scope() -> None:
    src, root = source("target()\ntarget.member()\ntarget()\n")
    src.declare(root, "target", "function")
    owner = src.declare(root, "caller", "function")
    scope = Scope(frozenset({"target"}), 0, root.named_children[2].start_byte)
    src.references(owner, root, frozenset({"identifier"}), scopes=(scope,))
    link_symbols(src.graph)
    references = [e for e in src.graph.edges if e.kind == EdgeKind.REFERENCES]
    assert len(references) == 1 and references[0].location is not None
    assert references[0].location.start_line == 3


def test_proof_intervals_preserve_references_without_runtime_calls() -> None:
    src, root = source("target()\ntarget()\n")
    target = src.declare(root, "target", "function", attributes=(("executable", True),))
    owner = src.declare(root, "caller", "function", attributes=(("executable", True),))
    heads = frozenset(n.start_byte for n in walk(root) if n.type == "identifier")
    scope = Scope(frozenset(), 0, root.named_children[0].end_byte, context="proof")
    src.references(
        owner,
        root,
        frozenset({"identifier"}),
        scopes=(scope,),
        application_heads=heads,
        spelling=lambda n: n.text.decode() if n.text else None,
    )
    link_symbols(src.graph)
    refs = [e for e in src.graph.edges if e.kind == EdgeKind.REFERENCES]
    calls = [e for e in src.graph.edges if e.kind == EdgeKind.CALLS]
    assert len(refs) == 2 and all(e.target_id == target for e in refs)
    assert len(calls) == 1 and calls[0].location is not None
    assert calls[0].location.start_line == 2


def test_aliases_are_exact_and_opened_names_share_an_ambiguity_tier() -> None:
    assert lookup_tiers("B.f", "Local", (), {"B": "Base"}) == [["Base.f"]]
    assert lookup_tiers("f", "Outer.Inner", ("A", "B"), {}) == [
        ["Outer.Inner.f"],
        ["Outer.f"],
        ["f", "A.f", "B.f"],
    ]


def test_only_imported_files_participate_and_private_symbols_stay_local() -> None:
    caller, root = source("target\nsecret\n", "caller")
    owner = caller.declare(root, "caller", "function")
    caller.references(owner, root, frozenset({"identifier"}), opens=("Base",))
    base, base_root = source("pass\n", "base")
    base.module_name("Base")
    expected = base.declare(base_root, "target", "function", namespace="Base")
    base.declare(base_root, "secret", "function", namespace="Base", attributes=(("private", True),))
    hidden, hidden_root = source("pass\n", "hidden")
    hidden.declare(hidden_root, "target", "function", namespace="Base")
    caller.import_(root, "Base")
    # An unrelated file with the same spelling must not manufacture ambiguity.
    hidden.graph.nodes[hidden.module] = replace(hidden.graph.nodes[hidden.module], attributes=())
    caller.graph.merge(base.graph)
    caller.graph.merge(hidden.graph)
    link_symbols(caller.graph)
    assert [e.target_id for e in caller.graph.edges if e.kind == EdgeKind.REFERENCES] == [expected]
    assert all(e.target_id in caller.graph.nodes for e in caller.graph.edges)


def test_unresolved_and_ambiguous_references_are_not_guessed() -> None:
    src, root = source("f\nmissing\n")
    src.declare(root, "f", "function", namespace="A")
    src.declare(root, "f", "function", namespace="B")
    owner = src.declare(root, "caller", "function")
    src.references(owner, root, frozenset({"identifier"}), opens=("A", "B"))
    link_symbols(src.graph)
    assert not any(e.kind == EdgeKind.REFERENCES for e in src.graph.edges)


def test_ordered_declarations_do_not_capture_an_earlier_import_reference() -> None:
    src, root = source("f\npass\n", "caller")
    src.ordered_declarations()
    src.declare(root.named_children[1], "f", "function", namespace="Local")
    owner = src.declare(root, "caller", "function")
    base, base_root = source("pass\n", "base")
    base.module_name("Base")
    expected = base.declare(base_root, "f", "function", namespace="Base")
    src.import_(root, "Base")
    src.references(
        owner,
        root.named_children[0],
        frozenset({"identifier"}),
        namespace="Local",
        scopes=(Scope(frozenset(), 0, 1, ("Base",)),),
    )
    src.graph.merge(base.graph)
    link_symbols(src.graph)
    assert [e.target_id for e in src.graph.edges if e.kind == EdgeKind.REFERENCES] == [expected]


def test_signatures_correspond_without_overwriting_source_evidence() -> None:
    src, root = source("pass\n")
    implementation = src.declare(root, "f", "function", namespace="Base")
    signature = src.declare(
        root, "f", "signature", namespace="Base", attributes=(("signature", True),)
    )
    assert signature != implementation
    link_symbols(src.graph)
    correspondence = [e for e in src.graph.edges if e.kind == EdgeKind.CORRESPONDS_TO]
    assert [(e.source_id, e.target_id) for e in correspondence] == [(signature, implementation)]
    before = src.graph.edges.copy()
    link_symbols(src.graph)
    assert src.graph.edges == before


def test_unsupported_regions_are_visible_to_source_diagnostics() -> None:
    src, root = source("pass\n")
    src.unsupported(root, "Embedded language requires a separate frontend")
    assert src.graph.parse_diagnostics[0].kind == "unsupported"
    assert src.graph.parse_diagnostics[0].location.start_line == 1


def test_empty_import_target_remains_an_explicit_proxy() -> None:
    src, root = source("pass\n")
    src.import_(root, "External")
    link_symbols(src.graph)
    imported = next(e.target_id for e in src.graph.edges if e.kind == EdgeKind.IMPORTS)
    assert imported in src.graph.nodes
    assert src.graph.nodes[imported].kind == NodeKind.MODULE


def test_shared_loader_symbol_is_part_of_native_cache_identity(tmp_path: Path) -> None:
    from trailmark.tree_sitter_custom._native import NativeGrammar

    (tmp_path / "binding.c").write_text("binding")
    a = NativeGrammar(tmp_path, ("binding.c",), "tree_sitter_a")
    b = NativeGrammar(tmp_path, ("binding.c",), "tree_sitter_b")
    assert a.binding_path() != b.binding_path()
    assert "-DTRAILMARK_LANGUAGE=tree_sitter_a" in a.compiler_flags()


def test_argument_relevance_does_not_override_contract_or_proof_context() -> None:
    src, root = source("target()\n")
    target = src.declare(root, "target", "function", attributes=(("executable", True),))
    owner = src.declare(root, "caller", "function", attributes=(("executable", True),))
    proof = Scope(frozenset(), 0, root.end_byte, context="proof")
    argument = Scope(frozenset(), 0, root.end_byte, context="argument")
    for context, scopes in (
        ("body", (argument,)),
        ("contract", (argument,)),
        ("body", (proof, argument)),
    ):
        src.references(
            owner,
            root,
            frozenset({"identifier"}),
            context=context,
            scopes=scopes,
            application_heads=frozenset({0}),
        )
    link_symbols(src.graph)
    refs = [e for e in src.graph.edges if e.kind == EdgeKind.REFERENCES]
    assert {dict(e.attributes)["reference_context"] for e in refs} == {
        "argument",
        "contract",
        "proof",
    }
    assert all(e.target_id == target for e in refs)
    assert not any(e.kind == EdgeKind.CALLS for e in src.graph.edges)
    assert lookup_tiers("file.Base.f", "Use", (), {"file": "Other", "file.Base": "Base"}) == [
        ["Base.f"]
    ]


@pytest.mark.parametrize("executable", [True, False, None])
def test_nested_calls_require_computational_enclosing_callees(executable: bool | None) -> None:
    src, root = source("outer(inner(leaf()))\n")
    owner = src.declare(root, "caller", "function", attributes=(("executable", True),))
    src.declare(root, "outer", "function", attributes=(("executable", executable),))
    for name in ("inner", "leaf"):
        src.declare(root, name, "function", attributes=(("executable", True),))
    scopes = []
    heads = set()
    for node in walk(root):
        if node.type != "call":
            continue
        head, args = node.child_by_field_name("function"), node.child_by_field_name("arguments")
        assert head is not None and args is not None
        heads.add(head.start_byte)
        scopes.extend(
            Scope(
                frozenset(),
                arg.start_byte,
                arg.end_byte,
                context="argument",
                callee=head.start_byte,
            )
            for arg in args.named_children
        )
    src.references(
        owner,
        root,
        frozenset({"identifier"}),
        scopes=tuple(scopes),
        application_heads=frozenset(heads),
    )
    link_symbols(src.graph)
    calls = {src.graph.nodes[e.target_id].name for e in src.graph.edges if e.kind == EdgeKind.CALLS}
    assert calls == ({"outer", "inner", "leaf"} if executable else set())
    assert len([e for e in src.graph.edges if e.kind == EdgeKind.REFERENCES]) == 3
    assert all(
        not key.startswith("_formal_") for edge in src.graph.edges for key, _ in edge.attributes
    )
    before = src.graph.edges.copy()
    link_symbols(src.graph)
    assert src.graph.edges == before


def test_unknown_enclosing_callee_keeps_nested_application_as_reference() -> None:
    src, root = source("unknown(leaf())\n")
    owner = src.declare(root, "caller", "function", attributes=(("executable", True),))
    leaf = src.declare(root, "leaf", "function", attributes=(("executable", True),))
    scope = Scope(frozenset(), 8, root.end_byte, context="argument", callee=0)
    src.references(
        owner, root, frozenset({"identifier"}), scopes=(scope,), application_heads=frozenset({0, 8})
    )
    link_symbols(src.graph)
    assert [
        (edge.kind, edge.target_id) for edge in src.graph.edges if edge.kind == EdgeKind.REFERENCES
    ] == [(EdgeKind.REFERENCES, leaf)]
    assert not any(edge.kind == EdgeKind.CALLS for edge in src.graph.edges)


def test_explicit_reexports_resolve_transitively_and_terminate_cycles() -> None:
    caller, root = source("f\n", "caller")
    owner = caller.declare(root, "caller", "function")
    caller.import_(root, "B")
    caller.references(owner, root, frozenset({"identifier"}), opens=("B",))
    middle, middle_root = source("pass\n", "middle")
    middle.module_name("B")
    middle.import_(middle_root, "C")
    middle.reexports("B", ("C",))
    base, base_root = source("pass\n", "base")
    base.module_name("C")
    base.import_(base_root, "B")
    base.reexports("C", ("B",))
    target = base.declare(base_root, "f", "function", namespace="C")
    caller.graph.merge(middle.graph)
    caller.graph.merge(base.graph)
    link_symbols(caller.graph)
    assert [e.target_id for e in caller.graph.edges if e.kind == EdgeKind.REFERENCES] == [target]


def test_declaration_aliases_retain_one_identity() -> None:
    src, root = source("C\nType.C\n")
    owner = src.declare(root, "caller", "function")
    target = src.declare(
        root, "C", "constructor", namespace="Type", attributes=(("qualified_aliases", '["C"]'),)
    )
    src.references(owner, root, frozenset({"identifier", "attribute"}))
    link_symbols(src.graph)
    refs = [e.target_id for e in src.graph.edges if e.kind == EdgeKind.REFERENCES]
    assert refs == [target, target]


def test_ghost_arguments_do_not_erase_computational_sibling_arguments() -> None:
    src, root = source("outer(first(), second())\n")
    owner = src.declare(root, "caller", "function", attributes=(("executable", True),))
    src.declare(
        root,
        "outer",
        "function",
        parameters=(Parameter("x"), Parameter("y")),
        attributes=(("executable", True), ("ghost_parameters", '["x"]')),
    )
    for name in ("first", "second"):
        src.declare(root, name, "function", attributes=(("executable", True),))
    call = next(n for n in walk(root) if n.type == "call")
    args = call.child_by_field_name("arguments")
    assert args is not None
    scopes = tuple(
        Scope(frozenset(), arg.start_byte, arg.end_byte, context="argument", callee=0, argument=i)
        for i, arg in enumerate(args.named_children)
    )
    heads = frozenset(n.start_byte for n in walk(root) if n.type == "identifier")
    src.references(owner, root, frozenset({"identifier"}), scopes=scopes, application_heads=heads)
    link_symbols(src.graph)
    assert {
        src.graph.nodes[e.target_id].name for e in src.graph.edges if e.kind == EdgeKind.CALLS
    } == {"outer", "second"}


def test_opaque_walk_reports_outer_boundaries_without_descending() -> None:
    _, root = source("def outer():\n def inner():\n  pass\n")
    visited = walk(root, frozenset({"function_definition"}), include_skipped=True)
    assert [node.type for node in visited] == ["module", "function_definition"]
    assert visited[-1].start_point.row == 0
    assert walk(root, frozenset({"function_definition"})) == [root]
