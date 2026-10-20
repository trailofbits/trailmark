"""Identity and type resolution regressions for graph normalization."""

from __future__ import annotations

from pathlib import Path

import pytest

from trailmark.analysis.proxies import ensure_proxy_nodes
from trailmark.models.edges import CodeEdge, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit, NodeKind, NodeOrigin, SourceLocation, TypeRef
from trailmark.parse import parse_directory

_LOCATION = SourceLocation("app.py", 1, 3)


def _unit(node_id: str, name: str, kind: NodeKind = NodeKind.FUNCTION) -> CodeUnit:
    return CodeUnit(node_id, name, kind, _LOCATION)


def test_unresolved_proxy_cannot_alias_concrete_source_namespace(tmp_path: Path) -> None:
    (tmp_path / "proxy").mkdir()
    (tmp_path / "proxy" / "unresolved.py").write_text(
        "class obj:\n    def foo(self):\n        pass\n"
    )
    (tmp_path / "caller.py").write_text("def invoke():\n    obj.foo()\n")

    graph = parse_directory(str(tmp_path))

    concrete = graph.nodes["proxy.unresolved:obj.foo"]
    assert concrete.origin == NodeOrigin.SOURCE
    call = next(edge for edge in graph.edges if edge.source_id == "caller:invoke")
    assert call.target_id != concrete.id
    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY
    assert dict(graph.nodes[call.target_id].attributes)["raw_symbol"] == "obj.foo"


def test_distinct_raw_symbols_do_not_collapse_after_escaping() -> None:
    graph = CodeGraph(
        nodes={"app:f": _unit("app:f", "f")},
        edges=[
            CodeEdge("app:f", "operator<", EdgeKind.CALLS),
            CodeEdge("app:f", "operator>", EdgeKind.CALLS),
        ],
    )
    ensure_proxy_nodes(graph)
    before_nodes = graph.nodes.copy()
    before_edges = graph.edges.copy()
    ensure_proxy_nodes(graph)

    assert len(graph.nodes) == 3
    assert len({edge.target_id for edge in graph.edges}) == 2
    assert {dict(graph.nodes[edge.target_id].attributes)["raw_symbol"] for edge in graph.edges} == {
        "operator<",
        "operator>",
    }
    assert graph.nodes == before_nodes
    assert graph.edges == before_edges


def test_same_proxy_identity_reuses_node_and_retains_call_sites() -> None:
    graph = CodeGraph(
        nodes={"app:f": _unit("app:f", "f")},
        edges=[
            CodeEdge("app:f", "obj.foo", EdgeKind.CALLS, location=SourceLocation("app.py", 2, 2)),
            CodeEdge("app:f", "obj.foo", EdgeKind.CALLS, location=SourceLocation("app.py", 3, 3)),
        ],
    )

    ensure_proxy_nodes(graph)
    ensure_proxy_nodes(graph)

    assert len(graph.nodes) == 2
    assert graph.nodes["proxy.unresolved:obj.foo"].origin == NodeOrigin.PROXY
    assert len(graph.edges) == 2
    assert {edge.location.start_line for edge in graph.edges if edge.location} == {2, 3}


@pytest.mark.parametrize("non_type_kind", [NodeKind.MODULE, NodeKind.FUNCTION, NodeKind.PROXY])
def test_non_types_do_not_make_a_real_type_ambiguous(non_type_kind: NodeKind) -> None:
    graph = CodeGraph(
        nodes={
            "Token": _unit("Token", "Token", non_type_kind),
            "Token:Token": _unit("Token:Token", "Token", NodeKind.CONTRACT),
            "Vault:balance": CodeUnit(
                "Vault:balance", "balance", NodeKind.METHOD, _LOCATION, return_type=TypeRef("Token")
            ),
        }
    )

    ensure_proxy_nodes(graph)

    assert [(edge.source_id, edge.target_id) for edge in graph.edges] == [
        ("Vault:balance", "Token:Token")
    ]


@pytest.mark.parametrize(
    "kind", [NodeKind.FUNCTION, NodeKind.MODULE, NodeKind.NAMESPACE, NodeKind.STATE_VARIABLE]
)
def test_non_type_targets_do_not_get_type_use_edges(kind: NodeKind) -> None:
    graph = CodeGraph(
        nodes={
            "m:Token": _unit("m:Token", "Token", kind),
            "m:f": CodeUnit("m:f", "f", NodeKind.FUNCTION, _LOCATION, return_type=TypeRef("Token")),
        }
    )

    ensure_proxy_nodes(graph)

    assert graph.edges == []


def test_two_real_types_with_same_name_remain_ambiguous() -> None:
    graph = CodeGraph(
        nodes={
            "a:Token": _unit("a:Token", "Token", NodeKind.CLASS),
            "b:Token": _unit("b:Token", "Token", NodeKind.STRUCT),
            "m:f": CodeUnit("m:f", "f", NodeKind.FUNCTION, _LOCATION, return_type=TypeRef("Token")),
        }
    )

    ensure_proxy_nodes(graph)

    assert graph.edges == []
