"""Aiken parser behavior through Trailmark's public factories."""

from pathlib import Path

import pytest
from tree_sitter import Parser
from tree_sitter_language_pack import get_language

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.nodes import NodeKind
from trailmark.parse import detect_languages, parse_directory, parse_file
from trailmark.query.api import QueryEngine


def _source(tmp_path: Path, text: str) -> Path:
    assert not Parser(get_language("aiken")).parse(text.encode()).root_node.has_error
    path = tmp_path / "sample.ak"
    path.write_text(text)
    return path


def test_types_signatures_imports_and_branches(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        """use aiken/collection/list
pub type State<a> { value: a }
type Action { Add(Int) Stop }
type Count = Int
fn positive(n: Int) -> Bool { if n > 0 { True } else { False } }
validator payments(owner: ByteArray) {
  spend(datum: Option<State<Int>>, redeemer: Int, _ref: Data, _tx: Data) { positive(redeemer) }
  else(_) { fail }
}
""",
    )
    graph = parse_file(str(path))
    assert graph.nodes["sample:payments"].kind == NodeKind.CONTRACT
    assert graph.nodes["sample:payments"].parameters[0].name == "owner"
    unit = graph.nodes["sample:payments.spend"]
    assert unit.kind == NodeKind.METHOD
    assert unit.parameters[0].type_ref and unit.parameters[0].type_ref.name == "Option<State<Int>>"
    assert unit.return_type and unit.return_type.name == "Bool"
    assert dict(unit.attributes)["aiken_handler"] == "spend"
    assert graph.nodes["sample:State"].type_parameters[0].name == "a"
    assert graph.nodes["sample:Action"].kind == NodeKind.ENUM
    assert dict(graph.nodes["sample:Count"].attributes)["aiken_type_kind"] == "alias"
    assert graph.nodes["sample:positive"].cyclomatic_complexity == 2
    assert graph.dependencies == ["aiken/collection/list"]
    assert any(
        e.source_id == unit.id and e.target_id == "sample:positive" and e.kind == EdgeKind.CALLS
        for e in graph.edges
    )


@pytest.mark.parametrize(
    "purpose", ["mint", "spend", "withdraw", "publish", "vote", "propose", "else"]
)
def test_handlers_are_reachable_entrypoints(tmp_path: Path, purpose: str) -> None:
    args = "_" if purpose == "else" else "d, r, ref, tx" if purpose == "spend" else "r, target, tx"
    _source(
        tmp_path,
        f"fn helper() {{ True }} validator payments {{ {purpose}({args}) {{ helper() }} }}",
    )
    assert detect_languages(str(tmp_path)) == ["aiken"]
    graph = parse_directory(str(tmp_path), language="auto")
    assert graph.source_file_counts == {"aiken": 1}
    engine = QueryEngine.from_directory(str(tmp_path), language="auto")
    surface = engine.attack_surface()
    assert len(surface) == 1
    assert surface[0]["node_id"] == f"sample:payments.{purpose}"
    assert surface[0]["trust_level"] == "untrusted_external"
    assert surface[0]["asset_value"] == "high"
    assert engine.entrypoint_paths_to("helper")


def test_pipeline_calls_once_per_stage(tmp_path: Path) -> None:
    path = _source(
        tmp_path, "fn first(x) { x } fn second(x, y) { x } fn f(x) { x |> first |> second(1) }"
    )
    graph = parse_file(str(path))
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert [(e.source_id, e.target_id, e.confidence) for e in calls] == [
        ("sample:f", "sample:first", EdgeConfidence.CERTAIN),
        ("sample:f", "sample:second", EdgeConfidence.CERTAIN),
    ]


def test_callback_and_import_do_not_bind_to_namesakes(tmp_path: Path) -> None:
    _source(
        tmp_path,
        """use foreign/checks.{check as verify}
fn check(x) { True }
fn parameter(check: fn(Int) -> Bool) { check(1) }
fn patterns(x) { expect Some(check) = x check(1) }
fn imported() { checks.check(1) }
fn aliases() { verify(1) }
fn locals() { let check = unknown check(1) }
""",
    )
    (tmp_path / "external.ak").write_text("fn verify(x) { True }")
    graph = parse_directory(str(tmp_path), language="aiken")
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 5
    assert all(e.confidence == EdgeConfidence.UNCERTAIN for e in calls)
    assert all(graph.nodes[e.target_id].kind == NodeKind.PROXY for e in calls)


def test_anonymous_calls_have_their_own_owner(tmp_path: Path) -> None:
    path = _source(tmp_path, "fn helper(x) { x } fn outer() { let f = fn(x) { helper(x) } True }")
    graph = parse_file(str(path))
    lambdas = [u for u in graph.nodes.values() if u.name.startswith("<lambda>")]
    assert len(lambdas) == 1
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    deferred = [e for e in calls if dict(e.attributes).get("deferred")]
    assert [(e.source_id, e.target_id, e.confidence) for e in deferred] == [
        ("sample:outer", lambdas[0].id, EdgeConfidence.UNCERTAIN)
    ]
    assert [(e.source_id, e.target_id) for e in calls if e not in deferred] == [
        (lambdas[0].id, "sample:helper")
    ]


def test_sibling_validator_handlers_stay_distinct(tmp_path: Path) -> None:
    path = _source(
        tmp_path, "validator a { mint(r,p,t) { True } } validator b { mint(r,p,t) { False } }"
    )
    graph = parse_file(str(path))
    assert {u.id for u in graph.nodes.values() if u.kind == NodeKind.METHOD} == {
        "sample:a.mint",
        "sample:b.mint",
    }


def test_parse_errors_warn(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "broken.ak"
    path.write_text("validator broken { spend(")
    parse_file(str(path))
    assert "Aiken syntax errors" in caplog.text
