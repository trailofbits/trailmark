"""Tests for explicit cross-language and external graph links."""

from __future__ import annotations

from pathlib import Path

import pytest

from trailmark.analysis.links import apply_repository_links
from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit, NodeKind, NodeOrigin, SourceLocation
from trailmark.parse import parse_directory
from trailmark.query.api import QueryEngine


def _write_sources(tmp_path: Path) -> None:
    (tmp_path / "caller.py").write_text("def invoke():\n    return 1\n")
    (tmp_path / "callee.rs").write_text("fn execute() {}\n")
    (tmp_path / ".trailmark").mkdir()


def test_links_cross_language_nodes(tmp_path: Path) -> None:
    _write_sources(tmp_path)
    (tmp_path / ".trailmark" / "links.toml").write_text(
        '[[link]]\nsource = "caller:invoke"\ntarget = "callee:execute"\n'
        'confidence = "certain"\ndescription = "host invokes contract"\n',
    )
    graph = parse_directory(str(tmp_path), "python,rust")
    edge = next(
        edge
        for edge in graph.edges
        if edge.source_id == "caller:invoke" and edge.target_id == "callee:execute"
    )
    assert edge.kind == EdgeKind.CALLS
    assert edge.confidence == EdgeConfidence.CERTAIN
    assert ("description", "host invokes contract") in edge.attributes
    engine = QueryEngine.from_directory(str(tmp_path), "python,rust")
    assert [unit["id"] for unit in engine.callees_of("caller:invoke")] == ["callee:execute"]


def test_external_link_requires_opt_in_and_creates_proxy(tmp_path: Path) -> None:
    _write_sources(tmp_path)
    config = tmp_path / ".trailmark" / "links.toml"
    config.write_text('[[link]]\nsource = "caller:invoke"\ntarget = "rpc:transfer"\n')
    with pytest.raises(ValueError, match="target_external = true"):
        parse_directory(str(tmp_path), "python")

    config.write_text(
        '[[link]]\nsource = "caller:invoke"\ntarget = "rpc:transfer"\ntarget_external = true\n',
    )
    graph = parse_directory(str(tmp_path), "python")
    proxy = graph.nodes["proxy.external:rpc:transfer"]
    assert proxy.origin == NodeOrigin.PROXY
    assert "caller:invoke" in graph.nodes


def test_per_endpoint_external_keeps_unflagged_endpoint_strict(tmp_path: Path) -> None:
    _write_sources(tmp_path)
    (tmp_path / ".trailmark" / "links.toml").write_text(
        '[[link]]\nsource = "caller:typo"\ntarget = "rpc:transfer"\ntarget_external = true\n',
    )

    with pytest.raises(ValueError, match="source_external = true"):
        parse_directory(str(tmp_path), "python")


def test_source_external_creates_proxy_for_source_only(tmp_path: Path) -> None:
    _write_sources(tmp_path)
    (tmp_path / ".trailmark" / "links.toml").write_text(
        '[[link]]\nsource = "queue:job"\ntarget = "caller:invoke"\nsource_external = true\n',
    )

    graph = parse_directory(str(tmp_path), "python")
    assert graph.nodes["proxy.external:queue:job"].origin == NodeOrigin.PROXY
    assert "proxy.external:caller:invoke" not in graph.nodes


@pytest.mark.parametrize(
    "body,match",
    [
        ("link = {}\n", "'link' must be an array"),
        ("metadata = {}\n", "unknown top-level key"),
        ('[[link]]\nsource = "invoke"\n', "'target' must be"),
        ('[[link]]\nsource = "invoke"\ntarget = "execute"\ndescripton = "typo"\n', "unknown key"),
        ('[[link]]\nsource = "invoke"\ntarget = "execute"\nkind = "invalid"\n', "invalid kind"),
    ],
)
def test_invalid_links_are_rejected(tmp_path: Path, body: str, match: str) -> None:
    _write_sources(tmp_path)
    (tmp_path / ".trailmark" / "links.toml").write_text(body)
    with pytest.raises(ValueError, match=match):
        parse_directory(str(tmp_path), "python,rust")


@pytest.mark.parametrize("collision", ["source", "raw_symbol"])
def test_external_link_proxy_collisions_preserve_graph(tmp_path: Path, collision: str) -> None:
    _write_sources(tmp_path)
    graph = CodeGraph(
        nodes={
            "caller:invoke": CodeUnit(
                "caller:invoke", "invoke", NodeKind.FUNCTION, SourceLocation("caller.py", 1, 2)
            )
        }
    )
    if collision == "source":
        graph.nodes["proxy.external:worker_"] = CodeUnit(
            "proxy.external:worker_",
            "worker_",
            NodeKind.METHOD,
            SourceLocation("proxy/external.py", 1, 2),
        )
    first = "worker?" if collision == "raw_symbol" else "first"
    (tmp_path / ".trailmark" / "links.toml").write_text(
        f'[[link]]\nsource = "caller:invoke"\ntarget = "{first}"\ntarget_external = true\n'
        '[[link]]\nsource = "caller:invoke"\ntarget = "worker!"\ntarget_external = true\n'
    )
    before_nodes = graph.nodes.copy()
    before_edges = graph.edges.copy()

    with pytest.raises(ValueError, match="collide at node ID 'proxy.external:worker_'"):
        apply_repository_links(graph, str(tmp_path))

    assert graph.nodes == before_nodes
    assert graph.edges == before_edges
