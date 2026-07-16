"""Tests for explicit cross-language and external graph links."""

from __future__ import annotations

from pathlib import Path

import pytest

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.nodes import NodeOrigin
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
    with pytest.raises(ValueError, match="external = true"):
        parse_directory(str(tmp_path), "python")

    config.write_text(
        '[[link]]\nsource = "caller:invoke"\ntarget = "rpc:transfer"\nexternal = true\n',
    )
    graph = parse_directory(str(tmp_path), "python")
    proxy = graph.nodes["proxy.external:rpc:transfer"]
    assert proxy.origin == NodeOrigin.PROXY


@pytest.mark.parametrize(
    "body,match",
    [
        ("link = {}\n", "'link' must be an array"),
        ('[[link]]\nsource = "invoke"\n', "'target' must be"),
        ('[[link]]\nsource = "invoke"\ntarget = "execute"\nkind = "invalid"\n', "invalid kind"),
    ],
)
def test_invalid_links_are_rejected(tmp_path: Path, body: str, match: str) -> None:
    _write_sources(tmp_path)
    (tmp_path / ".trailmark" / "links.toml").write_text(body)
    with pytest.raises(ValueError, match=match):
        parse_directory(str(tmp_path), "python,rust")
