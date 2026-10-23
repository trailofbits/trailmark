"""Receiver inference preserves assignment order with one index per body."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from tree_sitter import Node

from trailmark.models import EdgeKind
from trailmark.parse import parse_file
from trailmark.parsers import _receiver_index


@pytest.mark.parametrize(
    "body,targets",
    [
        ("let x=new A();x.m();x=new B();x.m();", ["app:A.m", "app:B.m"]),
        ("let x=new A();x.m();x=unknown;x.m();", ["app:A.m", "proxy.unresolved:x.m"]),
        ("let x=new A();if(flag){x=new B();}x.m();", ["proxy.unresolved:x.m"]),
        ("let x=new A();if(flag){x=new B();x.m();}", ["app:B.m"]),
        ("let x=new A();if(flag){x=new B();}else{x.m();}", ["proxy.unresolved:x.m"]),
        ("let x=new A();{let x=new B();x.m();}x.m();", ["app:B.m", "app:A.m"]),
        ("let x=new A();function nested(){let x=new B();}x.m();", ["app:A.m"]),
        ("let x=new A();class Local{method(x){x.m();}}", ["proxy.unresolved:x.m"]),
    ],
)
def test_receiver_assignment_order(tmp_path: Path, body: str, targets: list[str]) -> None:
    path = tmp_path / "app.ts"
    path.write_text("class A{m(){}} class B{m(){}} function run(flag){" + body + "}")
    graph = parse_file(str(path))
    calls = [
        edge
        for edge in graph.edges
        if edge.kind == EdgeKind.CALLS and not dict(edge.attributes).get("deferred")
    ]
    assert [edge.target_id for edge in calls] == targets


def test_many_receivers_index_each_body_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scanned = []
    original = _receiver_index.owned_syntax

    def capture(root: Node, types: frozenset[str], *, boundaries: frozenset[str]) -> Iterator[Node]:
        scanned.append(root.id)
        yield from original(root, types, boundaries=boundaries)

    monkeypatch.setattr(_receiver_index, "owned_syntax", capture)
    path = tmp_path / "app.ts"
    path.write_text("class A{m(){}} function run(){let x=new A();" + "x.m();" * 2000 + "}")
    graph = parse_file(str(path))
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    assert len(calls) == 2000 and all(edge.target_id == "app:A.m" for edge in calls)
    assert len(scanned) == 1
    # A later file must not reuse syntax or assignments from the previous parse.
    path.write_text("function run(x){x.m();}")
    graph = parse_file(str(path))
    assert (
        next(edge for edge in graph.edges if edge.kind == EdgeKind.CALLS).target_id
        == "proxy.unresolved:x.m"
    )
