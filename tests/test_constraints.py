"""Constraint storage retains provenance and uncertainty instead of proving source claims."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from trailmark.models import (
    Annotation,
    AnnotationKind,
    ArgumentRef,
    BinaryArtifact,
    CallSite,
    CodeEdge,
    CodeGraph,
    CodeUnit,
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
    NodeKind,
    ParameterRef,
    SourceLocation,
)
from trailmark.models.constraint_support import CONSTRAINT_LANGUAGES
from trailmark.models.entrypoints import EntrypointEvidence
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


@pytest.mark.parametrize(
    "predicate",
    [ConstraintBoolean("false"), ConstraintBoolean("not", (ConstraintBoolean("true"),))],
)
@pytest.mark.parametrize("second_caller", [False, True])
@pytest.mark.parametrize("reviewed", [False, True])
def test_impossible_incoming_claim_does_not_establish_dead_code(
    engine: QueryEngine, predicate: ConstraintExpr, second_caller: bool, reviewed: bool
) -> None:
    if not second_caller:
        graph = engine._store._graph
        graph.edges = [edge for edge in graph.edges if edge.source_id != "app:right"]
        engine._store.rebuild_index()
    site = engine._store.call_sites("app:left")[0]
    engine.annotate_constraint(
        "left",
        site.id,
        predicate,
        semantics="python-3.15:text",
        evidence=(ConstraintEvidence(site.location, site.source_sha256),),
        reviewed=reviewed,
    )
    if second_caller:
        save(engine, "right", "beta")
    assumptions = (
        ("complete_callers",) if reviewed else ("complete_callers", "accept_asserted_constraints")
    )
    summary = engine.constraint_summary("target", 0, assumptions=assumptions)
    assert summary["status"] == "unknown"
    assert summary["predicate"] == {"operation": "true", "terms": ()}
    assert "unsatisfiable_incoming_constraint" in summary["gaps"]


@pytest.mark.parametrize("excluded", ["unaccepted", "stale"])
def test_excluded_false_claim_does_not_add_an_unsatisfiable_gap(
    engine: QueryEngine, excluded: str
) -> None:
    site = engine._store.call_sites("app:left")[0]
    engine.annotate_constraint(
        "left",
        site.id,
        ConstraintBoolean("false"),
        semantics="python-3.15:text",
        evidence=(ConstraintEvidence(site.location, site.source_sha256),),
    )
    if excluded == "stale":
        engine._store._graph.dependencies.append("changed")
    assumptions = (
        ("complete_callers", "accept_asserted_constraints")
        if excluded == "stale"
        else ("complete_callers",)
    )
    result = engine.constraint_summary("target", 0, assumptions=assumptions)
    assert "unsatisfiable_incoming_constraint" not in result["gaps"]
    assert result["status"] == "unknown"


def test_constraint_calls_reuse_one_fingerprint_until_context_changes(engine: QueryEngine) -> None:
    from trailmark.analysis.constraints import graph_fingerprint

    with patch(
        "trailmark.analysis.constraints.graph_fingerprint", wraps=graph_fingerprint
    ) as compute:
        save(engine, "left", "alpha")
        save(engine, "right", "beta")
        assert engine.constraints_of("left")[0]["active"]
        assert (
            engine.constraint_summary("target", 0, assumptions=("complete_callers",))["status"]
            == "conditional"
        )
        assert compute.call_count == 1
        engine._store._graph.dependencies.append("changed")
        assert not engine.constraints_of("left")[0]["active"]
        assert compute.call_count == 2


@pytest.mark.parametrize(
    "writer", ["entrypoint", "binary", "call_site", "merge", "proxy", "rebuild"]
)
def test_public_graph_writes_invalidate_cached_constraint_context(
    engine: QueryEngine, writer: str
) -> None:
    from trailmark.analysis.proxies import ensure_proxy_nodes

    graph = engine._store._graph
    site = engine._store.call_sites("app:left")[0]
    if writer == "proxy":
        nested = CodeUnit(
            "app:left.nested",
            "nested",
            NodeKind.FUNCTION,
            replace(site.location, start_line=3, end_line=3, start_col=0, end_col=8),
        )
        graph.nodes[nested.id] = nested
        graph.edges.append(CodeEdge("app:left", nested.id, EdgeKind.CONTAINS))
        engine._store.rebuild_index()
    save(engine, "left", "alpha")
    fingerprint = engine._store.graph_fingerprint()
    assert engine.constraints_of("left")[0]["active"]
    if writer == "entrypoint":
        engine.add_entrypoint_evidence(
            EntrypointEvidence(
                "app:left",
                "heuristic",
                "review-test",
                site.location,
            )
        )
    elif writer == "binary":
        artifact = BinaryArtifact(
            "image", "image", "a" * 64, "elf", "aarch64", 64, "little", 0, 0, 16
        )
        engine._store.add_binary_graph(CodeGraph(binary_artifacts={artifact.id: artifact}))
    elif writer == "call_site":
        graph.add_call_site(replace(site, location=replace(site.location, start_col=1)))
    elif writer == "merge":
        graph.merge(CodeGraph(dependencies=["new-dependency"]))
    elif writer == "proxy":
        ensure_proxy_nodes(graph)
    else:
        graph.nodes["app:left"] = replace(graph.nodes["app:left"], docstring="changed")
        engine._store.rebuild_index()
    assert engine._store.graph_fingerprint() != fingerprint
    assert not engine.constraints_of("left")[0]["active"]


def test_nested_source_policy_edits_invalidate_the_fingerprint_cache(engine: QueryEngine) -> None:
    graph = engine._store._graph
    graph.source_policy["review"] = {"dependencies": ["original"]}
    save(engine, "left", "alpha")
    assert engine.constraints_of("left")[0]["active"]
    graph.source_policy["review"]["dependencies"].append("changed")
    assert not engine.constraints_of("left")[0]["active"]


def test_annotations_and_subgraphs_preserve_a_cached_constraint_context(
    engine: QueryEngine,
) -> None:
    from trailmark.analysis.constraints import graph_fingerprint

    save(engine, "left", "alpha")
    with patch(
        "trailmark.analysis.constraints.graph_fingerprint", wraps=graph_fingerprint
    ) as compute:
        engine._store.add_annotation(
            "app:left", Annotation(AnnotationKind.ASSUMPTION, "note", "manual")
        )
        engine._store.add_subgraph("reviewed", {"app:left"})
        assert engine.constraints_of("left")[0]["active"]
        assert compute.call_count == 0


@pytest.mark.parametrize("value", [object(), CodeUnit, SourceLocation("app.py", 1, 1)])
def test_fingerprint_rejects_unsupported_source_policy_values(
    engine: QueryEngine, value: object
) -> None:
    engine._store._graph.source_policy["invalid"] = value
    with pytest.raises(TypeError, match="Unsupported constraint context"):
        engine._store.graph_fingerprint()


def test_stored_claim_cannot_use_a_summary_parameter_reference(engine: QueryEngine) -> None:
    site = engine._store.call_sites("app:left")[0]
    with pytest.raises(ValueError, match="observed operand"):
        engine.annotate_constraint(
            "left",
            site.id,
            ConstraintPredicate(ParameterRef(0), "equals", (literal("x"),)),
            semantics="python:text",
            evidence=(ConstraintEvidence(site.location, site.source_sha256),),
        )


def test_receiver_claims_can_be_saved_and_restored(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text('def caller(data): return data.get("key")\n')
    engine = QueryEngine.from_directory(str(tmp_path))
    site = engine._store.call_sites("app:caller")[0]
    predicate = ConstraintPredicate(ArgumentRef(receiver=True), "type", (literal("mapping"),))
    assert engine.annotate_constraint(
        "caller",
        site.id,
        predicate,
        semantics="python:mapping",
        evidence=(ConstraintEvidence(site.location, site.source_sha256),),
        reviewed=True,
    )
    restored = QueryEngine.from_snapshot(engine.to_snapshot(), verify_sources=True)
    claim = restored.constraints_of("caller")[0]
    assert claim["active"]
    assert claim["constraint"]["predicate"]["subject"] == {
        "index": None,
        "receiver": True,
        "slices": (),
    }


def test_method_identity_assumptions_do_not_replace_argument_mapping(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        "class Target:\n    def method(self, value): return value\n"
        "    def caller(self, value): return self.method(value)\n"
    )
    engine = QueryEngine.from_directory(str(tmp_path))
    save(engine, "Target.caller", "alpha")
    site = engine.call_sites("Target.caller")[0]
    result = engine.constraint_summary(
        "Target.method",
        0,
        assumptions=(
            "complete_callers",
            f"identity_parameter_value:{site['id']}:0",
        ),
    )
    assert result["status"] == "unknown"
    assert "parameter_binding_unknown" in result["gaps"]


@pytest.mark.parametrize("single_file", [False, True])
@pytest.mark.parametrize("relative_path", ["nested/app.py", "__init__.py"])
def test_call_site_ids_are_portable_between_identical_checkouts(
    tmp_path: Path, single_file: bool, relative_path: str
) -> None:
    from trailmark.parse import parse_file

    sites = []
    for checkout in ("first", "second"):
        root = tmp_path / checkout
        source = root / relative_path
        source.parent.mkdir(parents=True)
        source.write_text("def caller(x):\n    target(x)\ndef target(y): return y\n")
        graph = parse_file(str(source)) if single_file else parse_directory(str(root))
        engine = QueryEngine.from_graph(graph)
        site = engine.call_sites("caller")[0]
        sites.append(site)
        restored = QueryEngine.from_snapshot(engine.to_snapshot())
        assert restored.call_sites("caller")[0]["id"] == site["id"]
    assert sites[0]["location"]["file_path"] != sites[1]["location"]["file_path"]
    assert sites[0]["id"] == sites[1]["id"]
    expected_path = Path(relative_path).name if single_file else relative_path
    assert sites[0]["source_key"] == ("root", expected_path)


def test_call_site_identity_includes_the_selected_root_name(tmp_path: Path) -> None:
    from trailmark.source_policy import SourcePolicy, SourceRoot

    (tmp_path / "app.py").write_text("def caller(x):\n    target(x)\ndef target(y): return y\n")
    sites = [
        QueryEngine.from_directory(
            str(tmp_path), source_policy=SourcePolicy((SourceRoot(str(tmp_path), name),))
        ).call_sites("caller")[0]
        for name in ("application", "dependency")
    ]
    assert sites[0]["id"] != sites[1]["id"]
    assert sites[0]["source_key"] == ("application", "app.py")
    assert sites[1]["source_key"] == ("dependency", "app.py")
    assert [site["caller_id"] for site in sites] == ["app:caller", "app:caller"]


def test_named_roots_keep_distinct_portable_call_site_identities(tmp_path: Path) -> None:
    from trailmark.source_policy import SourcePolicy, SourceRoot

    identities = []
    for checkout in ("first", "second"):
        roots = []
        for name in ("application", "dependency"):
            root = tmp_path / checkout / name
            (root / "nested").mkdir(parents=True)
            (root / "nested" / "app.py").write_text(
                "def caller(x):\n    target(x)\ndef target(y): return y\n"
            )
            roots.append(SourceRoot(str(root), name))
        engine = QueryEngine.from_directory(str(tmp_path), source_policy=SourcePolicy(tuple(roots)))
        graph = engine._store._graph
        keys = {site.source_key for site in graph.call_sites.values()}
        assert keys == {("application", "nested/app.py"), ("dependency", "nested/app.py")}
        assert {site.caller_id for site in graph.call_sites.values()} == {
            "application::nested.app:caller",
            "dependency::nested.app:caller",
        }
        restored = QueryEngine.from_snapshot(engine.to_snapshot())
        assert set(restored._store._graph.call_sites) == set(graph.call_sites)
        identities.append(set(graph.call_sites))
    assert len(identities[0]) == 2
    assert identities[0] == identities[1]


def test_summary_does_not_claim_to_detect_semantic_contradictions(engine: QueryEngine) -> None:
    graph = engine._store._graph
    graph.edges = [edge for edge in graph.edges if edge.source_id != "app:right"]
    engine._store.rebuild_index()
    site = engine._store.call_sites("app:left")[0]
    for length in (35, 36):
        predicate = ConstraintPredicate(
            ArgumentRef(0), "length", (ConstraintLiteral("integer", length),), "bytes"
        )
        engine.annotate_constraint(
            "left",
            site.id,
            predicate,
            semantics="python:bytes",
            evidence=(ConstraintEvidence(site.location, site.source_sha256),),
            reviewed=True,
        )
    summary = engine.constraint_summary("target", 0, assumptions=("complete_callers",))
    assert summary["status"] == "conditional"
    assert summary["predicate"]["operation"] == "and"
    assert "unsatisfiable_incoming_constraint" not in summary["gaps"]


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


@pytest.mark.parametrize("change", ["modified", "excluded"])
@pytest.mark.parametrize("restamp_context", [False, True])
def test_historical_supporting_evidence_remains_inactive_after_reload(
    tmp_path: Path, change: str, restamp_context: bool
) -> None:
    from trailmark.analysis.constraints import graph_fingerprint

    (tmp_path / "app.py").write_text(
        "def target(value): return value\ndef caller(value): return target(value)\n"
    )
    supporting = tmp_path / "guard.py"
    supporting.write_text("def validator(value): return True\n")
    engine = QueryEngine.from_directory(str(tmp_path))
    graph = engine._store._graph
    site = engine._store.call_sites("app:caller")[0]
    evidence = ConstraintEvidence(
        graph.nodes["guard:validator"].location, graph.source_digests[str(supporting)]
    )
    predicate = ConstraintPredicate(ArgumentRef(0), "equals", (literal("x"),))
    engine.annotate_constraint(
        "caller", site.id, predicate, semantics="python", evidence=(evidence,), reviewed=True
    )
    if change == "modified":
        supporting.write_text("def validator(value): return False\n")
        graph.source_digests[str(supporting)] = hashlib.sha256(supporting.read_bytes()).hexdigest()
    else:
        supporting.unlink()
        graph.source_digests.pop(str(supporting))
        graph.nodes.pop("guard")
        graph.nodes.pop("guard:validator")
        graph.parameter_bindings.pop("guard:validator")
        graph.edges = [
            edge
            for edge in graph.edges
            if edge.source_id in graph.nodes and edge.target_id in graph.nodes
        ]
        graph.source_file_counts["python"] = 1
        engine._store.rebuild_index()
    if restamp_context:
        annotation = graph.annotations["app:caller"][0]
        assert annotation.constraint is not None
        graph.annotations["app:caller"][0] = replace(
            annotation,
            constraint=replace(annotation.constraint, graph_fingerprint=graph_fingerprint(graph)),
        )
    assert engine.constraints_of("caller")[0]["active"] is False
    restored = QueryEngine.from_snapshot(engine.to_snapshot(), verify_sources=True)
    assert restored.constraints_of("caller") == engine.constraints_of("caller")
    result = restored.constraint_summary("target", 0, assumptions=("complete_callers",))
    assert result["status"] == "unknown"
    assert "stale_constraint" in result["gaps"]
    with pytest.raises(ValueError, match="digest"):
        restored.annotate_constraint(
            "caller", site.id, predicate, semantics="python", evidence=(evidence,)
        )


def test_new_external_entry_evidence_invalidates_an_earlier_review(engine: QueryEngine) -> None:
    save(engine, "left", "alpha")
    save(engine, "right", "beta")
    graph = engine._store._graph
    fact = EntrypointEvidence(
        "app:target",
        "reference",
        "possible-registration",
        graph.nodes["app:left"].location,
        source_id="app:left",
        binding="ambiguous",
        unresolved=("runtime-registration",),
    )
    graph.entrypoint_evidence[fact.id] = fact
    assert engine.constraints_of("left")[0]["active"] is False
    restored = QueryEngine.from_snapshot(engine.to_snapshot())
    result = restored.constraint_summary("target", 0, assumptions=("complete_callers",))
    assert result["status"] == "unknown"
    assert "stale_constraint" in result["gaps"]


def test_binary_context_is_preserved_and_invalidates_changed_assumptions(
    engine: QueryEngine,
) -> None:
    graph = engine._store._graph
    graph.binary_artifacts["image"] = BinaryArtifact(
        "image", "missing-image", "a" * 64, "elf", "aarch64", 64, "little", 4096, 0, 1024
    )
    save(engine, "left", "alpha")
    restored = QueryEngine.from_snapshot(engine.to_snapshot())
    assert restored.constraints_of("left")[0]["active"] is True
    graph.binary_artifacts["image"] = replace(graph.binary_artifacts["image"], complete=False)
    assert engine.constraints_of("left")[0]["active"] is False
    restored = QueryEngine.from_snapshot(engine.to_snapshot())
    assert restored.constraints_of("left")[0]["active"] is False


def test_stale_claim_cannot_be_submitted_as_a_new_annotation(engine: QueryEngine) -> None:
    save(engine, "left", "alpha")
    graph = engine._store._graph
    annotation = graph.annotations["app:left"][0]
    graph.source_policy["review_scope"] = "changed"
    with pytest.raises(ValueError, match="context"):
        graph.add_annotation("app:left", annotation)
    assert len(graph.annotations["app:left"]) == 1


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


@pytest.mark.parametrize("corruption", ["empty", "truncated", "renamed", "reordered"])
@pytest.mark.parametrize("parameter", [0, 1])
def test_changed_parameter_profiles_do_not_transfer_facts(
    engine: QueryEngine, corruption: str, parameter: int
) -> None:
    graph = engine._store._graph
    original = graph.parameter_bindings["app:target"]
    profiles = {
        "empty": (),
        "truncated": original[:1],
        "renamed": (replace(original[0], name="another"), original[1]),
        "reordered": tuple(reversed(original)),
    }
    graph.parameter_bindings["app:target"] = profiles[corruption]
    save(engine, "left", "alpha")
    save(engine, "right", "beta")
    for assumptions in (
        ("complete_callers",),
        (
            "complete_callers",
            *(
                f"argument_mapping:{engine.call_sites(caller)[0]['id']}:{parameter}=0"
                for caller in ("left", "right")
            ),
        ),
    ):
        result = engine.constraint_summary("target", parameter, assumptions=assumptions)
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
    assert not engine._store._graph.parameter_bindings["app:target"][0].value_preserving
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


def test_keyword_only_parameter_after_variadic_inputs_keeps_its_binding(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        "def target(*values, named): return named\ndef caller(): return target('a', named='b')\n"
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
    summary = engine.constraint_summary("target", 1, assumptions=("complete_callers",))
    assert summary["status"] == "conditional"
    assert summary["predicate"]["values"][0]["value"] == "b"


def test_callee_without_outgoing_calls_retains_its_cross_file_signature(tmp_path: Path) -> None:
    (tmp_path / "target.py").write_text("def target(value): return value\n")
    (tmp_path / "caller.py").write_text(
        "from target import target\ndef caller(value): return target(value)\n"
    )
    engine = QueryEngine.from_directory(str(tmp_path))
    save(engine, "caller:caller", "a")
    summary = engine.constraint_summary("target:target", 0, assumptions=("complete_callers",))
    assert summary["status"] == "conditional"
    assert summary["predicate"]["values"][0]["value"] == "a"


def test_linked_formal_applications_publish_evidence_for_explicit_transfer(tmp_path: Path) -> None:
    (tmp_path / "lib.fst").write_text("module Lib\nlet target (value:int) : Tot int = value\n")
    (tmp_path / "app.fst").write_text("module App\nopen Lib\nlet caller () : Tot int = target 7\n")
    engine = QueryEngine.from_directory(str(tmp_path), language="fstar")
    (site,) = engine._store.call_sites("app:App.caller")
    assert [argument.expression for argument in site.arguments] == ["7"]
    assert not engine._store._graph._parse_state.call_sites
    engine.annotate_constraint(
        "app:App.caller",
        site.id,
        ConstraintPredicate(ArgumentRef(0), "equals", (ConstraintLiteral("integer", 7),)),
        semantics="fstar:int",
        evidence=(ConstraintEvidence(site.location, site.source_sha256),),
        reviewed=True,
    )
    restored = QueryEngine.from_snapshot(engine.to_snapshot())
    result = restored.constraint_summary(
        "lib:Lib.target",
        0,
        assumptions=("complete_callers", f"argument_mapping:{site.id}:0=0"),
    )
    assert result["status"] == "conditional"
    assert result["predicate"]["values"][0]["value"] == 7


def test_parse_errors_keep_reviewed_claims_out_of_callee_summaries(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        "def target(value, **keywords): return value\ndef caller(): return target(7, label=)\n"
    )
    engine = QueryEngine.from_directory(str(tmp_path))
    site = engine.call_sites("caller")[0]
    assert "source_parse_errors" in site["gaps"]
    save(engine, "caller", "a")
    summary = engine.constraint_summary("target", 0, assumptions=("complete_callers",))
    assert summary["status"] == "unknown"
    assert "incoming_call_evidence_incomplete" in summary["gaps"]


def test_unverified_bindings_require_one_explicit_mapping(tmp_path: Path) -> None:
    (tmp_path / "app.c").write_text(
        "int target(int x) { return x; }\nint caller() { return target(7); }\n"
    )
    engine = QueryEngine.from_directory(str(tmp_path), language="c")
    site = engine._store.call_sites("app\\.c:caller")[0]
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
        claim["evidence"][0]["source_sha256"] = "f" * 63
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
    with pytest.raises(ValueError, match="digest"):
        graph.add_call_site(replace(site, source_sha256="f" * 64))
    restored = GraphStore.from_snapshot(graph.to_snapshot())
    assert restored.call_sites("app:left") == [site]
    assert (
        hashlib.sha256(Path(site.location.file_path).read_bytes()).hexdigest() == site.source_sha256
    )


@pytest.mark.parametrize("conflict", ["site", "profile", "source"])
def test_conflicting_constraint_inputs_do_not_partially_merge(
    engine: QueryEngine, conflict: str
) -> None:
    from trailmark.models import CodeGraph

    graph = engine._store._graph
    before = graph.to_snapshot()
    site = engine._store.call_sites("app:left")[0]
    other = CodeGraph(nodes=dict(graph.nodes), source_digests=dict(graph.source_digests))
    if conflict == "site":
        other.call_sites[site.id] = replace(site, syntax="changed")
    elif conflict == "profile":
        other.parameter_bindings["app:target"] = tuple(
            reversed(graph.parameter_bindings["app:target"])
        )
    else:
        other.source_digests[site.location.file_path] = "f" * 64
    with pytest.raises(ValueError, match="Conflicting"):
        graph.merge(other)
    assert graph.to_snapshot() == before


def test_matching_constraint_inputs_merge_without_losing_their_identity(
    engine: QueryEngine,
) -> None:
    from trailmark.models import CodeGraph

    graph = engine._store._graph
    other = CodeGraph(
        nodes=dict(graph.nodes),
        source_digests=dict(graph.source_digests),
        call_sites=dict(graph.call_sites),
        parameter_bindings=dict(graph.parameter_bindings),
    )
    graph.merge(other)
    assert graph.call_sites == other.call_sites
    assert graph.parameter_bindings == other.parameter_bindings


def test_missing_constraint_writes_and_store_reads_use_empty_results(engine: QueryEngine) -> None:
    site = engine._store.call_sites("app:left")[0]
    assert (
        engine.annotate_constraint(
            "missing",
            site.id,
            ConstraintPredicate(ArgumentRef(0), "equals", (literal("x"),)),
            semantics="python",
            evidence=(ConstraintEvidence(site.location, site.source_sha256),),
        )
        is False
    )
    assert engine._store.call_sites("missing") == []


@pytest.mark.parametrize("counts", [{}, {"custom-imported": 1}])
def test_imported_frontend_metadata_exposes_unverified_capabilities(counts: dict[str, int]) -> None:
    engine = QueryEngine.from_graph(
        CodeGraph(language="custom-imported", source_file_counts=counts)
    )
    profiles = {profile["language"]: profile for profile in engine.constraint_support()}
    assert profiles["custom-imported"]["call_sites"] == "unverified"
    assert profiles["custom-imported"]["automatic_inference"] == "unverified"


def test_call_occurrences_sort_by_columns_before_their_opaque_ids() -> None:
    location = SourceLocation("app.py", 1, 1)
    caller = CodeUnit("app:caller", "caller", NodeKind.FUNCTION, location)
    graph = CodeGraph(nodes={caller.id: caller}, source_digests={"app.py": "0" * 64})
    sites = [
        CallSite(
            caller.id,
            "python",
            replace(location, start_col=col, end_col=col + 8),
            "0" * 64,
            "target()",
            source_key=("root", "app.py"),
        )
        for col in range(0, 80, 10)
    ]
    assert sorted(sites, key=lambda site: site.id) != sites
    for site in reversed(sites):
        graph.add_call_site(site)
    assert GraphStore(graph).call_sites(caller.id) == sites


@pytest.mark.parametrize("corruption", ["key", "caller", "source"])
def test_graph_snapshot_validates_call_identities_before_storage(
    engine: QueryEngine, corruption: str
) -> None:
    site = engine._store.call_sites("app:left")[0]
    snapshot = engine._store._graph.to_snapshot()
    assert isinstance(snapshot["graph"], dict)
    rows = snapshot["graph"]["call_sites"]
    assert isinstance(rows, dict)
    row = rows.pop(site.id)
    if corruption == "key":
        rows["wrong"] = row
    else:
        assert isinstance(row, dict)
        field, value = (
            ("caller_id", "missing") if corruption == "caller" else ("source_sha256", "f" * 64)
        )
        row[field] = value
        rows[replace(site, **{field: value}).id] = row
    with pytest.raises(ValueError, match="call-site"):
        CodeGraph.from_snapshot(snapshot)


def test_ephemeral_parse_caches_do_not_change_graph_value_equality() -> None:
    left, right = CodeGraph(), CodeGraph()
    right._parse_state.resolved_type_files.add("precomputed.py")
    assert left == right
