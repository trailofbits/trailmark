"""Keep focused unit tests ahead of expensive integration without dropping tests."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from tests.mutation_order import prioritize_tests, relative_node_ids

pytestmark = pytest.mark.mutation_infrastructure


@pytest.mark.parametrize(
    ("source", "focused"),
    [
        ("tree_sitter_custom/verus/__init__.py", "test_verus_grammar.py"),
        ("parsers/rust/parser.py", "test_rust_parser.py"),
        ("parsers/python/parser.py", "test_python_parser.py"),
        ("parsers/_common.py", "test_common_parser.py"),
        ("models/nodes.py", "test_models.py"),
        ("storage/graph_store.py", "test_storage.py"),
        ("query/api.py", "test_query.py"),
        ("analysis/entrypoints.py", "test_entrypoints.py"),
    ],
)
def test_component_first_retains_every_test(source: str, focused: str) -> None:
    integration = "tests/test_integration.py::test_cross_component"
    unit = f"tests/{focused}::test_boundary[ZERO]"
    ids = [integration, unit, "tests/test_kat_parsers.py::test_parser_snapshot[rust-taxonomy]"]
    ordered = prioritize_tests(f"/checkout/src/trailmark/{source}", ids)
    assert ordered == [unit, integration, ids[2]]
    assert Counter(ordered) == Counter(ids)


def test_unknown_component_keeps_collection_order() -> None:
    ids = ["tests/test_other.py::test_one", "tests/test_integration.py::test_two"]
    assert prioritize_tests("src/trailmark/new_feature.py", ids) == ids


def test_parameter_ids_are_preserved_exactly(tmp_path: Path) -> None:
    ids = [
        f"{tmp_path}/tests/test_values.py::test_value[ONE]",
        f"{tmp_path}/tests/test_values.py::test_value[TWO]",
        "tests/test_values.py::test_value[spaces [INNER]]",
    ]
    assert relative_node_ids(ids, tmp_path) == [
        "tests/test_values.py::test_value[ONE]",
        "tests/test_values.py::test_value[TWO]",
        ids[2],
    ]
