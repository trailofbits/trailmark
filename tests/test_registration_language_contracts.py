"""Every supported frontend participates in source, registration and execution contracts."""

import json
from pathlib import Path

import pytest

from tests.kat_runner import fixture_pair
from tests.test_kat_parsers import PARSER_FIXTURES
from trailmark.analysis.entrypoints import detect_entrypoints
from trailmark.analysis.registrations import discover_registrations
from trailmark.models.endpoints import EndpointRegistration, HandlerBinding, RegistrationEvidence
from trailmark.models.execution import ExecutionFlow, ExecutionPoint
from trailmark.models.nodes import NodeKind
from trailmark.parse import parse_roots, supported_languages
from trailmark.query.api import QueryEngine
from trailmark.source_policy import SourcePolicy, SourceRoot

_FIXTURES = dict(reversed(PARSER_FIXTURES))


def test_contract_matrix_covers_every_registered_frontend() -> None:
    assert set(_FIXTURES) == set(supported_languages())


@pytest.mark.parametrize("language", supported_languages())
def test_source_registration_and_execution_contracts_for_every_language(
    tmp_path: Path,
    language: str,
) -> None:
    fixture, _ = fixture_pair(language, _FIXTURES[language])
    dependency = tmp_path / "vendor" / "selected"
    dependency.mkdir(parents=True)
    selected = dependency / fixture.name
    selected.write_bytes(fixture.read_bytes())
    policy = SourcePolicy(
        (
            SourceRoot(str(tmp_path), "app"),
            SourceRoot(str(dependency), "selected", "dependency"),
            SourceRoot(str(dependency), "overlap", "dependency"),
        )
    )
    graph = parse_roots(policy, language)
    assert sum(graph.source_file_counts.values()) == 1
    assert all(n.id.startswith("selected::") for n in graph.nodes.values())
    graph.entrypoints.update(detect_entrypoints(graph, str(dependency)))
    existing = set(graph.entrypoints)
    discover_registrations(graph)
    represented = {b.node_id for r in graph.registrations.values() for b in r.bindings}
    represented.update(n for r in graph.registrations.values() for n in r.declaration_nodes)
    assert existing <= represented
    assert all(r.owning_root == str(dependency) for r in graph.registrations.values())
    for registration in graph.registrations.values():
        assert (
            EndpointRegistration.from_dict(json.loads(json.dumps(registration.to_dict())))
            == registration
        )
    engine = QueryEngine.from_graph(graph)
    unit = next(n for n in graph.nodes.values() if n.kind == NodeKind.MODULE)
    # Explicit test inputs validate the common API; they are never detector output.
    registration = EndpointRegistration(
        "manual-contract",
        "entrypoint",
        str(dependency),
        (RegistrationEvidence("manual", unit.location, "Language-neutral contract fixture"),),
        (HandlerBinding(node_id=unit.id, location=unit.location, status="resolved"),),
    )
    engine.add_registration(registration)
    root = ExecutionPoint(unit.id, unit.location, "dispatch")
    target = ExecutionPoint(unit.id, unit.location, "continuation")
    engine.add_execution_flow(
        ExecutionFlow(
            "unverified-dispatch",
            root,
            target,
            "dispatch",
            registration.evidence,
            registration_id=registration.id,
        )
    )
    assert engine.authentication_paths((root,), target)["classification"] == "unknown"
    assert engine.endpoint_inventory("missing")["registrations"] == []
    assert engine.execution_evidence("missing") == []
    assert (
        json.loads(engine.to_json())["execution_evidence"][0]["registration_id"] == registration.id
    )
