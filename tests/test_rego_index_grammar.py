"""Reference indices must not poison unrelated rule call bindings."""

from pathlib import Path

import pytest

from trailmark.models.edges import EdgeKind
from trailmark.models.nodes import NodeOrigin
from trailmark.parse import parse_file


@pytest.mark.parametrize(
    "index", ["input.request.object.spec.type", "input.items[input.key]", "key(input.kind)"]
)
def test_reference_indices_preserve_rule_bodies_and_local_calls(tmp_path: Path, index: str) -> None:
    source = tmp_path / "policy.rego"
    source.write_text(
        "package p\nkey(x) = x\nhelper(x) = x\nallow {\n"
        ' types := {"NodePort", "ExternalName"}\n'
        f" types[{index}]\n helper(true)\n}}\n"
    )
    graph = parse_file(str(source), "rego", strict=True)
    assert not graph.parse_diagnostics
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS and e.target_id == "p:helper"]
    assert len(calls) == 1
    assert calls[0].location and calls[0].location.start_line == 7
    assert graph.nodes[calls[0].source_id].name == "$clause"


def test_index_text_in_literals_and_comments_does_not_create_declarations(tmp_path: Path) -> None:
    source = tmp_path / "policy.rego"
    source.write_text(
        'package p\nallow {\n text := "types[input.fake()]"\n'
        ' # types[input.comment()]\n types := {"x"}\n types[input.key]\n}\n'
    )
    graph = parse_file(str(source), "rego", strict=True)
    assert not graph.parse_diagnostics
    assert {n.name for n in graph.nodes.values()} == {"p", "allow", "$clause"}


def test_malformed_index_still_reports_recovery_and_blocks_guesses(tmp_path: Path) -> None:
    source = tmp_path / "policy.rego"
    source.write_text("package p\nhelper(x) = x\nallow {\n types[input.key\n helper(true)\n}\n")
    graph = parse_file(str(source), "rego")
    assert graph.parse_diagnostics
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS and e.source_id != "p:allow"]
    assert not any(e.target_id == "p:helper" for e in calls)
    assert any(graph.nodes[e.target_id].origin == NodeOrigin.PROXY for e in calls)
