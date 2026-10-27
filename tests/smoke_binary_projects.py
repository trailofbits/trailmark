"""Reproduce pinned arm64 library smoke checks with independent metadata oracles.

Run as ``python -m tests.smoke_binary_projects --help`` from the checkout.
Builds live outside the repository; this script never downloads or executes them.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import subprocess
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

from tests.binary_oracle import aarch64_branches, aarch64_direct_calls, inspect_image, merged_ranges
from trailmark.models import BinaryImportOptions, NodeKind, NodeOrigin
from trailmark.models.edges import EdgeKind
from trailmark.query.api import QueryEngine

# Source counts include inactive/platform-specific code. They are regression
# floors for these exact revisions, not a build-specific recall denominator.
PROJECTS = {
    "libsodium": (
        "src/libsodium",
        "c",
        ("sodium_init", "crypto_generichash_blake2b", "crypto_sign_ed25519_detached"),
        "a786c1a045db4ceaf8ad944539abbea071ff145f",
        1585,
        1344,
    ),
    "monocypher": (
        "src",
        "c",
        ("crypto_blake2b", "crypto_x25519", "crypto_aead_lock"),
        "f5f0a19a5dc6c14a49adb3cd63ea79a60f6ba77b",
        149,
        145,
    ),
    "zeromq": (
        "src",
        "cpp",
        ("zmq_send", "zmq_recv", "zmq_socket"),
        "46493370217ac135246617fa2f6ac819d8b61bfc",
        1875,
        1413,
    ),
}


def smoke(args: argparse.Namespace) -> dict[str, Any]:
    source, language, names, expected_revision, source_floor, match_floor = PROJECTS[args.project]
    repository = args.workspace / args.project
    artifact_path = args.artifact.resolve()
    git = shutil.which("git")
    assert git is not None
    revision = subprocess.run(  # noqa: S603 -- Read the explicitly selected local checkout.
        [git, "rev-parse", "HEAD"], cwd=repository, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert revision == expected_revision, (
        "Update the smoke expectations deliberately for a new revision"
    )
    oracle = inspect_image(artifact_path, args.readobj, args.nm)
    original_symbols = {v for values in oracle.symbols.values() for v in values}
    if args.reference_image:
        reference = inspect_image(args.reference_image, args.readobj, args.nm)
        assert reference.image_base == oracle.image_base
        # Stripping removes the independent function-boundary oracle. Reuse
        # symbols only after checking every executable byte against the build
        # before stripping; all address conversions still use the target file.
        data, reference_data = artifact_path.read_bytes(), args.reference_image.read_bytes()
        assert {(s.name, s.start, s.end) for s in oracle.sections} == {
            (s.name, s.start, s.end) for s in reference.sections
        }
        for section in oracle.sections:
            offset = reference.offset(section.start)
            size = section.end - section.start
            assert (
                data[section.offset : section.offset + size]
                == reference_data[offset : offset + size]
            )
        oracle = replace(oracle, symbols=reference.symbols)
    started = time.monotonic()
    engine = QueryEngine.from_directory(
        str(repository / source), language=language, detect_entrypoints_=False
    )
    graph = engine._store._graph
    selected = {}
    for name in names:
        candidates = [
            n
            for n in graph.nodes.values()
            if n.origin == NodeOrigin.SOURCE
            and n.kind in (NodeKind.FUNCTION, NodeKind.METHOD)
            and n.name == name
        ]
        (node,) = candidates
        selected[name] = node.id
    baseline_calls = {
        node_id: {n["id"] for n in engine.callees_of(node_id)} for node_id in selected.values()
    }
    source_summary = engine.summary()
    source_count = sum(
        n.origin == NodeOrigin.SOURCE and n.kind in (NodeKind.FUNCTION, NodeKind.METHOD)
        for n in graph.nodes.values()
    )
    assert source_count >= source_floor
    options = BinaryImportOptions(
        backend=args.backend,
        objdump=args.objdump,
        symbolizer=args.symbolizer,
        debug_file=str(args.debug_file) if args.debug_file else None,
    )
    import_start = time.monotonic()
    report = engine.augment_binary_file(str(artifact_path), options=options)
    import_seconds = time.monotonic() - import_start
    (artifact,) = engine.binary_artifacts()
    assert artifact["architecture"] == "aarch64", "These raw-call oracles cover arm64 smoke builds"
    assert artifact["image_base"] == oracle.image_base
    expected_scope = merged_ranges([(s.start, s.end) for s in oracle.sections])
    assert merged_ranges(artifact["scanned_ranges"]) == expected_scope
    graph = engine._store._graph
    assert merged_ranges([(r.start, r.end) for r in graph.binary_ranges.values()]) == expected_scope
    assert {d["code"] for d in report["diagnostics"]} <= {"function_boundaries_unknown"}
    matched = {m.source_node_id for m in graph.binary_source_mappings.values() if m.source_node_id}
    assert len(matched) >= match_floor
    calls = [
        e for e in graph.edges if e.kind == EdgeKind.CALLS and "artifact" in dict(e.attributes)
    ]
    tails = [e for e in calls if dict(e.attributes).get("kind") == "tail"]
    branch_oracle = set().union(
        *(aarch64_branches(artifact_path, oracle, s.start, s.end) for s in oracle.sections)
    )
    assert all(
        (dict(e.attributes)["callsite"], dict(e.attributes)["target_address"]) in branch_oracle
        for e in tails
    )
    # Prefer a debug-only static owner in stripped artifacts, exercising the
    # reconstruction path that a fresh symbol-table-only load cannot supply.
    tail_probe = next(
        (
            e
            for e in tails
            if dict(graph.nodes[e.source_id].attributes).get("address") not in original_symbols
        ),
        tails[0] if tails else None,
    )
    if tail_probe:
        site = dict(tail_probe.attributes)["callsite"]
        assert isinstance(site, int)
        result = engine.disassemble_binary(artifact["id"], site, site + 4, max_instructions=1)
        (instruction,) = result["instructions"]
        assert (
            instruction["call_kind"] == "tail"
            and instruction["target"] == dict(tail_probe.attributes)["target_address"]
        )
    checks = []
    data = artifact_path.read_bytes()
    for name, source_id in selected.items():
        rows = engine.binary_ranges(source_id)
        assert rows
        symbol = ("_" if artifact["format"] == "Mach-O" else "") + name
        (address,) = oracle.symbols[symbol]
        owners = {
            r["node_id"]
            for r in rows
            if dict(graph.nodes[r["node_id"]].attributes).get("address") == address
        }
        (owner_id,) = owners
        native_ranges = engine.binary_ranges(owner_id)
        assert min(r["start"] for r in native_ranges) == address
        end = max(r["end"] for r in native_ranges)
        # nm independently supplies the following function symbol. The graph
        # may exclude alignment bytes; neither scope may contain extra BLs.
        (section,) = (s for s in oracle.sections if s.start <= address < s.end)
        next_symbol = min(
            (v for values in oracle.symbols.values() for v in values if address < v <= section.end),
            default=section.end,
        )
        expected_calls = aarch64_direct_calls(artifact_path, oracle, address, next_symbol)
        actual_calls: set[tuple[int, int]] = set()
        for edge in calls:
            attributes = dict(edge.attributes)
            if edge.source_id == owner_id and attributes.get("kind") == "direct":
                assert graph.nodes[edge.target_id].kind == NodeKind.FUNCTION, (name, edge)
                site, target = attributes["callsite"], attributes["target_address"]
                assert isinstance(site, int) and isinstance(target, int)
                actual_calls.add((site, target))
        assert actual_calls == expected_calls, (name, actual_calls ^ expected_calls)
        assert aarch64_direct_calls(artifact_path, oracle, address, end) == expected_calls
        symbol_entries = {v for values in oracle.symbols.values() for v in values}
        expected_tails = {
            (site, target)
            for site, target in branch_oracle
            if address <= site < next_symbol and target in symbol_entries and target != address
        }
        actual_tails = {
            (dict(e.attributes)["callsite"], dict(e.attributes)["target_address"])
            for e in tails
            if e.source_id == owner_id and dict(e.attributes)["target_address"] in symbol_entries
        }
        assert actual_tails == expected_tails, (name, actual_tails ^ expected_tails)
        for row in rows:
            assert any(m["source_node_id"] == source_id for m in row["source_mappings"])
            assembly = engine.disassemble_binary(
                row["artifact_id"], row["start"], row["end"], max_instructions=8
            )
            assert assembly["instructions"] and len(assembly["instructions"]) <= 8
            assert {d["code"] for d in assembly["diagnostics"]} <= {"instruction_limit"}
            at = engine.source_at_address(row["artifact_id"], row["start"])
            assert any(m["source_node_id"] == source_id for m in at)
            assert at == engine.source_at_address(
                row["artifact_id"], row["start"] - oracle.image_base, address_kind="rva"
            )
            assert at == engine.source_at_address(
                row["artifact_id"], oracle.offset(row["start"]), address_kind="file_offset"
            )
            for mapping in at:
                assert mapping["id"] not in {
                    m["id"] for m in engine.source_at_address(row["artifact_id"], mapping["end"])
                }
            for instruction in assembly["instructions"]:
                offset = oracle.offset(instruction["address"])
                assert instruction["file_offset"] == offset
                assert data[offset : offset + instruction["size"]].hex() == instruction["bytes"]
        assert {n["id"] for n in engine.callees_of(source_id)} == baseline_calls[source_id]
        if expected_calls:
            assert any(n.get("origin") == "binary" for n in engine.callees_of(owner_id))
        checks.append(
            {
                "function": name,
                "source_node_id": source_id,
                "binary_node_id": owner_id,
                "nm_address": address,
                "ranges_checked": len(rows),
                "direct_call_oracle": sorted(expected_calls),
                "tail_transfer_oracle": sorted(expected_tails),
            }
        )
    before = engine.to_json()
    engine.augment_binary_file(str(artifact_path), options=options)
    assert engine.to_json() == before
    if args.compare_graph:
        opener = gzip.open if args.compare_graph.suffix == ".gz" else Path.open
        with opener(args.compare_graph, "rt") as stream:
            assert json.load(stream) == json.loads(before)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.output_dir / f"{args.label}.graph.json.gz", "wt") as stream:
        stream.write(before)
    sites = [e for e in calls if "callsite" in dict(e.attributes)]
    result = {
        "case": args.label,
        "project": args.project,
        "revision": revision,
        "sha256": hashlib.sha256(data).hexdigest(),
        "artifact_path": str(artifact_path),
        "symbol_reference": str(args.reference_image) if args.reference_image else None,
        "source_functions": source_count,
        "matched_source_functions": len(matched),
        "binary_functions": sum(
            n.kind == NodeKind.FUNCTION and n.origin == NodeOrigin.BINARY
            for n in graph.nodes.values()
        ),
        "resolved_function_calls": sum(
            graph.nodes[e.target_id].kind == NodeKind.FUNCTION
            and graph.nodes[e.target_id].origin == NodeOrigin.BINARY
            for e in sites
        ),
        "calls_to_import_entries": sum(
            bool(dict(graph.nodes[e.target_id].attributes).get("import_symbol")) for e in sites
        ),
        "calls_to_local_functions": sum(
            graph.nodes[e.target_id].kind == NodeKind.FUNCTION
            and graph.nodes[e.target_id].origin == NodeOrigin.BINARY
            and not dict(graph.nodes[e.target_id].attributes).get("import_symbol")
            for e in sites
        ),
        "tail_encoding_oracles_checked": len(tails),
        "tail_query_checked": tail_probe is not None,
        "region_calls": sum(graph.nodes[e.target_id].kind == NodeKind.BINARY_REGION for e in sites),
        "uncertain_calls": sum(e.confidence.value == "uncertain" for e in sites),
        "tail_calls": sum(dict(e.attributes).get("kind") == "tail" for e in sites),
        "linkage_edges": len(calls) - len(sites),
        "checks": checks,
        "mapping_methods": dict(Counter(m.method for m in graph.binary_source_mappings.values())),
        "inline_mappings": sum(m.inline_depth > 0 for m in graph.binary_source_mappings.values()),
        "source_diagnostics": len(source_summary["parse_diagnostics"]),
        "report": report,
        "artifact": artifact,
        "oracle_sections": [vars(s) for s in oracle.sections],
        "oracle_executable_bytes": sum(b - a for a, b in expected_scope),
        "import_seconds": round(import_seconds, 3),
        "total_seconds": round(time.monotonic() - started, 3),
        "passed": True,
        "reimport_equal": True,
        "cross_process_equal": bool(args.compare_graph),
    }
    (args.output_dir / f"{args.label}.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", choices=PROJECTS)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument(
        "--reference-image", type=Path, help="Unstripped symbol oracle; executable bytes must match"
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("--backend", choices=("llvm", "gnu"), default="llvm")
    for tool in ("objdump", "symbolizer", "readobj", "nm"):
        parser.add_argument(f"--{tool}", required=True)
    parser.add_argument("--debug-file", type=Path)
    parser.add_argument("--compare-graph", type=Path)
    result = smoke(parser.parse_args())
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "case",
                    "passed",
                    "matched_source_functions",
                    "resolved_function_calls",
                    "tail_calls",
                    "region_calls",
                    "total_seconds",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
