"""Describe independent reader agreement without promoting it to ground truth."""

from __future__ import annotations

from typing import Any

from tests.graph_eval.util import digest


def _claims(reference: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], set[str]]:
    declarations = {
        item["key"]: {"name": item["name"], "role": item["role"], "location": item["location"]}
        for item in reference["declarations"]
    }

    def anchor(key: str | None) -> Any:
        return declarations.get(key, key) if key is not None else None

    claims = {}
    for item in reference["assertions"]:
        if item["expectation"] == "unknown":
            continue
        claim = {
            "subject": anchor(item["subject"]),
            "kind": item["kind"],
            "site": item["site"],
            "target": anchor(item["target_key"]),
            "target_name": item["target_name"],
            "expectation": item["expectation"],
            "resolution": item["resolution"],
            "allowed_confidence": sorted(item["allowed_confidence"]),
            "context": item["context"],
        }
        claims[digest(claim)] = claim
    return claims, {digest(value) for value in declarations.values()}


def reader_agreement(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Count exact canonical agreement, without guessing semantic equivalence.

    Location, role and spelling differences remain differences. A reader's
    omission is never a negative claim. Private declaration keys are ignored.
    """
    a, declarations_a = _claims(left)
    b, declarations_b = _claims(right)
    return {
        "basis": "Exact source anchors and claim fields; hypotheses, not truth labels",
        "readers": [
            {
                "status": item["status"],
                "inventory_complete": item["inventory_complete"],
                "declarations": len(item["declarations"]),
                "assertions": len(item["assertions"]),
                "unknown_assertions": sum(
                    claim["expectation"] == "unknown" for claim in item["assertions"]
                ),
            }
            for item in (left, right)
        ],
        "shared_declaration_anchors": len(declarations_a & declarations_b),
        "declaration_anchors_only_a": len(declarations_a - declarations_b),
        "declaration_anchors_only_b": len(declarations_b - declarations_a),
        "shared_claims": len(a.keys() & b.keys()),
        "claims_only_a": len(a.keys() - b.keys()),
        "claims_only_b": len(b.keys() - a.keys()),
        "warning": (
            "Unshared claims may reflect different coverage or wording. Agreement is not accuracy."
        ),
    }
