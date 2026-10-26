"""Measure a retained commerce evaluation without changing its evidence.

Run with: uv run python -m tests.commerce_smoke WORKSPACE OUTPUT --case saleor/shop
Native snapshots are explicit inputs, reported separately with --native.
Output must be outside WORKSPACE. This driver never starts applications.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from trailmark.query.api import QueryEngine

_REGISTRIES = {
    "oscar": ("django", "references/oscar/route-census.json"),
    "saleor": ("django", "references/saleor/route-census.json"),
    "sylius": ("symfony", "references/sylius-routes.json"),
    "magento": ("magento", "references/magento/http-routes.json"),
    "medusa": ("medusa", "references/medusa/native-route-registry.json"),
}


def measure(workspace: Path, output: Path, case: str, *, native: bool) -> dict[str, Any]:
    """Capture static and opt-in native results as different cohorts."""
    scope = json.loads((workspace / "results" / case / "scope.json").read_text())
    engine = QueryEngine.from_directory(scope["target"], "auto")
    inventory = engine.endpoint_inventory()
    records = inventory["registrations"]
    surface = engine.attack_surface()
    summary = engine.summary()
    http = [r for r in records if r["kind"] == "http"]
    operations = [r for r in records if r["kind"] == "graphql"]
    static_pairs = {
        (method, r["path"]) for r in http if r["path"] is not None for method in r["methods"]
    }
    result: dict[str, Any] = {
        "case": case,
        "root": scope["target"],
        "source_files": summary["source_files"],
        "source_file_counts": summary["source_file_counts"],
        "nodes": summary["total_nodes"],
        "parse_diagnostics": len(summary["parse_diagnostics"]),
        "parse_diagnostic_languages": dict(
            sorted(Counter(d["language"] for d in summary["parse_diagnostics"]).items())
        ),
        "source_policy_counts": [r["counts"] for r in summary["source_policy"]["roots"]],
        "generic_entrypoints": len(surface),
        "descriptions": dict(sorted(Counter(s["description"] for s in surface).items())),
        "static_registration_kinds": dict(sorted(Counter(r["kind"] for r in records).items())),
        "graphql_sdl_operations": sum(bool(r["declaration_nodes"]) for r in operations),
        "graphql_plugin_extension_operations": sum(not r["declaration_nodes"] for r in operations),
        "http_known_method_path_pairs": len(static_pairs),
        "graphql_operations": dict(
            sorted(
                Counter(
                    f"{r['namespace'] or 'unknown'}/{r['operation_type']}" for r in operations
                ).items()
            )
        ),
        "graphql_resolver_bindings": dict(
            sorted(Counter(b["status"] for r in operations for b in r["bindings"]).items())
        ),
        "graphql_operations_with_transport": sum(
            bool(r["transport_registrations"]) for r in operations
        ),
        "registration_diagnostic_reasons": dict(
            sorted(Counter(d["reason"] for d in inventory["diagnostics"]).items())
        ),
        "execution_facts": len(engine.execution_evidence()),
        "auth_classification": "unknown; no reviewed execution facts supplied",
        "limits": [
            "HTTP registrations, GraphQL operations and represented source handlers "
            "are separate units.",
            "Static source presence does not establish deployment or authentication.",
        ],
    }
    platform = case.split("/")[0]
    if native and platform in _REGISTRIES:
        framework, relative = _REGISTRIES[platform]
        mappings: tuple[tuple[str, str], ...] = ()
        if platform == "saleor":
            # This crosswalk was captured from the runtime, not authored as an
            # expected detector result. Verify its bytes before mapping paths.
            crosswalk = json.loads(
                (workspace / "references/saleor/source-crosswalk.json").read_text()
            )
            for item in crosswalk:
                actual = hashlib.sha256(Path(item["local_file"]).read_bytes()).hexdigest()
                if actual != item["runtime_sha256"] or actual != item["local_sha256"]:
                    raise ValueError("Saleor runtime/source crosswalk hash mismatch")
            mappings = (("/app", scope["target"]),)
        engine.import_native_registrations(
            str(workspace / relative), framework=framework, path_mappings=mappings
        )
        native_records = [
            r
            for r in engine.endpoint_inventory()["registrations"]
            if any(e["source"] == "native" for e in r["evidence"])
        ]
        native_pairs = {
            (m, r["path"]) for r in native_records if r["path"] is not None for m in r["methods"]
        }
        result["native"] = {
            "input": relative,
            "registrations": len(native_records),
            "represented_source_handlers": len(
                {
                    b["node_id"]
                    for r in native_records
                    for b in r["bindings"]
                    if b["status"] == "resolved"
                }
            ),
            "binding_status": dict(
                sorted(Counter(b["status"] for r in native_records for b in r["bindings"]).items())
            ),
            "known_method_path_pairs": len(native_pairs),
            "static_method_path_intersection": len(static_pairs & native_pairs),
            "limit": "Unresolved and out-of-root targets remain explicit; "
            "this is not automatic detector recall.",
        }
    if platform in {"saleor", "magento", "vendure"}:
        references = (
            [("unknown", "references/saleor/operations.json")]
            if platform == "saleor"
            else [("magento", "references/magento/graphql-root-operations.json")]
            if platform == "magento"
            else [
                ("shop-api", "references/vendure/shop-operations.json"),
                ("admin-api", "references/vendure/admin-operations.json"),
            ]
        )
        native_operations = set()
        for namespace, relative in references:
            for row in json.loads((workspace / relative).read_text()):
                role = row.get("root_type") or row["kind"].removesuffix("Type").title()
                native_operations.add((namespace, role, row["name"]))
        static_operations = {
            (r["namespace"] or "unknown", r["operation_type"], r["operation_name"])
            for r in operations
        }
        result["graphql_native_comparison"] = {
            "native_operations": len(native_operations),
            "static_identity_intersection": len(static_operations & native_operations),
            "native_missing_from_selected_roots": sorted(native_operations - static_operations),
            "static_extra_identities": sorted(static_operations - native_operations),
        }
    output.mkdir(parents=True, exist_ok=True)
    (output / (case.replace("/", "-") + ".json")).write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--case", required=True)
    parser.add_argument("--native", action="store_true")
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.workspace.resolve()):
        parser.error("Output must be outside the immutable evidence workspace")
    print(json.dumps(measure(args.workspace, args.output, args.case, native=args.native), indent=2))


if __name__ == "__main__":
    main()
