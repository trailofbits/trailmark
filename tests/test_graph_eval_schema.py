"""Per-job structured adjudications permit only the nominated decisions."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from tests.graph_eval.compare import validate_adjudication_claims
from tests.graph_eval.schema import (
    ADJUDICATION_SCHEMA,
    ADJUDICATION_SCHEMA_VERSION,
    adjudication_schema,
    validate_adjudication,
    validate_schema,
)


def _output(claim_ids: list[str]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "case_id": "case-with-independent-text",
        "decisions": [
            {
                "claim_id": claim_id,
                "category": "unresolved",
                "evidence": "The retained source evidence",
                "expected": "The expected dependency",
                "observed": "The measured dependency",
                "reproduction": "The retained replay command",
            }
            for claim_id in claim_ids
        ],
        "notes": ["Independent prose must not inherit the claim allowlist."],
    }


def test_adjudication_schema_sorts_exact_ids_and_preserves_version_one_output() -> None:
    schema = adjudication_schema(iter(["reader-B:question:z", "reader-A:assertion:a"]))
    decisions = schema["properties"]["decisions"]
    assert decisions["minItems"] == decisions["maxItems"] == 2
    assert decisions["items"]["properties"]["claim_id"]["enum"] == [
        "reader-A:assertion:a",
        "reader-B:question:z",
    ]
    output = _output(["reader-B:question:z", "reader-A:assertion:a"])
    validate_schema(output, schema)
    validate_adjudication(output, "case-with-independent-text")
    validate_adjudication_claims(output, {"reader-A:assertion:a", "reader-B:question:z"})
    assert schema["properties"]["schema_version"]["enum"] == [ADJUDICATION_SCHEMA_VERSION] == [1]


@pytest.mark.parametrize("extra", ["unknown-id", "known-but-not-required-agreement"])
def test_adjudication_schema_rejects_ids_outside_required_set(extra: str) -> None:
    schema = adjudication_schema({"required-a", "required-b"})
    with pytest.raises(ValueError, match=r"decisions\[1\]\.claim_id: value outside enum"):
        validate_schema(_output(["required-a", extra]), schema)


@pytest.mark.parametrize("ids", [["a"], ["a", "b", "a"]])
def test_adjudication_schema_rejects_missing_and_excess_decision_counts(ids: list[str]) -> None:
    with pytest.raises(ValueError, match="decisions: invalid item count"):
        validate_schema(_output(ids), adjudication_schema({"a", "b"}))


def test_zero_claims_require_empty_decisions_without_empty_enum() -> None:
    schema = adjudication_schema([])
    decisions = schema["properties"]["decisions"]
    assert decisions["minItems"] == decisions["maxItems"] == 0
    assert "enum" not in decisions["items"]["properties"]["claim_id"]
    validate_schema(_output([]), schema)
    validate_adjudication_claims(_output([]), set())
    with pytest.raises(ValueError, match="decisions: invalid item count"):
        validate_schema(_output(["extra"]), schema)


def test_adjudication_schema_copies_are_independent_and_leave_global_unchanged() -> None:
    original = copy.deepcopy(ADJUDICATION_SCHEMA)
    first = adjudication_schema(["a", "a", "b"])
    second = adjudication_schema(["b", "a"])
    assert first == second
    decisions = first["properties"]["decisions"]
    decisions["maxItems"] = 99
    decisions["items"]["properties"]["claim_id"]["enum"].append("extra")
    decisions["items"]["properties"]["category"]["enum"].append("private-category")
    first["properties"]["schema_version"]["enum"].append(99)
    assert second == adjudication_schema({"a", "b"})
    assert original == ADJUDICATION_SCHEMA
    assert "enum" not in second["properties"]["case_id"]
    assert "enum" not in second["properties"]["decisions"]["items"]["properties"]["evidence"]
    assert "enum" not in second["properties"]["notes"]["items"]


def test_semantic_exact_coverage_still_rejects_duplicate_substitution() -> None:
    output = _output(["a", "a"])
    validate_schema(output, adjudication_schema({"a", "b"}))
    with pytest.raises(ValueError, match="Duplicate adjudication claim IDs"):
        validate_adjudication_claims(output, {"a", "b"})
