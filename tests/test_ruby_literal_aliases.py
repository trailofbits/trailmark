"""Literal aliases name a prior inspected body without guessing dispatch."""

from pathlib import Path

import pytest

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeOrigin
from trailmark.parse import parse_directory, parse_file
from trailmark.query.api import QueryEngine


def _graph(tmp_path: Path, source: str) -> CodeGraph:
    path = tmp_path / "aliases.rb"
    path.write_text(source)
    return parse_file(str(path), "ruby")


@pytest.mark.parametrize(
    "declaration",
    ["alias_method :copy, :target", 'alias_method "copy", "target"', "alias copy target"],
)
def test_literal_alias_reaches_body_and_records_its_source(
    tmp_path: Path, declaration: str
) -> None:
    graph = _graph(
        tmp_path, f"class Box\n def target; sink; end\n {declaration}\n def entry; copy; end\nend\n"
    )
    edge = next(
        e for e in graph.edges if e.source_id == "aliases:Box.entry" and e.kind == EdgeKind.CALLS
    )
    assert edge.target_id == "aliases:Box.target"
    assert edge.confidence == EdgeConfidence.INFERRED
    assert edge.location is not None and edge.location.start_line == 4
    declaration_edge = next(
        e for e in graph.edges if dict(e.attributes).get("ruby_alias_name") == "copy"
    )
    assert declaration_edge.kind == EdgeKind.REFERENCES
    assert declaration_edge.target_id == edge.target_id
    assert declaration_edge.location is not None and declaration_edge.location.start_line == 3
    assert QueryEngine.from_graph(graph).paths_between("aliases:Box.entry", "aliases:Box.target")


def test_alias_chain_captures_prior_body(tmp_path: Path) -> None:
    graph = _graph(
        tmp_path,
        "class Box\n def target; end\n alias_method :first, :target\n"
        " alias_method :second, :first\n def entry; second; end\nend\n",
    )
    assert any(
        e.source_id == "aliases:Box.entry"
        and e.target_id == "aliases:Box.target"
        and e.kind == EdgeKind.CALLS
        for e in graph.edges
    )


@pytest.mark.parametrize(
    "body",
    [
        "alias_method :copy, :target\n def target; end",
        "def target; end\n alias_method :copy, :target\n def target; changed; end",
        "def target; end\n alias_method :copy, :target\n def copy; changed; end",
        "def target; end\n if condition\n alias_method :copy, :target\n end",
        "def target; end\n alias_method :copy, dynamic_name",
        "def target; end\n alias_method :copy, :target\n alias_method dynamic_name, :target",
        "def target; end\n alias_method :copy, :target\n remove_method :copy",
        "def target; end\n alias_method :copy, :target\n define_method(:copy) { changed }",
        "def target; end\n def self.alias_method(*args); end\n alias_method :copy, :target",
        "def target; end\n alias_method :copy, :target\n alias_method :copy, :target",
        "def target; end\n self.alias_method :copy, :target",
        "def target; end\n alias_method :copy, :target\n undef copy",
        "def target; end\n alias_method :copy, :target\n send(:alias_method, :copy, :other)",
        "def target; end\n alias_method :copy, :target\n extend DynamicMethods",
        "def target; end\n class << self\n def alias_method(*args); end\n"
        " end\n alias_method :copy, :target",
    ],
)
def test_unproven_alias_names_remain_proxy(tmp_path: Path, body: str) -> None:
    graph = _graph(tmp_path, f"class Box\n {body}\n def entry; copy; end\nend\n")
    edge = next(
        e for e in graph.edges if e.source_id == "aliases:Box.entry" and e.kind == EdgeKind.CALLS
    )
    assert graph.nodes[edge.target_id].origin == NodeOrigin.PROXY
    assert edge.confidence == EdgeConfidence.UNCERTAIN


def test_alias_is_not_rebound_to_a_later_reopened_body(tmp_path: Path) -> None:
    path = tmp_path / "aliases.rb"
    path.write_text(
        "class Box\n def target; old_body; end\n alias_method :copy, :target\n"
        " def entry; copy; end\nend\nclass Box\n def target; new_body; end\nend\n"
    )
    graph = parse_directory(str(tmp_path), "ruby")
    edge = next(
        e for e in graph.edges if e.source_id == "aliases:Box.entry" and e.kind == EdgeKind.CALLS
    )
    assert graph.nodes[edge.target_id].origin == NodeOrigin.PROXY


@pytest.mark.parametrize(
    "container,owner", [("module Helpers", "Helpers"), ("class Box\n class << self", "Box.self")]
)
def test_aliases_keep_module_and_singleton_receiver(
    tmp_path: Path, container: str, owner: str
) -> None:
    graph = _graph(
        tmp_path,
        f"{container}\n def target; end\n alias_method :copy, :target\n def entry; copy; end\nend\n"
        + ("end\n" if ".self" in owner else ""),
    )
    assert any(
        e.source_id == f"aliases:{owner}.entry"
        and e.target_id == f"aliases:{owner}.target"
        and e.kind == EdgeKind.CALLS
        for e in graph.edges
    )


def test_instance_alias_does_not_resolve_a_singleton_method(tmp_path: Path) -> None:
    graph = _graph(
        tmp_path,
        "class Box\n def self.target; end\n alias_method :copy, :target\n"
        " def entry; copy; end\nend\n",
    )
    edge = next(
        e for e in graph.edges if e.source_id == "aliases:Box.entry" and e.kind == EdgeKind.CALLS
    )
    assert graph.nodes[edge.target_id].origin == NodeOrigin.PROXY


def test_unknown_inherited_alias_method_override_blocks_alias_inference(tmp_path: Path) -> None:
    graph = _graph(
        tmp_path,
        "class Box < UnknownBase\n def target; end\n"
        " alias_method :copy, :target\n def entry; copy; end\nend\n",
    )
    edge = next(
        e for e in graph.edges if e.source_id == "aliases:Box.entry" and e.kind == EdgeKind.CALLS
    )
    assert graph.nodes[edge.target_id].origin == NodeOrigin.PROXY


def test_singleton_alias_does_not_capture_an_unrelated_explicit_receiver(tmp_path: Path) -> None:
    graph = _graph(
        tmp_path,
        "class Box\n class << self\n def obj.target; end\n"
        " alias_method :copy, :target\n def entry; copy; end\n end\nend\n",
    )
    edge = next(
        e
        for e in graph.edges
        if e.source_id == "aliases:Box.self.entry" and e.kind == EdgeKind.CALLS
    )
    assert graph.nodes[edge.target_id].origin == NodeOrigin.PROXY


@pytest.mark.parametrize(
    "effect",
    [
        "prepend Module.new { def copy; changed; end }",
        "attr_reader :copy",
        "unknown_effect",
        "def self.method_added(name); alias_method(:copy, :other); end",
        "def self.singleton_method_added(name); unknown_effect; end",
        "def self.private(*names); unknown_effect; end\n private :copy",
    ],
)
def test_class_body_effects_and_hooks_block_alias_inference(tmp_path: Path, effect: str) -> None:
    graph = _graph(
        tmp_path,
        "class Box\n def target; end\n alias_method :copy, :target\n"
        f" {effect}\n def entry; copy; end\nend\n",
    )
    edge = next(
        e for e in graph.edges if e.source_id == "aliases:Box.entry" and e.kind == EdgeKind.CALLS
    )
    assert graph.nodes[edge.target_id].origin == NodeOrigin.PROXY


def test_later_reopened_dynamic_writer_blocks_prior_alias(tmp_path: Path) -> None:
    graph = _graph(
        tmp_path,
        "class Box\n def target; end\n alias_method :copy, :target\n"
        " def entry; copy; end\nend\nclass Box\n define_method(:copy) { other }\nend\n",
    )
    edge = next(
        e for e in graph.edges if e.source_id == "aliases:Box.entry" and e.kind == EdgeKind.CALLS
    )
    assert graph.nodes[edge.target_id].origin == NodeOrigin.PROXY


def test_inherited_alias_does_not_bypass_unmodeled_subclass_writers(tmp_path: Path) -> None:
    graph = _graph(
        tmp_path,
        "class Base\n def target; end\n alias_method :copy, :target\nend\n"
        "class Box < Base\n define_method(:copy) { other }\n def entry; copy; end\nend\n",
    )
    edge = next(
        e for e in graph.edges if e.source_id == "aliases:Box.entry" and e.kind == EdgeKind.CALLS
    )
    assert graph.nodes[edge.target_id].origin == NodeOrigin.PROXY


def test_inline_and_named_visibility_preserve_a_proven_alias(tmp_path: Path) -> None:
    graph = _graph(
        tmp_path,
        "class Box\n private def target; end\n alias_method :copy, :target\n"
        " private :copy\n def entry; copy; end\nend\n",
    )
    edge = next(
        e for e in graph.edges if e.source_id == "aliases:Box.entry" and e.kind == EdgeKind.CALLS
    )
    assert edge.target_id == "aliases:Box.target"
