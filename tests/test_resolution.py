"""Work bounds and conservative lookup contracts shared by formal frontends."""

from collections.abc import Iterator

import pytest

from trailmark.analysis.proxies import materialize_type_use_edges
from trailmark.models._parsing import ArgumentScope, Lookup, PendingReference
from trailmark.models.edges import CodeEdge, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit, NodeKind, SourceLocation, TypeRef
from trailmark.parsers._formal_linking import _Symbols
from trailmark.parsers._resolution import SymbolIndex, module_visibility
from trailmark.query.api import QueryEngine


def _unit(ident: str, name: str, kind: NodeKind = NodeKind.FUNCTION) -> CodeUnit:
    return CodeUnit(ident, name, kind, SourceLocation(f"{ident}.fst", 1, 2))


@pytest.mark.mutation_fast
def test_ambiguity_in_a_nearer_tier_stops_lookup() -> None:
    units = [_unit(name, name) for name in ("a", "b", "fallback")]
    index = SymbolIndex(CodeGraph(nodes={unit.id: unit for unit in units}), "unused")
    index.names = {"near": units[:2], "far": units[2:]}
    assert index.resolve((("near",), ("far",)), lambda found: (u for _, u in found)) == (
        True,
        None,
    )
    assert index.resolve((("missing",), ("far",)), lambda found: (u for _, u in found)) == (
        True,
        "fallback",
    )
    assert index.resolve((("missing",),), lambda found: (u for _, u in found)) == (False, None)


@pytest.mark.mutation_fast
def test_visibility_terminates_cycles_and_keeps_disconnected_modules_separate() -> None:
    assert module_visibility({"a": {"b"}, "b": {"a", "c"}, "c": set(), "d": set()}) == {
        "a": {"a", "b", "c"},
        "b": {"a", "b", "c"},
        "c": {"c"},
        "d": {"d"},
    }


class _CountedNodes(dict[str, CodeUnit]):
    visits = 0

    def values(self) -> Iterator[CodeUnit]:  # ty: ignore[incompatible-method-override]
        for unit in super().values():
            self.visits += 1
            yield unit


@pytest.mark.mutation_fast
@pytest.mark.parametrize("suffix", ["fst", "fsti"])
@pytest.mark.parametrize("size", [40, 80, 160])
def test_import_and_interface_indexes_have_linear_declaration_visits(
    suffix: str, size: int
) -> None:
    nodes, edges = _CountedNodes(), []
    for i in range(size):
        path = f"/virtual/M{i}.{suffix}"
        location = SourceLocation(path, 1, 2)
        nodes[f"m{i}"] = CodeUnit(
            f"m{i}",
            f"M{i}",
            NodeKind.MODULE,
            location,
            attributes=(("formal_modules", f'["M{i}"]'),),
        )
        nodes[f"m{i}:M{i}"] = CodeUnit(
            f"m{i}:M{i}",
            f"M{i}",
            NodeKind.NAMESPACE,
            location,
            attributes=(("qualified_name", f"M{i}"),),
        )
        if i:
            edges.append(
                CodeEdge(
                    f"m{i}",
                    f"@import:M{i - 1}",
                    EdgeKind.IMPORTS,
                    attributes=(("import_name", f"M{i - 1}"),),
                )
            )
    graph = CodeGraph(nodes=nodes, edges=edges)
    symbols = _Symbols(graph)
    assert len(graph.edges) == size - 1
    assert symbols.visible[f"m{size - 1}"] == {f"m{i}" for i in range(size)}
    assert nodes.visits <= 4 * len(nodes)


def test_pending_references_survive_merges_with_the_same_owner() -> None:
    graphs = [CodeGraph(), CodeGraph()]
    references = [PendingReference(lookup=Lookup(((name,),))) for name in ("a", "b")]
    for graph, reference in zip(graphs, references, strict=True):
        graph.edges.append(
            graph._parse_state.stage(CodeEdge("owner", "@pending", EdgeKind.REFERENCES), reference)
        )
        graph._parse_state.arguments["owner"] = {"argument": ArgumentScope(1, 0, None)}
    merged = CodeGraph()
    for graph in graphs:
        merged.merge(graph)
        graph._parse_state.clear_references()
    assert [merged._parse_state.reference(edge) for edge in merged.edges] == references
    assert merged._parse_state.arguments == {"owner": {"argument": ArgumentScope(1, 0, None)}}
    merged._parse_state.clear_references()
    assert all(merged._parse_state.reference(edge) is None for edge in merged.edges)
    assert not merged._parse_state.arguments


def test_frontend_type_resolution_survives_merges_without_rebinding_raw_types() -> None:
    source = CodeUnit(
        "a:caller",
        "caller",
        NodeKind.FUNCTION,
        SourceLocation("a.fst", 1, 2),
        return_type=TypeRef("Target"),
    )
    graph = CodeGraph(nodes={source.id: source}, language="python")
    graph._parse_state.resolved_type_files.add("a.fst")
    merged = CodeGraph(language="python")
    merged.merge(graph)
    target = _unit("b:Target", "Target", NodeKind.STRUCT)
    fallback = CodeUnit(
        "b:legacy",
        "legacy",
        NodeKind.FUNCTION,
        SourceLocation("b.fst", 1, 2),
        return_type=TypeRef("Target"),
    )
    merged.merge(CodeGraph(nodes={u.id: u for u in (target, fallback)}))
    for _ in range(2):
        materialize_type_use_edges(merged)
    assert [(e.source_id, e.target_id) for e in merged.edges] == [(fallback.id, target.id)]
    assert "_parse_state" not in QueryEngine.from_graph(merged).to_json()
