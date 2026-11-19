"""Translate frozen questions into bounded checks of actual public query answers."""

from __future__ import annotations

from typing import Any


def plan_queries(
    reference: dict[str, Any], comparison: dict[str, Any], *, prefix: str, profile: str
) -> dict[str, Any]:
    mapping = comparison["mapping"]
    requests, records = [], []
    for question in reference["questions"]:
        ident = prefix + question["id"]
        operation = question["operation"]
        record: dict[str, Any] = {
            "id": ident,
            "claim_id": prefix + "public-question:" + question["id"],
            "question": question["question"],
            "operation": operation,
            "expected": [mapping.get(key) for key in question["expected"]],
            "domain": sorted(set(mapping.values())),
            "status": "pending",
            "inventory_complete": reference["inventory_complete"],
        }
        keys = [question["subject"], *question["expected"]]
        if question["target"] is not None:
            keys.append(question["target"])
        if any(key not in mapping for key in keys):
            record.update(status="blocked", reason="Unmatched source declaration")
        elif operation in {"dependencies", "ownership"}:
            record.update(
                status="unsupported", reason="No equivalent direct public QueryEngine method"
            )
        elif not question["expected"] and not reference["inventory_complete"]:
            record.update(status="unknown", reason="Incomplete inventory cannot establish absence")
        elif not question["expected"] and any(
            declaration["key"] not in mapping for declaration in reference.get("declarations", [])
        ):
            record.update(
                status="blocked", reason="Incomplete declaration alignment cannot establish absence"
            )
        elif operation == "path" and len(question["expected"]) > 21:
            record.update(
                status="not_evaluated", reason="Expected path exceeds evaluator's 20-edge bound"
            )
        else:
            subject = mapping[question["subject"]]
            if operation in {"callees", "callers"}:
                request = {
                    "id": ident,
                    "operation": operation + "_of",
                    "arguments": {"name": subject},
                }
                record["semantics"] = (
                    "Direct calls only; specification/type dependencies are separate"
                )
            elif operation == "path" and question["target"] is not None:
                kinds = (
                    ["calls"]
                    if profile == "runtime"
                    else ["calls", "references", "type_uses", "corresponds_to"]
                )
                request = {
                    "id": ident,
                    "operation": "paths_between",
                    "arguments": {
                        "src": subject,
                        "dst": mapping[question["target"]],
                        "max_depth": max(1, len(question["expected"]) - 1)
                        if question["expected"]
                        else 20,
                        "edge_kinds": kinds,
                    },
                }
                record["semantics"] = (
                    "Public structural paths; positive search uses expected length (cap 20), "
                    "negative search uses 20 edges and the mapped source scope"
                )
            else:
                record.update(status="unknown", reason="Path has no declared target")
                records.append(record)
                continue
            requests.append(request)
        records.append(record)
    return {"requests": requests, "records": records}


def score_answers(plan: dict[str, Any], answers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compare only mapped in-scope declarations; preserve full raw API answers separately."""
    by_id = {answer["id"]: answer for answer in answers}
    records = []
    for original in plan["records"]:
        record = dict(original)
        if record["status"] != "pending":
            records.append(record)
            continue
        answer = by_id.get(record["id"])
        if answer is None or answer.get("status") != "ok":
            record.update(status="error", answer=answer)
        elif record["operation"] == "path":
            paths = answer["answer"]
            expected = record["expected"]
            in_scope = [path for path in paths if set(path).issubset(record["domain"])]
            okay = expected in paths if expected else not in_scope
            record.update(status="agree" if okay else "discrepancy", observed=paths)
        else:
            observed = sorted(
                {item["id"] for item in answer["answer"] if item["id"] in record["domain"]}
            )
            expected = set(record["expected"])
            # A partial inventory supports positive expectations only.
            okay = (
                set(observed) == expected
                if record["inventory_complete"]
                else expected.issubset(observed)
            )
            record.update(status="agree" if okay else "discrepancy", observed=observed)
        records.append(record)
    return records
