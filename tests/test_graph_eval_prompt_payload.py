"""Scope and transport controls for reviewable judge evidence, without model calls."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.graph_eval import campaign
from tests.graph_eval.prompt_payload import (
    ENCODING_VERSION,
    PROJECTION_VERSION,
    compact_json,
    decode_payload,
    encode_payload,
    payload_digest,
    scope_evidence,
)
from tests.graph_eval.prompts import build_adjudication_prompt, reference_prompt
from tests.graph_eval.schema import ADJUDICATION_SCHEMA, REFERENCE_SCHEMA, adjudication_schema
from tests.graph_eval.util import digest, file_digest, read_json, write_json


def _payload() -> dict[str, Any]:
    diagnostics = [
        {
            "message": "overlap",
            "location": {"file_path": "main.py", "start_line": 8, "end_line": 11},
        },
        {
            "message": "outside",
            "location": {"file_path": "other.py", "start_line": 1, "end_line": 1},
        },
        {"message": "unknown location"},
        {
            "message": "later",
            "location": {"file_path": "main.py", "start_line": 30, "end_line": 30},
        },
        {"message": "context", "location": {"file_path": "ctx.py", "start_line": 3, "end_line": 3}},
    ]
    entries = [{"node_id": "main:run"}, {"node_id": "other:target"}, {"node_id": "unrelated:f"}, {}]
    queries = [
        {
            "operation": "summary",
            "answer": {"total_nodes": 10000, "parse_diagnostics": diagnostics},
        },
        {"operation": "attack_surface", "answer": entries},
        {"operation": "call_resolution", "answer": {"call_sites": 2000}},
    ]
    row = {"claim_id": "reader-A:assertion:call-1", "status": "discrepancy", "evidence": "x" * 150}
    comparison = {
        "query_answers": queries,
        "mapping": {
            "file:main.py": "main",
            "file:ctx.py": "ctx",
            "file:mentioned.py": "mentioned",
            "file:unrelated.py": "unrelated",
            "decl-key": "main:run",
        },
        "assertions": [row, {"claim_id": "reader-A:assertion:agree", "status": "agree"}],
        "discrepancies": [row],
    }
    return {
        "case": {
            "id": "case",
            "primary": [{"path": "main.py", "start_line": 10, "end_line": 20}],
            "context": [{"path": "ctx.py", "start_line": 1, "end_line": 5}],
        },
        "reader_a": {"questions": [{"subject": "file:mentioned.py"}], "notes": ["source: café"]},
        "reader_b": {"notes": ["literal source text " + "x" * 200]},
        "measurement": {
            "graph": {
                "nodes": {"main:run": {"code": "source body"}, "other:target": {}},
                "edges": [],
            },
            "query_answers": queries,
            "public_navigation": {"answers": queries, "domain": ["unrelated", "main", "main"]},
        },
        "comparisons": [comparison, copy.deepcopy(comparison)],
    }


def test_case_excerpt_preserves_reader_answers_and_declares_only_global_exclusions() -> None:
    original = _payload()
    snapshot = copy.deepcopy(original)
    result = scope_evidence(original)
    assert original == snapshot
    for key in ("reader_a", "reader_b"):
        assert result[key] == original[key]
    for key in ("graph", "public_navigation"):
        assert result["measurement"][key] == original["measurement"][key]
    queries = result["measurement"]["query_answers"]
    assert queries[0]["answer"]["total_nodes"] == 10000
    assert [item["message"] for item in queries[0]["answer"]["parse_diagnostics"]] == [
        "overlap",
        "unknown location",
        "context",
    ]
    assert queries[1]["answer"] == [{"node_id": "main:run"}, {"node_id": "other:target"}, {}]
    assert queries[2] == original["measurement"]["query_answers"][2]
    notice = queries[0]["prompt_projection"]
    assert notice["original_response_sha256"] == payload_digest(
        snapshot["measurement"]["query_answers"][0]
    )
    assert (notice["original_count"], notice["retained_count"], notice["excluded_count"]) == (
        5,
        3,
        2,
    )
    excluded = [
        snapshot["measurement"]["query_answers"][0]["answer"]["parse_diagnostics"][i]
        for i in (1, 3)
    ]
    assert notice["excluded_sha256"] == payload_digest(excluded)
    assert result["prompt_projection"]["version"] == PROJECTION_VERSION
    assert len(result["prompt_projection"]["query_excerpts"]) == 6
    for comparison in result["comparisons"]:
        assert comparison["query_answers"] == queries
        assert set(comparison["mapping"]) == {
            "file:main.py",
            "file:ctx.py",
            "file:mentioned.py",
            "decl-key",
        }


def test_tables_preserve_per_site_edges_domains_and_claims_without_name_collapse() -> None:
    payload = _payload()
    edge: dict[str, Any] = {
        "source": "main:run",
        "target": "other:target",
        "kind": "calls",
        "location": {"file_path": "main.py", "start_line": 11, "start_col": 2},
        "confidence": "uncertain",
        "attributes": {"raw_target": "obj.call", "deferred": True},
    }
    second = copy.deepcopy(edge)
    second["location"]["start_line"] = 12
    payload["measurement"]["graph"]["edges"] = [edge, second, edge]
    domain = [f"module-{i}:function" for i in range(30)] + ["module-0:function"]
    payload["measurement"]["public_navigation"] = {
        "plans": [{"domain": domain}],
        "records": [{"domain": domain}],
        "answers": [edge, second],
    }
    for comparison in payload["comparisons"]:
        for row in [*comparison["assertions"], *comparison["discrepancies"]]:
            row["candidates"] = [edge, second, edge]
    encoded = encode_payload(payload)
    decoded = decode_payload(json.loads(compact_json(encoded)))
    assert decoded == payload
    assert len(encoded["catalog"]["queries"]) == 1
    assert len(encoded["catalog"]["edges"]) == 2
    assert encoded["catalog"]["rows"] and encoded["catalog"]["lists"]
    assert decoded["measurement"]["graph"]["edges"] == [edge, second, edge]
    assert decoded["measurement"]["public_navigation"]["records"][0]["domain"] == domain
    assert encode_payload(dict(reversed(list(payload.items())))) == encoded
    assert len(compact_json(encoded)) < len(compact_json(payload))


@pytest.mark.parametrize(("start", "end"), [(0, 0), (True, True), (15, 12), (None, None)])
def test_invalid_diagnostic_ranges_are_retained_as_unknown(start: Any, end: Any) -> None:
    payload = _payload()
    diagnostic = {
        "message": "invalid/unknown range",
        "location": {"file_path": "main.py", "start_line": start, "end_line": end},
    }
    payload["measurement"]["query_answers"][0]["answer"]["parse_diagnostics"] = [diagnostic]
    scoped = scope_evidence(payload)
    assert scoped["measurement"]["query_answers"][0]["answer"]["parse_diagnostics"] == [diagnostic]


@pytest.mark.parametrize("tag", ["$edge", "$row", "$list", "$query", "$literal"])
def test_reference_shaped_target_objects_are_literal_and_collision_safe(tag: str) -> None:
    payload = _payload()
    source_data = {tag: 0, "nested": [{tag: 0}, {"$literal": [["$edge", 12]]}]}
    payload["reader_a"]["target_data"] = source_data
    payload["measurement"]["graph"]["nodes"]["main:run"]["attributes"] = source_data
    assert decode_payload(encode_payload(payload)) == payload


_json_data = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(max_size=30),
    lambda children: (
        st.lists(children, max_size=4)
        | st.dictionaries(
            st.sampled_from(["$edge", "$row", "$literal", "source", "plain"]), children, max_size=4
        )
    ),
    max_leaves=15,
)


@given(_json_data)
@settings(max_examples=40, deadline=None)
def test_arbitrary_nested_target_json_roundtrips(data: Any) -> None:
    payload = _payload()
    payload["reader_a"]["arbitrary_target_json"] = data
    assert decode_payload(json.loads(compact_json(encode_payload(payload)))) == payload


@pytest.mark.parametrize("index", [-1, True, "0", 9999])
def test_decoder_rejects_invalid_reference_indices(index: Any) -> None:
    encoded = encode_payload(_payload())
    encoded["payload"] = {"$edge": index}
    with pytest.raises(ValueError, match="reference index"):
        decode_payload(encoded)


def test_decoder_rejects_cycles_and_changed_values() -> None:
    encoded = encode_payload(_payload())
    encoded["catalog"]["rows"] = [{"$row": 0}]
    encoded["payload"] = {"$row": 0}
    with pytest.raises(ValueError, match="Cyclic"):
        decode_payload(encoded)
    encoded = encode_payload(_payload())
    encoded["payload"]["case"]["id"] = "tampered"
    with pytest.raises(ValueError, match="hash differs"):
        decode_payload(encoded)


def test_prompt_requires_exact_discrepancy_ids_and_saves_frozen_provenance(tmp_path: Path) -> None:
    data = _payload()
    built = build_adjudication_prompt(
        data["case"], data["reader_a"], data["reader_b"], data["measurement"], data["comparisons"]
    )
    ids = ["reader-A:assertion:call-1"]
    assert built.logical_payload["required_claim_ids"] == ids
    assert "EXACTLY the IDs" in built.text and "agree/blocked" in built.text
    assert built.provenance["encoding_version"] == ENCODING_VERSION
    assert decode_payload(built.presentation) == built.logical_payload
    schema = adjudication_schema(ids)
    prompt = built.text + "\ncontract suffix"
    campaign._save_judge_prompt(tmp_path, built, prompt, schema)
    saved = read_json(tmp_path / "judge/prompt-provenance.json")
    assert saved["logical_payload_sha256"] == payload_digest(built.logical_payload)
    assert saved["presentation_sha256"] == payload_digest(built.presentation)
    assert saved["full_prompt_sha256"] == hashlib.sha256(prompt.encode()).hexdigest()
    assert saved["output_schema_sha256"] == digest(schema)
    campaign._save_judge_prompt(tmp_path, built, prompt, schema)
    with pytest.raises(ValueError, match="Frozen judge presentation changed"):
        campaign._save_judge_prompt(tmp_path, built, prompt + "changed", schema)
    assert "catalog" not in reference_prompt(data["case"])


def test_codec_implementation_is_part_of_campaign_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    module_root = Path(campaign.__file__).parent
    manifest = {
        "worker_fingerprint": campaign.worker_fingerprint(),
        "reference_schema_sha256": digest(REFERENCE_SCHEMA),
        "adjudication_schema_sha256": digest(ADJUDICATION_SCHEMA),
        "prompt_sha256": file_digest(module_root / "prompts.py"),
        "prompt_payload_sha256": file_digest(module_root / "prompt_payload.py"),
        "judge_encoding": ENCODING_VERSION,
        "judge_projection": PROJECTION_VERSION,
        "tool_runtime": {},
    }
    monkeypatch.setattr(campaign, "_check_tool_source", lambda _runtime: module_root)
    campaign._validate_protocol(manifest)
    manifest["prompt_payload_sha256"] = "changed"
    with pytest.raises(ValueError, match="prompt_payload_sha256"):
        campaign._validate_protocol(manifest)


def test_judge_launch_uses_exact_required_ids_in_schema_prompt_and_saved_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.graph_eval import compare

    data = _payload()
    case = {**data["case"], "project": "project"}
    readers = []
    for label in ("A", "B"):
        path = tmp_path / f"reader-{label}.json"
        write_json(path, {"reader": label})
        readers.append({"status": "ok", "output_path": str(path)})
    navigation = {
        "answers": [{"id": "unmodified", "answer": ["out-of-scope"]}],
        "records": [
            {"claim_id": "public-query-A:x", "status": "discrepancy"},
            {"claim_id": "public-query-B:agree", "status": "agree"},
        ],
    }
    row = {"adjudication": None, "readers": readers, "public_navigation": navigation}
    measurement = {**data["measurement"], "status": "ok"}
    monkeypatch.setattr(campaign, "case_result", lambda *_args: row)
    monkeypatch.setattr(campaign, "verify_reference_states", lambda *_args: [])
    monkeypatch.setattr(campaign, "verify_measurement", lambda *_args: measurement)
    monkeypatch.setattr(campaign, "_comparison_seal", lambda *_args: {"sealed": "test"})
    monkeypatch.setattr(
        compare,
        "compare_reference",
        lambda *_args: {
            "assertions": [{"claim_id": "assertion:agree", "status": "agree"}],
            "discrepancies": [{"claim_id": "assertion:missing", "status": "discrepancy"}],
            "query_answers": data["measurement"]["query_answers"],
        },
    )
    captured = []

    def launch(
        _campaign: Path,
        _manifest: dict[str, Any],
        _case: dict[str, Any],
        _role: str,
        prompt: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        captured.append((prompt, schema))
        return {"status": "input_too_large"}

    monkeypatch.setattr(campaign, "_agent_job", launch)
    campaign.run_judges(tmp_path, {"contract": {}}, [case], workers=1)
    assert len(captured) == 1
    prompt, schema = captured[0]
    encoded, _ = json.JSONDecoder().raw_decode(prompt, prompt.index("{"))
    logical = decode_payload(encoded)
    required = ["public-query-A:x", "reader-A:assertion:missing", "reader-B:assertion:missing"]
    assert logical["required_claim_ids"] == required
    decisions = schema["properties"]["decisions"]
    assert decisions["minItems"] == decisions["maxItems"] == 3
    assert decisions["items"]["properties"]["claim_id"]["enum"] == required
    assert logical["measurement"]["public_navigation"] == navigation
    assert read_json(tmp_path / "cases/case/judge/prompt-evidence.json") == logical
