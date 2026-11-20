"""Explicit case excerpts and lossless named tables for judge presentation.

Scoping changes the supplied context, with an exclusion ledger. Table encoding
preserves the excerpt exactly. Original frozen artifacts are never rewritten.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from collections.abc import Iterator
from typing import Any

ENCODING_VERSION = "graph-eval-evidence-tables-v1"
PROJECTION_VERSION = "graph-eval-case-context-v1"
_TABLES = {"$query": "queries", "$row": "rows", "$edge": "edges", "$list": "lists"}
_RESERVED = {*_TABLES, "$literal"}


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_digest(value: Any) -> str:
    return hashlib.sha256(compact_json(value).encode("utf-8")).hexdigest()


def _walk(value: Any) -> Iterator[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def scope_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    """Excerpt only demonstrably irrelevant default global context.

    Both readers, graph, source text, and every public_navigation field/answer
    remain unchanged. Unknown diagnostic locations and entrypoint records stay.
    """
    # JSON artifacts have value semantics. Break shared Python aliases too, so
    # excerpting a default response can never alter a reader navigation answer.
    result = json.loads(compact_json(payload))
    case = result["case"]
    scopes = [*case["primary"], *case.get("context", [])]
    node_ids = set(result["measurement"].get("graph", {}).get("nodes", {}))
    ledger: dict[str, Any] = {
        "version": PROJECTION_VERSION,
        "notice": (
            "Default project query context is a case excerpt, not an untouched API answer. "
            "Scalar counts remain global. Full originals remain in sealed measurement.json "
            "and comparisons.json. All public_navigation fields and answers are unchanged."
        ),
        "scopes": scopes,
        "query_excerpts": [],
        "mapping_excerpts": [],
    }

    def relevant(item: Any) -> bool:
        if not isinstance(item, dict):
            return True
        location = item.get("location", item)
        if not isinstance(location, dict):
            return True
        path = location.get("file_path", location.get("path"))
        if not path:
            return True
        candidates = [scope for scope in scopes if path == scope["path"]]
        if not candidates:
            return False
        start = location.get("start_line", location.get("line"))
        end = location.get("end_line", start)
        if type(start) is not int or type(end) is not int or start < 1 or end < start:
            return True
        return any(
            start <= scope["end_line"] and end >= scope["start_line"] for scope in candidates
        )

    containers = [("measurement", result["measurement"])]
    containers.extend((f"comparisons/{i}", row) for i, row in enumerate(result["comparisons"]))
    for pointer, container in containers:
        for index, query in enumerate(container.get("query_answers", [])):
            before = copy.deepcopy(query)
            operation, answer = query.get("operation"), query.get("answer")
            original: list[Any] | None = None
            keep: list[bool] = []
            rule = ""
            if operation == "summary" and isinstance(answer, dict):
                diagnostics = answer.get("parse_diagnostics")
                if isinstance(diagnostics, list):
                    original = diagnostics
                    keep = [relevant(item) for item in original]
                    rule = "summary.parse_diagnostics overlapping primary/context ranges"
            elif operation == "attack_surface" and isinstance(answer, list):
                original = answer
                keep = [
                    not isinstance(item, dict)
                    or not isinstance(item.get("node_id"), str)
                    or item["node_id"] in node_ids
                    for item in original
                ]
                rule = "attack_surface entrypoints in projected graph, including endpoints"
            if original is None or all(keep):
                continue
            kept = [item for item, retained in zip(original, keep, strict=True) if retained]
            excluded = [item for item, retained in zip(original, keep, strict=True) if not retained]
            if operation == "summary":
                query["answer"]["parse_diagnostics"] = kept
            else:
                query["answer"] = kept
            notice = {
                "version": PROJECTION_VERSION,
                "scope": "case excerpt, not the original global API response",
                "rule": rule,
                "original_count": len(original),
                "retained_count": len(kept),
                "excluded_count": len(excluded),
                "excluded_sha256": payload_digest(excluded),
                "original_response_sha256": payload_digest(before),
                "projected_response_sha256": payload_digest(query),
            }
            query["prompt_projection"] = notice
            ledger["query_excerpts"].append({"path": f"{pointer}/query_answers/{index}", **notice})

    # Cached older comparisons include every project file alias. Preserve every
    # alias actually named by a reader or scope, and every non-file mapping.
    mentioned = {
        item for item in _walk([result["reader_a"], result["reader_b"]]) if isinstance(item, str)
    }
    mentioned.update("file:" + scope["path"] for scope in scopes)
    for index, comparison in enumerate(result["comparisons"]):
        mapping = comparison.get("mapping")
        if not isinstance(mapping, dict):
            continue
        kept_mapping = {
            key: value
            for key, value in mapping.items()
            if not key.startswith("file:") or key in mentioned
        }
        if len(mapping) != len(kept_mapping):
            excluded_mapping = {
                key: value for key, value in mapping.items() if key not in kept_mapping
            }
            comparison["mapping"] = kept_mapping
            ledger["mapping_excerpts"].append(
                {
                    "path": f"comparisons/{index}/mapping",
                    "rule": "file: aliases mentioned by readers/scopes; all other keys kept",
                    "original_count": len(mapping),
                    "retained_count": len(kept_mapping),
                    "excluded_count": len(excluded_mapping),
                    "excluded_sha256": payload_digest(excluded_mapping),
                    "original_mapping_sha256": payload_digest(mapping),
                }
            )
    result["prompt_projection"] = ledger
    return result


def _escape(value: Any) -> Any:
    if isinstance(value, dict):
        if _RESERVED.intersection(value):
            return {"$literal": [[key, _escape(child)] for key, child in sorted(value.items())]}
        return {key: _escape(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_escape(child) for child in value]
    return value


def _pool(values: list[Any], minimum_bytes: int) -> tuple[dict[str, int], list[Any]]:
    counts = Counter(compact_json(value) for value in values)
    repeated = sorted(
        text for text, count in counts.items() if count > 1 and len(text.encode()) > minimum_bytes
    )
    return {text: index for index, text in enumerate(repeated)}, [
        json.loads(text) for text in repeated
    ]


def _replace_fields(value: Any, names: set[str], indexes: dict[str, int], tag: str) -> Any:
    if isinstance(value, dict):
        return {
            key: {tag: indexes[compact_json(child)]}
            if key in names and isinstance(child, list) and compact_json(child) in indexes
            else _replace_fields(child, names, indexes, tag)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_replace_fields(child, names, indexes, tag) for child in value]
    return value


def _edge(value: Any) -> bool:
    return isinstance(value, dict) and all(
        isinstance(value.get(key), str) for key in ("source", "target", "kind")
    )


def encode_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Share named evidence structures, escaping all original control keys."""
    value = _escape(payload)
    tables: dict[str, list[Any]] = {}
    indexes, tables["queries"] = _pool(
        [
            row["query_answers"]
            for row in _walk(value)
            if isinstance(row, dict) and isinstance(row.get("query_answers"), list)
        ],
        40,
    )
    value = _replace_fields(value, {"query_answers"}, indexes, "$query")
    slots = [
        (comparison, name)
        for comparison in value.get("comparisons", [])
        for name in ("assertions", "declarations", "discrepancies", "extras", "questions")
        if isinstance(comparison.get(name), list)
    ]
    indexes, tables["rows"] = _pool([row for parent, key in slots for row in parent[key]], 120)
    for parent, key in slots:
        parent[key] = [
            {"$row": indexes[compact_json(row)]} if compact_json(row) in indexes else row
            for row in parent[key]
        ]
    indexes, tables["edges"] = _pool(
        [row for row in _walk([value, tables["rows"], tables["queries"]]) if _edge(row)], 0
    )

    def edges(item: Any) -> Any:
        if _edge(item) and compact_json(item) in indexes:
            return {"$edge": indexes[compact_json(item)]}
        if isinstance(item, dict):
            return {key: edges(child) for key, child in item.items()}
        if isinstance(item, list):
            return [edges(child) for child in item]
        return item

    value, tables["rows"], tables["queries"] = (
        edges(value),
        edges(tables["rows"]),
        edges(tables["queries"]),
    )
    list_fields = {"candidates", "domain"}
    indexes, tables["lists"] = _pool(
        [
            row[key]
            for row in _walk([value, tables["rows"], tables["queries"]])
            if isinstance(row, dict)
            for key in sorted(list_fields)
            if isinstance(row.get(key), list)
        ],
        40,
    )
    value = _replace_fields(value, list_fields, indexes, "$list")
    for key in ("rows", "queries"):
        tables[key] = _replace_fields(tables[key], list_fields, indexes, "$list")
    encoded = {
        "encoding": ENCODING_VERSION,
        "logical_sha256": payload_digest(payload),
        "payload": value,
        "catalog": tables,
    }
    # Check the serialized transport, not shared in-memory objects.
    decode_payload(json.loads(compact_json(encoded)))
    return encoded


def decode_payload(encoded: dict[str, Any]) -> dict[str, Any]:
    """Reject malformed/cyclic refs and verify the reconstructed payload hash."""
    if encoded.get("encoding") != ENCODING_VERSION:
        raise ValueError("Unknown judge evidence encoding")
    tables = encoded.get("catalog")
    if not isinstance(tables, dict) or any(
        not isinstance(tables.get(key), list) for key in _TABLES.values()
    ):
        raise ValueError("Invalid judge evidence catalog")

    def decode(value: Any, active: frozenset[tuple[str, int]] = frozenset()) -> Any:
        if isinstance(value, dict):
            controls = _RESERVED.intersection(value)
            if controls:
                if len(value) != 1:
                    raise ValueError("Invalid judge evidence control object")
                tag = next(iter(controls))
                if tag == "$literal":
                    pairs = value[tag]
                    if not isinstance(pairs, list) or any(
                        not isinstance(pair, list) or len(pair) != 2 or not isinstance(pair[0], str)
                        for pair in pairs
                    ):
                        raise ValueError("Invalid literal judge evidence object")
                    restored = {key: decode(child, active) for key, child in pairs}
                    if len(restored) != len(pairs):
                        raise ValueError("Duplicate literal judge evidence key")
                    return restored
                index = value[tag]
                table = _TABLES[tag]
                if type(index) is not int or not 0 <= index < len(tables[table]):
                    raise ValueError("Invalid judge evidence reference index")
                identity = (table, index)
                if identity in active:
                    raise ValueError("Cyclic judge evidence reference")
                return decode(tables[table][index], active | {identity})
            return {key: decode(child, active) for key, child in value.items()}
        if isinstance(value, list):
            return [decode(child, active) for child in value]
        return value

    restored = decode(encoded.get("payload"))
    if not isinstance(restored, dict) or payload_digest(restored) != encoded.get("logical_sha256"):
        raise ValueError("Judge evidence logical payload hash differs")
    return restored


def provenance(logical: dict[str, Any], presentation: dict[str, Any]) -> dict[str, Any]:
    return {
        "encoding_version": ENCODING_VERSION,
        "projection_version": PROJECTION_VERSION,
        "logical_payload_sha256": payload_digest(logical),
        "presentation_sha256": payload_digest(presentation),
        "logical_payload_utf8_bytes": len(compact_json(logical).encode()),
        "presentation_utf8_bytes": len(compact_json(presentation).encode()),
    }
