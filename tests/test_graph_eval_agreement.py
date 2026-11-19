"""Reader agreement ignores private keys and never treats omissions as negatives."""

from __future__ import annotations

import copy
from typing import Any

from tests.graph_eval.agreement import reader_agreement


def _reference() -> dict[str, Any]:
    return {
        "status": "partial",
        "inventory_complete": False,
        "declarations": [
            {"key": "f", "name": "run", "role": "function", "location": {"path": "a.py", "line": 1}}
        ],
        "assertions": [
            {
                "subject": "f",
                "kind": "calls",
                "site": {"path": "a.py", "line": 2},
                "target_key": None,
                "target_name": "external",
                "expectation": "present",
                "resolution": "unresolved",
                "allowed_confidence": ["uncertain"],
                "context": "runtime execution",
            }
        ],
    }


def test_private_keys_do_not_create_disagreement() -> None:
    a = _reference()
    b = copy.deepcopy(a)
    b["declarations"][0]["key"] = "different"
    b["assertions"][0]["subject"] = "different"
    result = reader_agreement(a, b)
    assert result["shared_claims"] == 1
    assert result["shared_declaration_anchors"] == 1


def test_omitted_and_unknown_claims_are_not_negatives() -> None:
    a = _reference()
    b = copy.deepcopy(a)
    b["assertions"][0]["expectation"] = "unknown"
    result = reader_agreement(a, b)
    assert result["claims_only_a"] == 1
    assert result["claims_only_b"] == 0
    assert result["readers"][1]["unknown_assertions"] == 1


def test_execution_and_specification_contexts_are_distinct() -> None:
    a = _reference()
    b = copy.deepcopy(a)
    b["assertions"][0]["context"] = "proof/specification only; not executed"
    assert reader_agreement(a, b)["shared_claims"] == 0
