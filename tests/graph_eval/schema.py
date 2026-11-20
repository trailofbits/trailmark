"""Strict, versioned contracts for independent graph-evaluation evidence."""

from __future__ import annotations

import copy
from collections.abc import Iterable
from pathlib import PurePosixPath
from typing import Any

REFERENCE_SCHEMA_VERSION = 2
ADJUDICATION_SCHEMA_VERSION = 1
CONFIDENCE = ["certain", "inferred", "uncertain"]
RELATIONS = [
    "calls",
    "references",
    "contains",
    "imports",
    "inherits",
    "implements",
    "type_uses",
    "reads",
    "writes",
    "resolves_to",
    "specializes",
    "corresponds_to",
    "diagnostic",
]
QUESTION_RELATIONS = [kind for kind in RELATIONS if kind != "diagnostic"]
CATEGORIES = [
    "confirmed_defect",
    "documented_limitation",
    "capability_need",
    "expectation_error",
    "comparison_error",
    "intentional_approximation",
    "unresolved",
    "agree",
]


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _array(items: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": items}


_STRING: dict[str, Any] = {"type": "string"}
_NULLABLE: dict[str, Any] = {"type": ["string", "null"]}
LOCATION_SCHEMA = _object(
    {
        "path": _STRING,
        "line": {"type": "integer", "minimum": 1},
        "end_line": {"type": "integer", "minimum": 1},
        "column": {"type": ["integer", "null"], "minimum": 0},
        "end_column": {"type": ["integer", "null"], "minimum": 0},
    }
)
REFERENCE_SCHEMA = _object(
    {
        "schema_version": {"type": "integer", "enum": [REFERENCE_SCHEMA_VERSION]},
        "case_id": _STRING,
        "status": {"type": "string", "enum": ["complete", "partial", "needs_context"]},
        "inventory_complete": {"type": "boolean"},
        "declarations": _array(
            _object(
                {
                    "key": _STRING,
                    "name": _STRING,
                    "role": _STRING,
                    "graph_kind": _NULLABLE,
                    "owner": _NULLABLE,
                    "location": LOCATION_SCHEMA,
                    "evidence": _STRING,
                }
            )
        ),
        "assertions": _array(
            _object(
                {
                    "id": _STRING,
                    "subject": _STRING,
                    "kind": {"type": "string", "enum": RELATIONS},
                    "site": LOCATION_SCHEMA,
                    "target_key": _NULLABLE,
                    "target_name": _NULLABLE,
                    "expectation": {"type": "string", "enum": ["present", "absent", "unknown"]},
                    "resolution": {"type": "string", "enum": ["resolved", "unresolved", "either"]},
                    "allowed_confidence": _array({"type": "string", "enum": CONFIDENCE}),
                    "context": _STRING,
                    "evidence": _STRING,
                }
            )
        ),
        "questions": {
            **_array(
                _object(
                    {
                        "id": _STRING,
                        "operation": {
                            "type": "string",
                            "enum": [
                                "callees",
                                "callers",
                                "path",
                                "dependencies",
                                "ownership",
                            ],
                        },
                        "edge_kinds": {
                            **_array({"type": "string", "enum": QUESTION_RELATIONS}),
                            "minItems": 1,
                        },
                        "subject": _STRING,
                        "target": _NULLABLE,
                        "expected": _array(_STRING),
                        "question": _STRING,
                        "evidence": _STRING,
                    }
                )
            ),
            "minItems": 3,
            "maxItems": 3,
        },
        "notes": _array(_STRING),
    }
)
ADJUDICATION_SCHEMA = _object(
    {
        "schema_version": {"type": "integer", "enum": [ADJUDICATION_SCHEMA_VERSION]},
        "case_id": _STRING,
        "decisions": _array(
            _object(
                {
                    "claim_id": _STRING,
                    "category": {"type": "string", "enum": CATEGORIES},
                    "evidence": _STRING,
                    "expected": _STRING,
                    "observed": _STRING,
                    "reproduction": _STRING,
                }
            )
        ),
        "notes": _array(_STRING),
    }
)


def adjudication_schema(claim_ids: Iterable[str]) -> dict[str, Any]:
    """Constrain version-1 decisions to this job's exact required claim set.

    Semantic validation still rejects repeated IDs that substitute for missing
    claims. An empty claim set requires an empty array without an invalid empty enum.
    """
    schema = copy.deepcopy(ADJUDICATION_SCHEMA)
    allowed = sorted(set(claim_ids))
    decisions = schema["properties"]["decisions"]
    decisions.update(minItems=len(allowed), maxItems=len(allowed))
    if allowed:
        # The base schema shares _STRING with other properties. Replace this
        # property rather than adding the enum to its shared dictionary.
        properties = decisions["items"]["properties"]
        properties["claim_id"] = {**properties["claim_id"], "enum": allowed}
    return schema


def validate_schema(value: Any, schema: dict[str, Any], path: str = "$") -> None:
    """Validate the small JSON-Schema subset used here without extra dependencies."""
    types = schema["type"]
    types = types if isinstance(types, list) else [types]
    matches = {
        "null": value is None,
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "string": isinstance(value, str),
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
    }
    if not any(matches.get(kind, False) for kind in types):
        raise ValueError(f"{path}: expected {types}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: value outside enum")
    if isinstance(value, dict):
        properties = schema["properties"]
        if set(value) != set(properties):
            raise ValueError(f"{path}: missing/extra fields {set(value) ^ set(properties)}")
        for key, child in properties.items():
            validate_schema(value[key], child, f"{path}.{key}")
    elif isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get(
            "maxItems", len(value)
        ):
            raise ValueError(f"{path}: invalid item count")
        for index, item in enumerate(value):
            validate_schema(item, schema["items"], f"{path}[{index}]")
    elif (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value < schema.get("minimum", value)
    ):
        raise ValueError(f"{path}: below minimum")


def validate_output(value: Any, schema: dict[str, Any]) -> None:
    """Validate structured worker output against one of the strict contracts."""
    validate_schema(value, schema)


def _location(location: dict[str, Any]) -> None:
    path = PurePosixPath(location["path"])
    if path.is_absolute() or ".." in path.parts or not location["path"]:
        raise ValueError("Evidence paths must be project-relative POSIX paths")
    if location["end_line"] < location["line"]:
        raise ValueError("Evidence range ends before it starts")
    begin, end = location["column"], location["end_column"]
    if (
        location["line"] == location["end_line"]
        and begin is not None
        and end is not None
        and end < begin
    ):
        raise ValueError("Evidence columns end before they start")


def validate_reference(value: Any, case_id: str | None = None) -> None:
    validate_schema(value, REFERENCE_SCHEMA)
    if case_id is not None and value["case_id"] != case_id:
        raise ValueError("Reference belongs to a different case")
    keys = [item["key"] for item in value["declarations"]]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate declaration keys")
    for field in ("assertions", "questions"):
        ids = [item["id"] for item in value[field]]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate {field} IDs")

    def known(key: str | None) -> bool:
        return key is None or key in keys or key.startswith("file:")

    for item in value["declarations"]:
        _location(item["location"])
        if not known(item["owner"]):
            raise ValueError(f"Unknown declaration owner: {item['owner']}")
    for item in value["assertions"]:
        _location(item["site"])
        if not known(item["subject"]) or not known(item["target_key"]):
            raise ValueError("Assertion refers to an unknown declaration")
    for item in value["questions"]:
        validate_question_relations(item)
        if not all(known(key) for key in [item["subject"], item["target"], *item["expected"]]):
            raise ValueError("Question refers to an unknown declaration")
    if value["inventory_complete"] and value["status"] != "complete":
        raise ValueError("A partial reference cannot claim a complete inventory")


def validate_question_relations(question: dict[str, Any]) -> None:
    """Enforce explicit relation semantics, including constraints beyond JSON shape."""
    kinds = question.get("edge_kinds")
    if (
        not isinstance(kinds, list)
        or not kinds
        or any(kind not in QUESTION_RELATIONS for kind in kinds)
    ):
        raise ValueError("Questions require nonempty explicit edge_kinds without diagnostic")
    if len(kinds) != len(set(kinds)):
        raise ValueError("Question edge_kinds must be unique")
    operation = question["operation"]
    required = {"callees": ["calls"], "callers": ["calls"], "ownership": ["contains"]}
    if operation in required and kinds != required[operation]:
        raise ValueError(f"{operation} questions require edge_kinds={required[operation]}")


def validate_adjudication(value: Any, case_id: str | None = None) -> None:
    validate_schema(value, ADJUDICATION_SCHEMA)
    if case_id is not None and value["case_id"] != case_id:
        raise ValueError("Adjudication belongs to a different case")
    ids = [item["claim_id"] for item in value["decisions"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate adjudication claim IDs")
    for item in value["decisions"]:
        if item["category"] == "confirmed_defect" and not all(
            item[key].strip() for key in ("evidence", "expected", "observed", "reproduction")
        ):
            raise ValueError("Confirmed defects require evidence and a replayable reproduction")
