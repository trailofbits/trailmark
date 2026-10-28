"""Human-reference lookup obeys precedence and retains insertion order within ties."""

from hypothesis import example, given, settings
from hypothesis import strategies as st

from trailmark.models import CodeGraph, CodeUnit, NodeKind, NodeOrigin, SourceLocation
from trailmark.storage.graph_store import GraphStore

_LOCATION = SourceLocation("lookup.c", 1, 1)
# Buckets encode the public precedence independently of the matching algorithm:
# concrete exact names, concrete colon aliases, concrete dot aliases, then proxies.
_BUCKETS = ((0, 1), (2, 3), (4, 11), (5,), (6,), (7,))
_UNITS = [
    CodeUnit("mod:exact_a", "transfer", NodeKind.FUNCTION, _LOCATION),
    CodeUnit("mod:exact_b", "transfer@9_2", NodeKind.FUNCTION, _LOCATION),
    CodeUnit("mod:transfer", "renamed", NodeKind.FUNCTION, _LOCATION),
    CodeUnit("other:transfer@3_2", "renamed", NodeKind.FUNCTION, _LOCATION),
    CodeUnit("mod:C.transfer", "renamed", NodeKind.METHOD, _LOCATION),
    CodeUnit("proxy:thing", "transfer", NodeKind.PROXY, _LOCATION),
    CodeUnit("proxy:transfer", "renamed", NodeKind.FUNCTION, _LOCATION, origin=NodeOrigin.PROXY),
    CodeUnit("proxy:C.transfer", "renamed", NodeKind.PROXY, _LOCATION, origin=NodeOrigin.PROXY),
    CodeUnit("mod:transfer@release_3", "unrelated", NodeKind.FUNCTION, _LOCATION),
    CodeUnit("mod:transfers", "unrelated", NodeKind.FUNCTION, _LOCATION),
    CodeUnit("mod:C.transfer@3_2x", "unrelated", NodeKind.FUNCTION, _LOCATION),
    CodeUnit("other:C.transfer@3_2", "renamed", NodeKind.METHOD, _LOCATION),
]


@given(order=st.lists(st.integers(0, len(_UNITS) - 1), unique=True))
@example(order=[])
@example(order=[5, 4, 2, 0, 1])
@example(order=[1, 0, 3, 2, 4, 5, 6, 7])
@example(order=[8, 9, 10])
@example(order=[11])
@example(order=[7, 11, 4])
@settings(max_examples=200, deadline=None)
def test_human_reference_precedence_matches_semantic_buckets(order: list[int]) -> None:
    units = [_UNITS[index] for index in order]
    expected = next(
        (_UNITS[index] for bucket in _BUCKETS for index in order if index in bucket), None
    )
    store = GraphStore(CodeGraph(nodes={unit.id: unit for unit in units}))
    assert store.find_node("transfer") == expected
    assert store.find_node_id("transfer") == (expected.id if expected else None)
    for unit in units:
        assert store.find_node_id(unit.id) == unit.id
        assert store.find_node(unit.id) == unit
