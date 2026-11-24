import json

from trailmark.models import CodeEdge, CodeGraph, CodeUnit, EdgeKind, NodeKind, SourceLocation
from trailmark.query.api import QueryEngine
from trailmark.storage.graph_store import GraphStore


def test_unresolved_relationships_agree_at_each_layer() -> None:
    location = SourceLocation("app.py", 1, 1)
    unit = CodeUnit("app:action", "action", NodeKind.FUNCTION, location)
    edges = [
        CodeEdge(unit.id, "external:Base", EdgeKind.INHERITS, location=location),
        CodeEdge("missing:Owner", unit.id, EdgeKind.CONTAINS),
    ]
    graph = CodeGraph(nodes={unit.id: unit}, edges=list(edges))
    store = GraphStore(graph)
    engine = QueryEngine(store)
    assert store.unresolved_relationships() == graph.unresolved_relationships() == edges
    rows = engine.unresolved_relationships()
    assert [(row["missing_source"], row["missing_target"]) for row in rows] == [
        (False, True),
        (True, False),
    ]
    assert rows[0]["reason"] == "endpoint_not_in_graph"
    assert rows[0]["location"]["start_line"] == 1
    assert json.loads(engine.to_json())["unresolved_relationships"] == rows
    assert graph.edges == edges
