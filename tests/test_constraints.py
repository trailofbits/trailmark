"""Constraint storage retains provenance and uncertainty instead of proving source claims."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from trailmark.models import (
    Annotation,
    AnnotationKind,
    ArgumentRef,
    CodeEdge,
    ConstraintBoolean,
    ConstraintEvidence,
    ConstraintExpr,
    ConstraintLiteral,
    ConstraintPredicate,
    ConstraintSlice,
    EdgeConfidence,
    EdgeKind,
    EntrypointKind,
    EntrypointTag,
)
from trailmark.models.constraint_support import CONSTRAINT_LANGUAGES
from trailmark.parse import parse_directory, supported_languages
from trailmark.query.api import QueryEngine
from trailmark.storage.graph_store import GraphStore

pytestmark = pytest.mark.mutation_fast


@pytest.fixture
def engine(tmp_path: Path) -> QueryEngine:
    (tmp_path / "app.py").write_text(
        "def target(value, options=None): return value\n"
        "def left(value, options=None): return target(value, options)\n"
        "def right(value): return target(value)\n"
    )
    return QueryEngine.from_graph(parse_directory(str(tmp_path)))


def literal(value: str) -> ConstraintLiteral:
    return ConstraintLiteral("text", value)


def save(
    engine: QueryEngine,
    caller: str,
    value: str,
    *,
    reviewed: bool = True,
    semantics: str = "python-3.15:text",
) -> None:
    node_id = engine._store.find_node_id(caller)
    assert node_id is not None
    site = engine._store.call_sites(node_id)[0]
    assert engine.annotate_constraint(
        caller,
        site.id,
        ConstraintPredicate(ArgumentRef(0), "equals", (literal(value),)),
        semantics=semantics,
        evidence=(ConstraintEvidence(site.location, site.source_sha256),),
        source="llm",
        reviewed=reviewed,
    )


def test_call_sites_and_claims_follow_the_three_layers(engine: QueryEngine) -> None:
    graph = engine._store._graph
    sites = engine._store.call_sites("app:left")
    assert len(sites) == 1
    assert graph.call_sites[sites[0].id] is sites[0]
    assert [arg.expression for arg in sites[0].arguments] == ["value", "options"]
    result = engine.call_sites("left")[0]
    assert result["bindings"] == [{"target_id": "app:target", "confidence": "certain"}]
    save(engine, "left", "alpha")
    assert len(engine._store.constraints_for("app:left")) == 1
    assert engine.constraints_of("left")[0]["active"] is True
    assert engine.annotations_of("left")[0]["constraint"]["grammar_version"] == 1
    json.dumps(engine.call_sites("left"))
    json.dumps(engine.constraints_of("left"))
    assert engine.call_sites("missing") == []
    assert engine.constraints_of("missing") == []
    assert engine.constraint_summary("missing", 0) == {}


def test_summary_requires_completeness_and_preserves_unconstrained_callers(
    engine: QueryEngine,
) -> None:
    save(engine, "left", "alpha")
    assert engine.constraint_summary("target", 0)["status"] == "unknown"
    summary = engine.constraint_summary("target", 0, assumptions=("complete_callers",))
    assert summary["predicate"] == {"operation": "true", "terms": ()}
    assert "unconstrained_incoming_argument" in summary["gaps"]
    save(engine, "right", "beta")
    summary = engine.constraint_summary("target", 0, assumptions=("complete_callers",))
    assert summary["status"] == "conditional"
    assert "unconstrained_incoming_argument" not in summary["gaps"]
    assert summary["predicate"]["operation"] == "or"
    assert {term["values"][0]["value"] for term in summary["predicate"]["terms"]} == {
        "alpha",
        "beta",
    }
    assert all(
        term["subject"] == {"parameter": 0, "slices": ()} for term in summary["predicate"]["terms"]
    )
    assert (
        engine.constraint_summary("target", 1, assumptions=("complete_callers",))["status"]
        == "unknown"
    )


def test_asserted_is_not_automatically_reviewed(engine: QueryEngine) -> None:
    save(engine, "left", "alpha", reviewed=False)
    save(engine, "right", "beta", reviewed=False)
    summary = engine.constraint_summary("target", 0, assumptions=("complete_callers",))
    assert summary["status"] == "unknown"
    assert "asserted_constraint_not_accepted" in summary["gaps"]
    accepted = engine.constraint_summary(
        "target", 0, assumptions=("complete_callers", "accept_asserted_constraints")
    )
    assert accepted["status"] == "conditional"
    assert accepted["assumptions"] == ("complete_callers", "accept_asserted_constraints")


@pytest.mark.parametrize("different_sites", [False, True])
def test_summary_does_not_combine_different_semantics_profiles(
    engine: QueryEngine, different_sites: bool
) -> None:
    save(engine, "left", "alpha")
    save(
        engine,
        "right",
        "beta",
        semantics="python-3.15:deployment-B" if different_sites else "python-3.15:text",
    )
    if not different_sites:
        save(engine, "left", "gamma", semantics="python-3.15:deployment-B")

    summary = engine.constraint_summary("target", 0, assumptions=("complete_callers",))
    assert summary["status"] == "unknown"
    assert summary["predicate"] == {"operation": "true", "terms": ()}
    assert "conflicting_semantics_profiles" in summary["gaps"]
    assert summary["semantics"] == ("python-3.15:deployment-B", "python-3.15:text")
    assert all(claim["active"] for claim in engine.constraints_of("left"))
    assert all(claim["active"] for claim in engine.constraints_of("right"))


def test_added_callers_invalidate_prior_claims_without_erasing_them(engine: QueryEngine) -> None:
    save(engine, "left", "alpha")
    graph = engine._store._graph
    graph.edges.append(CodeEdge("app:right", "app:target", EdgeKind.CALLS))
    assert engine.constraints_of("left")[0]["active"] is False
    summary = engine.constraint_summary("target", 0, assumptions=("complete_callers",))
    assert summary["status"] == "unknown"
    assert "stale_constraint" in summary["gaps"]
    assert "incoming_call_evidence_incomplete" in summary["gaps"]
    restored = QueryEngine.from_snapshot(engine.to_snapshot())
    assert restored.constraints_of("left")[0]["active"] is False


def test_changing_only_dispatch_confidence_invalidates_a_claim(engine: QueryEngine) -> None:
    save(engine, "left", "alpha")
    graph = engine._store._graph
    index = next(
        index
        for index, edge in enumerate(graph.edges)
        if edge.source_id == "app:left" and edge.kind == EdgeKind.CALLS
    )
    graph.edges[index] = replace(graph.edges[index], confidence=EdgeConfidence.UNCERTAIN)
    assert engine.constraints_of("left")[0]["active"] is False


def test_dictionary_order_does_not_invalidate_claim_context(engine: QueryEngine) -> None:
    save(engine, "left", "alpha")
    graph = engine._store._graph
    graph.nodes = dict(reversed(graph.nodes.items()))
    graph.call_sites = dict(reversed(graph.call_sites.items()))
    graph.parameter_bindings = dict(reversed(graph.parameter_bindings.items()))
    assert engine.constraints_of("left")[0]["active"] is True
    restored = QueryEngine.from_snapshot(engine.to_snapshot())
    assert restored.constraints_of("left")[0]["active"] is True


@pytest.mark.parametrize("depth", [128, 129])
def test_constraint_nesting_limit(engine: QueryEngine, depth: int) -> None:
    site = engine._store.call_sites("app:left")[0]
    predicate: ConstraintExpr = ConstraintPredicate(ArgumentRef(0), "equals", (literal("x"),))
    for _ in range(depth):
        predicate = ConstraintBoolean("not", (predicate,))

    def annotate() -> bool:
        return engine.annotate_constraint(
            "left",
            site.id,
            predicate,
            semantics="python",
            evidence=(ConstraintEvidence(site.location, site.source_sha256),),
        )

    if depth == 128:
        assert annotate()
    else:
        with pytest.raises(ValueError, match="nesting"):
            annotate()


def test_first_absent_operand_is_rejected(engine: QueryEngine) -> None:
    site = engine._store.call_sites("app:left")[0]
    with pytest.raises(ValueError, match="operand"):
        engine.annotate_constraint(
            "left",
            site.id,
            ConstraintPredicate(ArgumentRef(len(site.arguments)), "equals", (literal("x"),)),
            semantics="python",
            evidence=(ConstraintEvidence(site.location, site.source_sha256),),
        )


@pytest.mark.parametrize("parameter", [-1, 2, True, 0.0, "0"])
def test_summary_rejects_invalid_parameter_indices(engine: QueryEngine, parameter: int) -> None:
    with pytest.raises(ValueError, match="parameter"):
        engine.constraint_summary("target", parameter, assumptions=("complete_callers",))


@pytest.mark.parametrize("assumptions", [["complete_callers"], ("",), (" ",), ("x", "x"), (1,)])
def test_summary_rejects_invalid_assumptions(
    engine: QueryEngine, assumptions: tuple[str, ...]
) -> None:
    with pytest.raises(ValueError, match="assumptions"):
        engine.constraint_summary("target", 0, assumptions=assumptions)


def test_source_bound_snapshot_round_trip_needs_no_source_read(engine: QueryEngine) -> None:
    save(engine, "left", "alpha")
    save(engine, "right", "beta")
    before = engine.constraint_summary("target", 0, assumptions=("complete_callers",))
    snapshot = engine.to_snapshot()
    assert json.loads(snapshot)["snapshot_version"] == 2
    source = Path(engine.call_sites("left")[0]["location"]["file_path"])
    source.unlink()
    restored = QueryEngine.from_snapshot(snapshot)
    assert restored.call_sites("left") == engine.call_sites("left")
    assert restored.constraint_summary("target", 0, assumptions=("complete_callers",)) == before
    with pytest.raises(ValueError):
        QueryEngine.from_snapshot(snapshot, verify_sources=True)


def test_validation_does_not_promote_provenance_to_semantic_truth(engine: QueryEngine) -> None:
    site = engine._store.call_sites("app:left")[0]
    evidence = (ConstraintEvidence(site.location, site.source_sha256),)
    with pytest.raises(ValueError, match="operand"):
        engine.annotate_constraint(
            "left",
            site.id,
            ConstraintPredicate(ArgumentRef(99), "equals", (literal("x"),)),
            semantics="python",
            evidence=evidence,
        )
    with pytest.raises(ValueError, match="owner"):
        engine.annotate_constraint(
            "right",
            site.id,
            ConstraintPredicate(ArgumentRef(0), "equals", (literal("x"),)),
            semantics="python",
            evidence=evidence,
        )
    with pytest.raises(ValueError, match="digest"):
        engine.annotate_constraint(
            "left",
            site.id,
            ConstraintPredicate(ArgumentRef(0), "equals", (literal("x"),)),
            semantics="python",
            evidence=(ConstraintEvidence(site.location, "f" * 64),),
        )
    assert engine.constraints_of("left") == []


def test_negating_an_unrelated_argument_does_not_prove_false(engine: QueryEngine) -> None:
    site = engine._store.call_sites("app:left")[0]
    unrelated = ConstraintBoolean(
        "not", (ConstraintPredicate(ArgumentRef(1), "equals", (literal("x"),)),)
    )
    engine.annotate_constraint(
        "left",
        site.id,
        unrelated,
        semantics="python",
        evidence=(ConstraintEvidence(site.location, site.source_sha256),),
        reviewed=True,
    )
    save(engine, "right", "beta")
    assert (
        engine.constraint_summary("target", 0, assumptions=("complete_callers",))["status"]
        == "unknown"
    )


def test_claim_assumptions_are_not_silently_applied(engine: QueryEngine) -> None:
    site = engine._store.call_sites("app:left")[0]
    engine.annotate_constraint(
        "left",
        site.id,
        ConstraintPredicate(ArgumentRef(0), "equals", (literal("x"),)),
        semantics="python",
        evidence=(ConstraintEvidence(site.location, site.source_sha256),),
        assumptions=("validator_is_builtin",),
        reviewed=True,
    )
    save(engine, "right", "beta")
    result = engine.constraint_summary("target", 0, assumptions=("complete_callers",))
    assert "constraint_assumptions_unsatisfied" in result["gaps"]
    assert result["status"] == "unknown"


def test_claim_accepts_exactly_its_required_assumptions(engine: QueryEngine) -> None:
    save(engine, "left", "alpha")
    save(engine, "right", "beta")
    graph = engine._store._graph
    for caller in ("app:left", "app:right"):
        annotation = graph.annotations[caller][0]
        assert annotation.constraint is not None
        graph.annotations[caller][0] = replace(
            annotation,
            constraint=replace(annotation.constraint, assumptions=("complete_callers",)),
        )
    summary = engine.constraint_summary("target", 0, assumptions=("complete_callers",))
    assert summary["status"] == "conditional"
    assert "constraint_assumptions_unsatisfied" not in summary["gaps"]


@pytest.mark.parametrize("mapping", ["-1", "2", "01", "+0", "0 ", "x"])
def test_invalid_mapping_cannot_override_a_known_binding(engine: QueryEngine, mapping: str) -> None:
    save(engine, "left", "alpha")
    save(engine, "right", "beta")
    site = engine._store.call_sites("app:left")[0]
    result = engine.constraint_summary(
        "target", 0, assumptions=("complete_callers", f"argument_mapping:{site.id}:0={mapping}")
    )
    assert result["status"] == "unknown"
    assert "parameter_binding_unknown" in result["gaps"]


@pytest.mark.parametrize(
    ("signature", "call"),
    [
        ("x", "target('a', x='b')"),
        ("x", "target(x='a', x='b')"),
        ("x, /", "target(x='a')"),
    ],
)
def test_invalid_named_binding_cannot_supply_a_parameter_constraint(
    tmp_path: Path, signature: str, call: str
) -> None:
    (tmp_path / "app.py").write_text(
        f"def target({signature}): return x\ndef caller(): return {call}\n"
    )
    engine = QueryEngine.from_directory(str(tmp_path))
    save(engine, "caller", "a")
    result = engine.constraint_summary("target", 0, assumptions=("complete_callers",))
    assert result["status"] == "unknown"
    assert "parameter_binding_unknown" in result["gaps"]


def test_repeated_occurrences_keep_different_constraints(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        "def target(x): return x\ndef caller(x):\n    target('a')\n    return target('b')\n"
    )
    engine = QueryEngine.from_directory(str(tmp_path))
    sites = engine.call_sites("caller")
    assert len(sites) == 2 and sites[0]["id"] != sites[1]["id"]
    assert [site["arguments"][0]["expression"] for site in sites] == ["'a'", "'b'"]
    assert len({binding["target_id"] for site in sites for binding in site["bindings"]}) == 1
    assert engine.annotate("caller", AnnotationKind.AUDIT_NOTE, "Two independent call occurrences")
    for index in (0, 1):
        site = engine._store.call_sites("app:caller")[index]
        assert engine.annotate_constraint(
            "caller",
            site.id,
            ConstraintPredicate(ArgumentRef(0), "equals", (literal("ab"[index]),)),
            semantics="python",
            evidence=(ConstraintEvidence(site.location, site.source_sha256),),
            reviewed=True,
        )
        summary = engine.constraint_summary("target", 0, assumptions=("complete_callers",))
        assert summary["status"] == ("unknown" if index == 0 else "conditional")
    assert summary["predicate"]["operation"] == "or"
    assert [term["values"][0]["value"] for term in summary["predicate"]["terms"]] == ["a", "b"]


def test_named_arguments_preserve_labels_and_bind_by_name(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        "def target(x, y): return x\ndef caller(): return target(y='b', x='a')\n"
    )
    engine = QueryEngine.from_directory(str(tmp_path))
    site = engine._store.call_sites("app:caller")[0]
    assert [(arg.name, arg.expression) for arg in site.arguments] == [("y", "'b'"), ("x", "'a'")]
    engine.annotate_constraint(
        "caller",
        site.id,
        ConstraintPredicate(ArgumentRef(1), "equals", (literal("a"),)),
        semantics="python",
        evidence=(ConstraintEvidence(site.location, site.source_sha256),),
        reviewed=True,
    )
    result = engine.constraint_summary("target", 0, assumptions=("complete_callers",))
    assert result["predicate"]["values"][0]["value"] == "a"


def test_all_frontends_have_explicit_capability_profiles(engine: QueryEngine) -> None:
    assert set(supported_languages()) == CONSTRAINT_LANGUAGES
    profiles = engine.constraint_support()
    assert {profile["language"] for profile in profiles} == CONSTRAINT_LANGUAGES
    assert not any(profile["automatic_inference"] == "partial" for profile in profiles)


def test_variadic_formal_is_not_confused_with_one_actual_argument(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        "def target(*values): return values\ndef caller(): return target('a', 'b')\n"
    )
    engine = QueryEngine.from_directory(str(tmp_path))
    save(engine, "caller", "a")
    assert engine._store._graph.parameter_bindings["app:target"][0].kind == "var_positional"
    assert not engine._store._graph.parameter_bindings["app:target"][0].value_preserving
    summary = engine.constraint_summary("target", 0, assumptions=("complete_callers",))
    assert summary["status"] == "unknown"
    assert "parameter_binding_unknown" in summary["gaps"]


def test_php_scalar_coercions_do_not_transfer_operand_constraints(tmp_path: Path) -> None:
    (tmp_path / "app.php").write_text(
        "<?php function target(int $value) { return $value; }\n"
        'function caller() { return target("7"); }\n'
    )
    engine = QueryEngine.from_directory(str(tmp_path), language="php")
    site = engine._store.call_sites("app:caller")[0]
    engine.annotate_constraint(
        "caller",
        site.id,
        ConstraintPredicate(ArgumentRef(0), "equals", (ConstraintLiteral("bytes", "37"),)),
        semantics="php-8:byte-strings",
        evidence=(ConstraintEvidence(site.location, site.source_sha256),),
        reviewed=True,
    )
    summary = engine.constraint_summary("target", 0, assumptions=("complete_callers",))
    assert summary["status"] == "unknown"
    assert "parameter_value_conversion_unknown" in summary["gaps"]


def test_unbound_method_receiver_requires_an_explicit_parameter_mapping(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        "class Target:\n    def method(self, value): return value\n"
        "def caller(instance): return Target.method(instance, 7)\n"
    )
    graph = parse_directory(str(tmp_path))
    # Model a target binding established separately from the parser's unresolved proxy.
    graph.edges = [
        replace(edge, target_id="app:Target.method")
        if edge.kind == EdgeKind.CALLS and edge.source_id == "app:caller"
        else edge
        for edge in graph.edges
    ]
    engine = QueryEngine.from_graph(graph)
    site = engine._store.call_sites("app:caller")[0]
    engine.annotate_constraint(
        "caller",
        site.id,
        ConstraintPredicate(ArgumentRef(1), "equals", (ConstraintLiteral("integer", 7),)),
        semantics="python",
        evidence=(ConstraintEvidence(site.location, site.source_sha256),),
        reviewed=True,
    )
    assert (
        engine.constraint_summary("Target.method", 0, assumptions=("complete_callers",))["status"]
        == "unknown"
    )
    mapped = engine.constraint_summary(
        "Target.method", 0, assumptions=("complete_callers", f"argument_mapping:{site.id}:0=1")
    )
    assert mapped["status"] == "conditional"
    assert mapped["predicate"]["values"][0]["value"] == 7


def test_python_decorator_cannot_silently_reuse_the_wrapped_signature(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        "@transform\ndef target(value): return value\ndef caller(): return target('a')\n"
    )
    engine = QueryEngine.from_directory(str(tmp_path))
    save(engine, "caller", "a")
    summary = engine.constraint_summary("target", 0, assumptions=("complete_callers",))
    assert summary["status"] == "unknown"
    assert "parameter_binding_unknown" in summary["gaps"]


def test_non_utf8_operands_preserve_original_bytes_without_asserting_text(tmp_path: Path) -> None:
    source = (
        b'<?php function caller() { return target("\xff"); } '
        b"function target($value) { return $value; }"
    )
    (tmp_path / "app.php").write_bytes(source)
    engine = QueryEngine.from_directory(str(tmp_path), language="php")
    site = engine.call_sites("caller")[0]
    assert site["source_sha256"] == hashlib.sha256(source).hexdigest()
    assert bytes.fromhex(site["syntax_bytes"]) == b'target("\xff")'
    assert bytes.fromhex(site["arguments"][0]["expression_bytes"]) == b'"\xff"'
    assert site["arguments"][0]["expression"] == '"\\xff"'
    assert "source_encoding_unverified" in site["gaps"]
    restored = QueryEngine.from_snapshot(engine.to_snapshot())
    assert restored.call_sites("caller") == [site]
    assert (
        restored.constraint_summary("target", 0, assumptions=("complete_callers",))["status"]
        == "unknown"
    )


def test_keyword_only_parameter_binds_to_its_named_operand(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        "def target(x, *, y): return y\ndef caller(): return target('a', y='b')\n"
    )

    engine = QueryEngine.from_directory(str(tmp_path))
    site = engine._store.call_sites("app:caller")[0]
    assert engine._store._graph.parameter_bindings["app:target"][1].kind == "keyword_only"
    engine.annotate_constraint(
        "caller",
        site.id,
        ConstraintPredicate(ArgumentRef(1), "equals", (literal("b"),)),
        semantics="python",
        evidence=(ConstraintEvidence(site.location, site.source_sha256),),
        reviewed=True,
    )
    assert (
        engine.constraint_summary("target", 1, assumptions=("complete_callers",))["predicate"][
            "values"
        ][0]["value"]
        == "b"
    )


def test_unverified_bindings_require_one_explicit_mapping(tmp_path: Path) -> None:
    (tmp_path / "app.c").write_text(
        "int target(int x) { return x; }\nint caller() { return target(7); }\n"
    )
    engine = QueryEngine.from_directory(str(tmp_path), language="c")
    site = engine._store.call_sites("app:caller")[0]
    engine.annotate_constraint(
        "caller",
        site.id,
        ConstraintPredicate(ArgumentRef(0), "equals", (ConstraintLiteral("integer", 7),)),
        semantics="c17:mathematical-int-values",
        evidence=(ConstraintEvidence(site.location, site.source_sha256),),
        reviewed=True,
    )
    assert (
        engine.constraint_summary("target", 0, assumptions=("complete_callers",))["status"]
        == "unknown"
    )
    mapping = f"argument_mapping:{site.id}:0=0"
    assert (
        engine.constraint_summary("target", 0, assumptions=("complete_callers", mapping))["status"]
        == "conditional"
    )
    assert (
        engine.constraint_summary(
            "target",
            0,
            assumptions=("complete_callers", mapping, f"argument_mapping:{site.id}:0=99"),
        )["status"]
        == "unknown"
    )


def test_entrypoint_is_a_known_unconstrained_entry_route(engine: QueryEngine) -> None:
    engine._store._graph.entrypoints["app:target"] = EntrypointTag(EntrypointKind.API)
    save(engine, "left", "alpha")
    save(engine, "right", "beta")
    result = engine.constraint_summary("target", 0, assumptions=("complete_callers",))
    assert result["status"] == "unknown"
    assert "known_external_entrypoint" in result["gaps"]
    conditional = engine.constraint_summary(
        "target", 0, assumptions=("complete_callers", "exclude_external_entrypoint:app:target")
    )
    assert conditional["status"] == "conditional"


@pytest.mark.parametrize(
    "corruption", ["site_key", "operand", "grammar", "claim_site", "evidence", "binding"]
)
def test_snapshot_rejects_invalid_constraint_references(
    engine: QueryEngine, corruption: str
) -> None:
    save(engine, "left", "alpha")
    snapshot = json.loads(engine.to_snapshot())
    graph = snapshot["graph"]
    key = next(key for key, site in graph["call_sites"].items() if site["caller_id"] == "app:left")
    claim = graph["annotations"]["app:left"][0]["constraint"]
    if corruption == "site_key":
        graph["call_sites"]["wrong"] = graph["call_sites"].pop(key)
    elif corruption == "operand":
        graph["call_sites"][key]["arguments"][0]["index"] = 9
    elif corruption == "grammar":
        claim["grammar_version"] = 2
    elif corruption == "claim_site":
        claim["call_site_id"] = "missing"
    elif corruption == "evidence":
        claim["evidence"][0]["source_sha256"] = "f" * 64
    else:
        graph["parameter_bindings"]["app:target"][0]["name"] = "not-the-parameter"
    with pytest.raises(ValueError):
        QueryEngine.from_snapshot(snapshot)


def test_namespacing_preserves_claim_identity_but_requires_context_review(
    engine: QueryEngine,
) -> None:
    from trailmark.parse import _namespace_root

    save(engine, "left", "alpha")
    graph = engine._store._graph
    _namespace_root(graph, "application")
    restored = QueryEngine.from_snapshot(graph.to_snapshot())
    claim = restored.constraints_of("left")[0]
    assert claim["active"] is False
    assert claim["constraint"]["call_site_id"] == restored.call_sites("left")[0]["id"]


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ConstraintLiteral("integer", True),
        lambda: ConstraintLiteral("bytes", "G0"),
        lambda: ConstraintLiteral("real", float("nan")),
        lambda: ConstraintSlice(-1, None, "bytes"),
        lambda: ConstraintPredicate(ArgumentRef(0), "prefix", (literal("x"),), "bytes"),
        lambda: ConstraintPredicate(
            ArgumentRef(0), "length", (ConstraintLiteral("integer", -1),), "bytes"
        ),
        lambda: ConstraintBoolean("not", ()),
        lambda: Annotation(AnnotationKind.CONSTRAINT, "missing payload", "llm"),
    ],
)
def test_invalid_grammar_is_rejected(factory: Callable[[], object]) -> None:
    with pytest.raises(ValueError):
        factory()


def test_store_and_model_reject_forged_call_site_identity(engine: QueryEngine) -> None:
    graph = engine._store._graph
    site = engine._store.call_sites("app:left")[0]
    with pytest.raises(ValueError):
        graph.add_call_site(replace(site, caller_id="missing"))
    with pytest.raises(ValueError):
        graph.add_call_site(replace(site, syntax="different"))
    restored = GraphStore.from_snapshot(graph.to_snapshot())
    assert restored.call_sites("app:left") == [site]
    assert (
        hashlib.sha256(Path(site.location.file_path).read_bytes()).hexdigest() == site.source_sha256
    )
