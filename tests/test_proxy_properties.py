"""Normalization preserves every call site and every concrete declaration."""

from __future__ import annotations

import subprocess
import sys
from copy import deepcopy

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from trailmark.analysis.proxies import ensure_proxy_nodes
from trailmark.models import (
    CodeEdge,
    CodeGraph,
    CodeUnit,
    EdgeConfidence,
    EdgeKind,
    NodeKind,
    NodeOrigin,
    Parameter,
    SourceLocation,
    TypeRef,
)


@given(
    sites=st.lists(
        st.tuples(st.text(max_size=30), st.sampled_from(list(EdgeConfidence))), max_size=20
    ),
)
@example(
    sites=[
        ("operator<", EdgeConfidence.CERTAIN),
        ("operator>", EdgeConfidence.INFERRED),
        ("operator<", EdgeConfidence.UNCERTAIN),
        (" ", EdgeConfidence.CERTAIN),
        ("", EdgeConfidence.INFERRED),
        ("unknown", EdgeConfidence.UNCERTAIN),
    ],
)
@example(
    sites=[
        ("known", EdgeConfidence.CERTAIN),
        ("proxy.unresolved:_", EdgeConfidence.UNCERTAIN),
        ("é", EdgeConfidence.INFERRED),
        ("ø", EdgeConfidence.UNCERTAIN),
        ("é", EdgeConfidence.CERTAIN),
    ],
)
@settings(max_examples=200, deadline=None)
def test_normalization_is_idempotent_and_preserves_call_sites(
    sites: list[tuple[str, EdgeConfidence]],
) -> None:
    symbols = [symbol for symbol, _ in sites]
    location = SourceLocation("calls.py", 1, 20)
    nodes = {
        node: CodeUnit(node, node, NodeKind.FUNCTION, location)
        for node in ("entry", "known", "proxy.unresolved:_")
    }
    calls = [
        CodeEdge(
            "entry",
            symbol,
            EdgeKind.CALLS,
            confidence=confidence,
            location=SourceLocation("calls.py", index + 1, index + 1),
            attributes=(("site", index),),
        )
        for index, (symbol, confidence) in enumerate(sites)
    ]
    other = CodeEdge("entry", "absent_type", EdgeKind.TYPE_USES)
    graph = CodeGraph(nodes=nodes.copy(), edges=[*calls, other])
    ensure_proxy_nodes(graph)
    assert graph.edges[-1] == other
    assert len(graph.edges) == len(calls) + 1
    for node_id, unit in nodes.items():
        assert graph.nodes[node_id] == unit
    for original, rewritten in zip(calls, graph.edges[:-1], strict=True):
        assert rewritten.source_id == original.source_id
        assert rewritten.kind == original.kind
        assert rewritten.confidence == original.confidence
        assert rewritten.location == original.location
        if original.target_id in nodes:
            assert rewritten == original
        else:
            target = graph.nodes[rewritten.target_id]
            assert target.kind == NodeKind.PROXY
            assert target.origin == NodeOrigin.PROXY
            assert target.name == original.target_id
            assert dict(target.attributes) == {
                "raw_symbol": original.target_id,
                "proxy_kind": "unresolved",
            }
            assert rewritten.attributes == original.attributes + (
                ("raw_target", original.target_id),
            )
    proxies = [node for node in graph.nodes.values() if node.kind == NodeKind.PROXY]
    assert {node.name for node in proxies} == set(symbols) - set(nodes)
    assert len(proxies) == len(set(symbols) - set(nodes))
    before = deepcopy(graph)
    ensure_proxy_nodes(graph)
    assert graph == before


@pytest.mark.parametrize("root", ["", "/project"])
@pytest.mark.parametrize("site", [None, SourceLocation("calls.c", 7, 7, 4, 12)])
def test_unlocated_proxy_uses_graph_root_and_located_proxy_keeps_its_site(
    root: str, site: SourceLocation | None
) -> None:
    unit = CodeUnit("entry", "entry", NodeKind.FUNCTION, SourceLocation("calls.c", 1, 10))
    graph = CodeGraph(
        nodes={unit.id: unit},
        root_path=root,
        edges=[CodeEdge(unit.id, "missing", EdgeKind.CALLS, location=site)],
    )
    ensure_proxy_nodes(graph)
    target = graph.nodes[graph.edges[0].target_id]
    assert target.location == (site or SourceLocation(root or "<proxy>", 0, 0))
    assert graph.edges[0].location == site


@pytest.mark.parametrize(
    "module,target",
    [(None, "local:Thing"), ("local", "local:Thing"), ("remote", "remote:Thing"), ("absent", None)],
)
def test_explicit_type_module_does_not_fall_back_to_an_ambiguous_local_type(
    module: str | None, target: str | None
) -> None:
    location = SourceLocation("types.py", 1, 1)
    units = [
        CodeUnit(
            "local:owner",
            "owner",
            NodeKind.FUNCTION,
            location,
            parameters=(Parameter("item", TypeRef("Thing", module=module)),),
        ),
        CodeUnit("local:Thing", "Thing", NodeKind.CLASS, location),
        CodeUnit("remote:Thing", "Thing", NodeKind.CLASS, location),
    ]
    graph = CodeGraph(nodes={unit.id: unit for unit in units})
    ensure_proxy_nodes(graph)
    assert [(edge.source_id, edge.target_id) for edge in graph.edges] == (
        [("local:owner", target)] if target is not None else []
    )


def test_containment_cycles_without_a_callable_owner_terminate() -> None:
    script = """
from trailmark.analysis.proxies import ensure_proxy_nodes
from trailmark.models import CodeGraph, CodeUnit, CodeEdge, EdgeKind, NodeKind, SourceLocation
location = SourceLocation('cycle.py', 1, 1)
units = [CodeUnit(name, name, kind, location) for name, kind in [
    ('A', NodeKind.CLASS), ('B', NodeKind.CLASS), ('work', NodeKind.FUNCTION),
]]
edges = [CodeEdge(start, end, EdgeKind.CONTAINS) for start, end in [
    ('A', 'B'), ('B', 'A'), ('A', 'work'),
]]
graph = CodeGraph(nodes={unit.id: unit for unit in units}, edges=edges.copy())
ensure_proxy_nodes(graph)
assert graph.edges == edges
print('done')
"""
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert result.stdout == "done\n"
