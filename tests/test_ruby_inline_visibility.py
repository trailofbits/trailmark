"""Ruby visibility wrappers retain the declarations evaluated as their arguments."""

from pathlib import Path

import pytest

from trailmark.models.edges import EdgeKind
from trailmark.parse import parse_file


@pytest.mark.parametrize("visibility", ["private", "protected", "public"])
def test_inline_visibility_keeps_bodies_and_does_not_change_following_methods(
    tmp_path: Path, visibility: str
) -> None:
    source = tmp_path / "wrapped.rb"
    source.write_text(
        "class Box\n"
        "  def entry\n"
        "    wrapped\n"
        "  end\n"
        f"  {visibility} def wrapped(value = 0)\n"
        "    helper(value)\n"
        "  end\n"
        "  def helper(value = 0)\n"
        "    value\n"
        "  end\n"
        "end\n"
    )
    graph = parse_file(str(source), language="ruby")
    wrapped = graph.nodes["wrapped:Box.wrapped"]
    assert wrapped.location.start_line == 5
    assert wrapped.parameters[0].name == "value"
    assert dict(wrapped.attributes)["ruby_visibility"] == visibility
    assert dict(graph.nodes["wrapped:Box.helper"].attributes)["ruby_visibility"] == "public"
    assert any(
        edge.kind == EdgeKind.CONTAINS
        and edge.source_id == "wrapped:Box"
        and edge.target_id == wrapped.id
        for edge in graph.edges
    )
    calls = {
        (edge.source_id, edge.target_id) for edge in graph.edges if edge.kind == EdgeKind.CALLS
    }
    assert ("wrapped:Box.entry", wrapped.id) in calls
    assert (wrapped.id, "wrapped:Box.helper") in calls


@pytest.mark.mutation_fast
@pytest.mark.parametrize("container", ["module Helpers", "class Box\n  class << self"])
def test_inline_private_methods_keep_module_and_singleton_ownership(
    tmp_path: Path, container: str
) -> None:
    source = tmp_path / "owners.rb"
    source.write_text(
        f"{container}\n"
        "  private def wrapped\n"
        "    sink\n"
        "  end\n"
        "  def sink; end\n"
        "end\n" + ("end\n" if "class << self" in container else "")
    )
    graph = parse_file(str(source), language="ruby")
    owner = "owners:Box" if "class << self" in container else "owners:Helpers"
    receiver = ".self" if "class << self" in container else ""
    wrapped_id = f"{owner}{receiver}.wrapped"
    wrapped = graph.nodes[wrapped_id]
    assert dict(wrapped.attributes)["ruby_visibility"] == "private"
    assert any(
        edge.kind == EdgeKind.CALLS
        and edge.source_id == wrapped_id
        and edge.target_id == f"{owner}{receiver}.sink"
        for edge in graph.edges
    )


def test_inline_public_does_not_reset_an_enclosing_private_default(tmp_path: Path) -> None:
    source = tmp_path / "defaults.rb"
    source.write_text(
        "class Box\n  private\n  public def exposed; sink; end\n  def sink; end\nend\n"
    )
    graph = parse_file(str(source), language="ruby")
    assert dict(graph.nodes["defaults:Box.exposed"].attributes)["ruby_visibility"] == "public"
    assert dict(graph.nodes["defaults:Box.sink"].attributes)["ruby_visibility"] == "private"


def test_inline_private_top_level_function_keeps_its_body(tmp_path: Path) -> None:
    source = tmp_path / "top.rb"
    source.write_text("private def wrapped; sink; end\ndef sink; end\n")
    graph = parse_file(str(source), language="ruby")
    assert dict(graph.nodes["top:wrapped"].attributes)["ruby_visibility"] == "private"
    assert any(
        edge.kind == EdgeKind.CALLS
        and edge.source_id == "top:wrapped"
        and edge.target_id == "top:sink"
        for edge in graph.edges
    )
