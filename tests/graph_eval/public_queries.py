"""Run real public navigation queries against a repeat of the sealed graph."""

from __future__ import annotations

import concurrent.futures
from pathlib import Path
from typing import Any

from tests.graph_eval.compare import compare_reference
from tests.graph_eval.measure import digest as graph_digest
from tests.graph_eval.measure import query_project
from tests.graph_eval.navigation import plan_queries, score_answers
from tests.graph_eval.util import digest, file_digest, read_json, source_inventory, write_json


def _verify_batch(destination: Path) -> dict[str, Any]:
    seal = read_json(destination / "seal.json")
    for name, expected in seal["files"].items():
        if file_digest(destination / name) != expected:
            raise ValueError(f"Frozen public query evidence changed: {destination / name}")
    return read_json(destination / "observation.json")


def verify_public_navigation(campaign: Path, case: dict[str, Any]) -> dict[str, Any]:
    """Verify query evidence and its original reference/measurement dependencies."""
    from tests.graph_eval.campaign import verify_measurement, verify_reference_states

    verify_reference_states(campaign, case)
    verify_measurement(campaign, case["project"])
    directory = campaign / "cases" / case["id"]
    seal = read_json(directory / "public-navigation-seal.json")
    expected = {
        "artifact_sha256": file_digest(directory / "public-navigation.json"),
        "reference_seal_sha256": file_digest(directory / "reference-seal.json"),
        "measurement_seal_sha256": file_digest(
            campaign / "measurements" / case["project"] / "seal.json"
        ),
        "batch_seal_sha256": file_digest(Path(seal["batch_directory"]) / "seal.json"),
        "batch_directory": seal["batch_directory"],
    }
    if seal != expected:
        raise ValueError(f"Frozen public navigation changed: {case['id']}")
    _verify_batch(Path(seal["batch_directory"]))
    return read_json(directory / "public-navigation.json")


def run_navigation(
    campaign: Path, manifest: dict[str, Any], selectedcases: list[dict[str, Any]], workers: int
) -> None:
    """Batch both readers' questions per project, keeping unsupported uses visible.

    Every selected project includes all its preregistered cases. This gives
    filtered and sharded invocations the same query identity and avoids competing
    writes or duplicate reparses when sibling cases belong to different shards.
    """
    from tests.graph_eval.campaign import (
        _artifact_lock,
        verify_measurement,
        verify_reference_states,
    )

    if workers < 1:
        raise ValueError("Workers must be positive")
    campaign = campaign.resolve()
    projects = {project["id"]: project for project in manifest["projects"]}

    def run_project(project_id: str) -> None:
        destination = campaign / "public-queries" / project_id
        with _artifact_lock(destination):
            project = projects[project_id]
            root = Path(project["roots"]["measurement-sources"])
            if digest(source_inventory(root)) != project["source_hash"]:
                raise ValueError(f"Public query source bytes changed: {project_id}")
            original = verify_measurement(campaign, project_id)
            cases = sorted(
                (case for case in manifest["cases"] if case["project"] == project_id),
                key=lambda case: case["id"],
            )
            plans: dict[str, dict[str, Any]] = {}
            readers: dict[str, dict[str, str]] = {}
            requests = []
            for case in cases:
                case_id = case["id"]
                plans[case_id], readers[case_id] = {}, {}
                states = verify_reference_states(campaign, case)
                for role, state in zip(("reader-A", "reader-B"), states, strict=True):
                    readers[case_id][role] = state["status"]
                    if state["status"] != "ok":
                        continue
                    reference = read_json(Path(state["output_path"]))
                    comparison = compare_reference(reference, original, case)
                    plan = plan_queries(
                        reference,
                        {"mapping": comparison.get("mapping", {})},
                        prefix=role + ":",
                        profile=case["profile"],
                    )
                    plans[case_id][role] = plan
                    requests.extend(
                        {**request, "id": case_id + "/" + request["id"]}
                        for request in plan["requests"]
                    )
            inputs = {
                "measurement_seal_sha256": file_digest(
                    campaign / "measurements" / project_id / "seal.json"
                ),
                "references": {
                    case["id"]: file_digest(campaign / "cases" / case["id"] / "reference-seal.json")
                    for case in cases
                },
                "case_inputs": {case["id"]: case["input_hash"] for case in cases},
                "plans": plans,
                "requests": requests,
                "tool_python": manifest["tool_python"],
                "implementation": {
                    name: file_digest(Path(__file__).with_name(name))
                    for name in ("public_queries.py", "navigation.py", "measure.py", "compare.py")
                },
            }
            identity = digest(inputs)
            batch = destination / identity
            batch.mkdir(parents=True, exist_ok=True)
            observation: dict[str, Any]
            if (batch / "seal.json").exists():
                observation = _verify_batch(batch)
            else:
                write_json(batch / "inputs.json", inputs)
                write_json(batch / "requests.json", requests)
                if original["status"] == "ok" and requests:
                    result = query_project(
                        root,
                        project["language"],
                        batch / "worker",
                        requests,
                        expected_graph_hash=graph_digest(original["graph"]),
                        python=Path(manifest["tool_python"]),
                        timeout=manifest["limits"]["measurement_timeout"],
                    )
                    observation = {
                        "status": result["status"],
                        "query_baseline": result.get("query_baseline"),
                        "query_answers": result.get("query_answers", []),
                        "worker_measurement": str(batch / "worker" / "measurement.json"),
                    }
                else:
                    observation = {
                        "status": "not_requested"
                        if original["status"] == "ok"
                        else "measurement_failed",
                        "query_answers": [],
                        "query_baseline": None,
                    }
                write_json(batch / "observation.json", observation)
                files = ["inputs.json", "requests.json", "observation.json"]
                if observation.get("worker_measurement"):
                    files.append("worker/measurement.json")
                write_json(
                    batch / "seal.json",
                    {
                        "input_hash": identity,
                        "files": {name: file_digest(batch / name) for name in files},
                    },
                )
            for case in cases:
                case_id = case["id"]
                directory = campaign / "cases" / case_id
                prefix = case_id + "/"
                answers = [
                    {**answer, "id": answer["id"].removeprefix(prefix)}
                    for answer in observation["query_answers"]
                    if answer["id"].startswith(prefix)
                ]
                # Raw answers remain visible even when the repeated graph is invalid.
                usable = answers if observation["status"] == "ok" else []
                records = [
                    record
                    for plan in plans[case_id].values()
                    for record in score_answers(plan, usable)
                ]
                evidence = {
                    "schema_version": 1,
                    "case_id": case_id,
                    "project": project_id,
                    "input_hash": identity,
                    "status": "completed"
                    if observation["status"] in {"ok", "not_requested"}
                    else "query_failed",
                    "measurement_status": original["status"],
                    "reader_statuses": readers[case_id],
                    "plans": plans[case_id],
                    "answers": answers,
                    "records": records,
                    "query_run": {
                        "status": observation["status"],
                        "query_baseline": observation["query_baseline"],
                        "directory": str(batch),
                    },
                }
                path = directory / "public-navigation.json"
                if evidence["status"] == "completed" and any(
                    status != "ok" for status in readers[case_id].values()
                ):
                    evidence["status"] = "partial"
                if path.exists():
                    previous = verify_public_navigation(campaign, case)
                    if previous != evidence:
                        raise ValueError(f"Frozen public navigation inputs changed: {case_id}")
                    continue
                write_json(path, evidence)
                write_json(
                    directory / "public-navigation-seal.json",
                    {
                        "artifact_sha256": file_digest(path),
                        "reference_seal_sha256": inputs["references"][case_id],
                        "measurement_seal_sha256": inputs["measurement_seal_sha256"],
                        "batch_seal_sha256": file_digest(batch / "seal.json"),
                        "batch_directory": str(batch),
                    },
                )

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(run_project, sorted({case["project"] for case in selectedcases})):
            del result
