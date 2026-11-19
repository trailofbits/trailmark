"""Compare frozen source claims without treating predictions as ground truth."""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from typing import Any

from tests.graph_eval.schema import validate_adjudication, validate_reference


def validate_adjudication_claims(
    adjudication: dict[str, Any], expected_claim_ids: set[str]
) -> None:
    """Require one decision per nominated claim, without accepting invented IDs."""
    validate_adjudication(adjudication)
    observed = {decision["claim_id"] for decision in adjudication["decisions"]}
    if observed != expected_claim_ids:
        missing, extra = (
            sorted(expected_claim_ids - observed),
            sorted(observed - expected_claim_ids),
        )
        raise ValueError(f"Adjudication claim mismatch: missing={missing}, extra={extra}")


def _location(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not value:
        return None
    return {
        "path": value.get("path", value.get("file_path", "")),
        "line": value.get("line", value.get("start_line", 0)),
        "end_line": value.get("end_line", value.get("line", value.get("start_line", 0))),
        "column": value.get("column", value.get("start_col")),
        "end_column": value.get("end_column", value.get("end_col")),
    }


def _overlaps(left: dict[str, Any], right: dict[str, Any] | None) -> bool:
    if right is None or left["path"] != right["path"]:
        return False
    if left["end_line"] < right["line"] or right["end_line"] < left["line"]:
        return False
    if left["line"] == left["end_line"] == right["line"] == right["end_line"]:
        lc, le, rc, re = (left["column"], left["end_column"], right["column"], right["end_column"])
        if None not in (lc, le, rc, re):
            return max(lc, rc) < min(le, re) or lc == le == rc == re
    return True


def _source(edge: dict[str, Any]) -> str:
    return str(edge.get("source", edge.get("source_id", "")))


def _target(edge: dict[str, Any]) -> str:
    return str(edge.get("target", edge.get("target_id", "")))


def _attrs(value: dict[str, Any]) -> dict[str, Any]:
    return dict(value.get("attributes") or {})


def _unresolved(ident: str, nodes: dict[str, Any]) -> bool:
    return ident.startswith("proxy.unresolved:") or (
        nodes.get(ident, {}).get("kind") == "proxy"
        and not ident.startswith(("proxy.external:", "binary:"))
    )


def _file_nodes(nodes: dict[str, Any]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = defaultdict(list)
    for ident, node in nodes.items():
        location = _location(node.get("location"))
        if node.get("kind") == "module" and ":" not in ident and location:
            found["file:" + location["path"]].append(ident)
    return found


def _declarations(
    reference: dict[str, Any], graph: dict[str, Any]
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    nodes = graph.get("nodes", {})
    parents: dict[str, set[str]] = defaultdict(set)
    for edge in graph.get("edges", []):
        if edge.get("kind") == "contains":
            parents[_target(edge)].add(_source(edge))
    mapping = {key: values[0] for key, values in _file_nodes(nodes).items() if len(values) == 1}
    records: list[dict[str, Any]] = []
    pending = list(reference["declarations"])
    while pending:
        ready = [item for item in pending if item["owner"] is None or item["owner"] in mapping]
        if not ready:
            ready = pending[:1]
        for declaration in ready:
            pending.remove(declaration)
            key, expected = declaration["key"], declaration["location"]
            candidates = [
                ident
                for ident, node in nodes.items()
                if node.get("kind") != "proxy"
                and node.get("origin", "source") == "source"
                and node.get("name") == declaration["name"]
                and _overlaps(expected, _location(node.get("location")))
            ]
            owner = mapping.get(declaration["owner"])
            owned = [ident for ident in candidates if owner in parents[ident]] if owner else []
            if len(owned) == 1:
                candidates = owned
            record: dict[str, Any] = {
                "key": key,
                "location": expected,
                "candidates": candidates,
                "issues": [],
            }
            if not candidates:
                record["status"] = "missing"
            elif len(candidates) > 1:
                record["status"] = "ambiguous"
            else:
                ident = candidates[0]
                mapping[key] = ident
                record["status"] = "matched"
                if owner and owner not in parents[ident]:
                    record["issues"].append("wrong_owner")
                expected_kind = declaration["graph_kind"]
                if expected_kind is not None and nodes[ident].get("kind") != expected_kind:
                    record["issues"].append("wrong_kind")
            records.append(record)
    reverse: dict[str, list[str]] = defaultdict(list)
    for key, ident in mapping.items():
        if not key.startswith("file:"):
            reverse[ident].append(key)
    for record in records:
        ident = mapping.get(record["key"])
        if ident is not None and len(reverse[ident]) > 1:
            record["status"] = "ambiguous"
            record["issues"].append("nonunique_declaration_mapping")
    for keys in reverse.values():
        if len(keys) > 1:
            for key in keys:
                mapping.pop(key)
    return mapping, records


def _target_matches(
    assertion: dict[str, Any], edge: dict[str, Any], mapping: dict[str, str], nodes: dict[str, Any]
) -> bool:
    target_key, name, ident = assertion["target_key"], assertion["target_name"], _target(edge)
    if target_key is not None:
        return target_key in mapping and mapping[target_key] == ident
    if name is None:
        return True
    return name in {nodes.get(ident, {}).get("name"), _attrs(edge).get("raw_target"), ident}


def _assertion(
    assertion: dict[str, Any],
    graph: dict[str, Any],
    mapping: dict[str, str],
    diagnostics: list[Any],
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": assertion["id"],
        "claim_id": "assertion:" + assertion["id"],
        "kind": assertion["kind"],
        "site": assertion["site"],
        "issues": [],
        "call_site_retained": None,
        "target_resolved": None,
        "candidates": [],
    }
    if assertion["expectation"] == "unknown":
        return {**record, "status": "unknown"}
    if assertion["kind"] == "diagnostic":
        hits = [
            d
            for d in diagnostics
            if _overlaps(assertion["site"], _location(d.get("location", d)))
            and (assertion["target_name"] is None or assertion["target_name"] == d.get("kind"))
        ]
        okay = bool(hits) == (assertion["expectation"] == "present")
        return {
            **record,
            "status": "agree" if okay else "discrepancy",
            "candidates": hits,
            "issues": []
            if okay
            else ["missing_diagnostic" if not hits else "unexpected_diagnostic"],
        }
    subject = mapping.get(assertion["subject"])
    if subject is None:
        return {**record, "status": "blocked", "issues": ["unmatched_subject"]}
    edges, nodes = graph.get("edges", []), graph.get("nodes", {})
    located = [
        edge for edge in edges if _overlaps(assertion["site"], _location(edge.get("location")))
    ]
    owned = [edge for edge in located if _source(edge) == subject]
    candidates = owned or located
    same_kind = [edge for edge in candidates if edge.get("kind") == assertion["kind"]]
    if same_kind:
        candidates = same_kind
    if assertion["expectation"] == "absent":
        if assertion["target_key"] is not None and assertion["target_key"] not in mapping:
            return {**record, "status": "blocked", "issues": ["unmatched_target"]}
        forbidden = [
            edge
            for edge in owned
            if edge.get("kind") == assertion["kind"]
            and _target_matches(assertion, edge, mapping, nodes)
        ]
        unlocated = [
            edge
            for edge in edges
            if _source(edge) == subject
            and edge.get("kind") == assertion["kind"]
            and not edge.get("location")
            and _target_matches(assertion, edge, mapping, nodes)
        ]
        if not forbidden and unlocated:
            return {
                **record,
                "status": "unlocated",
                "candidates": unlocated,
                "issues": ["unlocated_site"],
            }
        return {
            **record,
            "status": "discrepancy" if forbidden else "agree",
            "candidates": forbidden,
            "issues": ["unexpected_relationship"] if forbidden else [],
        }
    if not candidates:
        unlocated = [
            edge
            for edge in edges
            if _source(edge) == subject
            and edge.get("kind") == assertion["kind"]
            and edge.get("location") is None
            and _target_matches(assertion, edge, mapping, nodes)
        ]
        if unlocated:
            if assertion["kind"] in {"contains", "imports", "inherits", "implements", "type_uses"}:
                candidates = unlocated
            else:
                return {
                    **record,
                    "status": "unlocated",
                    "candidates": unlocated,
                    "issues": ["unlocated_site"],
                }
        else:
            return {
                **record,
                "status": "discrepancy",
                "issues": ["missing_relationship"],
                "call_site_retained": False if assertion["kind"] == "calls" else None,
            }
    if len(candidates) != 1:
        return {
            **record,
            "status": "ambiguous",
            "candidates": candidates,
            "issues": ["ambiguous_site"],
        }
    edge = candidates[0]
    record["candidates"] = candidates
    record["call_site_retained"] = (
        edge.get("kind") == "calls" if assertion["kind"] == "calls" else None
    )
    record["target_resolved"] = not _unresolved(_target(edge), nodes)
    if _source(edge) != subject:
        record["issues"].append("wrong_owner")
    if edge.get("kind") != assertion["kind"]:
        record["issues"].append("wrong_kind")
    if assertion["target_key"] is not None and assertion["target_key"] not in mapping:
        record["issues"].append("unmatched_target")
    elif not _target_matches(assertion, edge, mapping, nodes):
        record["issues"].append(
            "unresolved_target" if not record["target_resolved"] else "wrong_target"
        )
    resolution = assertion["resolution"]
    if resolution == "resolved" and not record["target_resolved"]:
        if "unresolved_target" not in record["issues"]:
            record["issues"].append("unresolved_target")
    elif resolution == "unresolved" and record["target_resolved"]:
        record["issues"].append("unexpected_resolution")
    if (
        assertion["allowed_confidence"]
        and edge.get("confidence") not in assertion["allowed_confidence"]
    ):
        record["issues"].append("wrong_confidence")
    record["status"] = "discrepancy" if record["issues"] else "agree"
    return record


def _questions(
    reference: dict[str, Any], graph: dict[str, Any], mapping: dict[str, str], profile: str
) -> list[dict[str, Any]]:
    """Evaluate bounded navigation in the enriched graph, not the public API."""
    domain = set(mapping.values())
    records = []
    for question in reference["questions"]:
        operation = question["operation"]
        record = {
            "id": question["id"],
            "claim_id": "question:" + question["id"],
            "basis": "enriched_graph",
            "operation": operation,
            "expected": question["expected"],
            "observed": [],
            "issues": [],
            "answer_scope": "complete_inventory"
            if reference["inventory_complete"]
            else "positive_subset",
        }
        keys = [question["subject"], *question["expected"]]
        if question["target"] is not None:
            keys.append(question["target"])
        if any(key not in mapping for key in keys):
            records.append({**record, "status": "blocked", "issues": ["unmatched_declaration"]})
            continue
        if (
            not question["expected"]
            and reference["inventory_complete"]
            and any(item["key"] not in mapping for item in reference["declarations"])
        ):
            records.append({**record, "status": "blocked", "issues": ["unmatched_declaration"]})
            continue
        subject = mapping[question["subject"]]
        expected = [mapping[key] for key in question["expected"]]
        kinds = {"calls"}
        if profile != "runtime" and operation in {"path", "dependencies"}:
            kinds |= {"references", "type_uses", "corresponds_to"}
        if operation == "dependencies":
            kinds |= {"imports", "inherits", "implements", "references", "type_uses"}
        elif operation == "ownership":
            kinds = {"contains"}
        adjacency: dict[str, set[str]] = defaultdict(set)
        for edge in graph.get("edges", []):
            source, target = _source(edge), _target(edge)
            if edge.get("kind") in kinds and source in domain and target in domain:
                if operation == "callers":
                    source, target = target, source
                adjacency[source].add(target)
        if operation == "path":
            target = mapping.get(question["target"])
            if target is None:
                records.append({**record, "status": "unknown", "issues": ["unspecified_target"]})
                continue
            if expected:
                okay = (
                    expected[0] == subject
                    and expected[-1] == target
                    and all(
                        right in adjacency[left]
                        for left, right in zip(expected, expected[1:], strict=False)
                    )
                )
                observed = expected if okay else []
            elif not reference["inventory_complete"]:
                records.append({**record, "status": "unknown", "issues": ["incomplete_inventory"]})
                continue
            else:
                queue = deque([[subject]])
                visited = {subject}
                observed = []
                while queue:
                    path = queue.popleft()
                    if path[-1] == target:
                        observed = path
                        break
                    for neighbor in sorted(adjacency[path[-1]] - visited):
                        visited.add(neighbor)
                        queue.append([*path, neighbor])
                okay = not observed
        else:
            observed = sorted(adjacency[subject])
            if not reference["inventory_complete"] and not expected:
                records.append({**record, "status": "unknown", "issues": ["incomplete_inventory"]})
                continue
            okay = (
                set(observed) == set(expected)
                if reference["inventory_complete"]
                else set(expected) <= set(observed)
            )
        records.append(
            {
                **record,
                "status": "agree" if okay else "discrepancy",
                "observed": observed,
                "issues": [] if okay else ["navigation_mismatch"],
            }
        )
    return records


def _prevent_site_reuse(reference: dict[str, Any], assertions: list[dict[str, Any]]) -> None:
    """A single observed edge cannot establish two distinct predicted sites."""
    owners: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    expectations = {item["id"]: item["expectation"] for item in reference["assertions"]}
    for row in assertions:
        if expectations[row["id"]] == "present" and len(row["candidates"]) == 1:
            owners[(id(row["candidates"][0]), row["kind"])].append(row)
    for rows in owners.values():
        if len(rows) > 1:
            for row in rows:
                row["status"] = "ambiguous"
                row["issues"].append("ambiguous_claim_mapping")
                row["call_site_retained"] = None
                row["target_resolved"] = None


def _inside(location: dict[str, Any] | None, scopes: list[dict[str, Any]]) -> bool:
    return bool(location) and any(
        location["path"] == scope["path"]
        and scope["start_line"] <= location["line"]
        and location["end_line"] <= scope["end_line"]
        for scope in scopes
    )


def _extras(
    reference: dict[str, Any],
    graph: dict[str, Any],
    mapping: dict[str, str],
    declarations: list[dict[str, Any]],
    assertions: list[dict[str, Any]],
    case: dict[str, Any],
) -> list[dict[str, Any]]:
    """Nominate extras only inside an explicitly complete primary inventory."""
    scopes = case.get("primary", [])
    if not reference["inventory_complete"] or not scopes:
        return []
    records = []
    # Ambiguous candidates are already represented by their declaration claim.
    represented = set(mapping.values()) | {
        ident for declaration in declarations for ident in declaration["candidates"]
    }
    for ident, node in sorted(graph.get("nodes", {}).items()):
        location = _location(node.get("location"))
        if (
            ident not in represented
            and node.get("kind") not in {"proxy", "module"}
            and node.get("origin", "source") == "source"
            and _inside(location, scopes)
        ):
            records.append(
                {
                    "claim_id": "extra_declaration:" + ident,
                    "status": "provisional",
                    "issues": ["extra_declaration"],
                    "observed": node,
                    "node_id": ident,
                }
            )
    claims = {item["id"]: item for item in reference["assertions"]}
    represented_edges = [edge for row in assertions for edge in row["candidates"]]
    for index, edge in enumerate(graph.get("edges", [])):
        location = _location(edge.get("location"))
        # Ownership edges often have no independent source site. A source unit's
        # broad declaration anchor is not evidence that an edge is in scope.
        if edge.get("kind") == "contains" or not _inside(location, scopes):
            continue
        if edge in represented_edges:
            continue
        if any(
            item["expectation"] == "unknown"
            and item["kind"] == edge.get("kind")
            and mapping.get(item["subject"]) == _source(edge)
            and _overlaps(item["site"], location)
            for item in claims.values()
        ):
            continue
        records.append(
            {
                "claim_id": f"extra_relationship:{index}",
                "status": "provisional",
                "issues": ["extra_relationship"],
                "observed": edge,
                "edge_index": index,
            }
        )
    return records


def compare_reference(
    reference: dict[str, Any], measurement: dict[str, Any], case: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Compare source hypotheses; discrepancies require independent adjudication."""
    case = case or {}
    validate_reference(reference, case.get("id"))
    result: dict[str, Any] = {
        "schema_version": 1,
        "case_id": reference["case_id"],
        "reference_status": reference["status"],
        "inventory_complete": reference["inventory_complete"],
        "mapping": {},
        "declarations": [],
        "assertions": [],
        "questions": [],
        "extras": [],
        "discrepancies": [],
        "metrics": {
            "precision": None,
            "recall": None,
            "reason": "Predictions are provisional; inventory and matches require adjudication.",
        },
    }
    if measurement.get("status") != "ok":
        return {
            **result,
            "status": "measurement_failed",
            "measurement_status": measurement.get("status"),
        }
    graph = measurement["graph"]
    mapping, declarations = _declarations(reference, graph)
    for item in declarations:
        item["claim_id"] = "declaration:" + item["key"]
    assertions = [
        _assertion(item, graph, mapping, measurement.get("diagnostics", []))
        for item in reference["assertions"]
    ]
    _prevent_site_reuse(reference, assertions)
    questions = _questions(reference, graph, mapping, case.get("profile", "runtime"))
    extras = _extras(reference, graph, mapping, declarations, assertions, case)
    discrepancies = [
        item
        for item in [*declarations, *assertions, *questions]
        if item["status"] not in {"matched", "agree", "unknown"} or item["issues"]
    ]
    discrepancies.extend(extras)
    call_rows = [item for item in assertions if item["kind"] == "calls"]
    metrics: dict[str, Any] = result["metrics"]
    metrics.update(
        {
            "declarations": dict(Counter(item["status"] for item in declarations)),
            "assertions": dict(Counter(item["status"] for item in assertions)),
            "questions": dict(Counter(item["status"] for item in questions)),
            "call_sites": {
                "retained": sum(item["call_site_retained"] is True for item in call_rows),
                "missing": sum(item["call_site_retained"] is False for item in call_rows),
                "unscored": sum(item["call_site_retained"] is None for item in call_rows),
            },
            "targets": {
                "resolved": sum(item["target_resolved"] is True for item in call_rows),
                "unresolved": sum(item["target_resolved"] is False for item in call_rows),
                "unscored": sum(item["target_resolved"] is None for item in call_rows),
            },
        }
    )
    return {
        **result,
        "status": "compared",
        "mapping": mapping,
        "declarations": declarations,
        "assertions": assertions,
        "questions": questions,
        "extras": extras,
        "discrepancies": discrepancies,
        "query_answers": measurement.get("query_answers", []),
        "navigation_basis": "enriched_graph; public query results retained separately",
    }
