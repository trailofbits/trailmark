"""Hash-sealed orchestration for the source-blind evaluation pilot."""

from __future__ import annotations

import concurrent.futures
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import shutil
import sys
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

from tests.graph_eval.corpus import BASELINE, SEED, checkout_projects
from tests.graph_eval.measure import measure_project
from tests.graph_eval.pilot import select_cases, source_bundle
from tests.graph_eval.prompt_payload import ENCODING_VERSION, PROJECTION_VERSION
from tests.graph_eval.prompts import AdjudicationPrompt, build_adjudication_prompt, reference_prompt
from tests.graph_eval.schema import (
    ADJUDICATION_SCHEMA,
    REFERENCE_SCHEMA,
    adjudication_schema,
    validate_adjudication,
    validate_reference,
)
from tests.graph_eval.util import (
    case_partition,
    command,
    digest,
    file_digest,
    read_json,
    source_inventory,
    write_json,
)
from tests.graph_eval.validators import validate_files
from tests.graph_eval.worker import (
    BLOCKED_COMPONENTS,
    DEFAULT_CONTEXT,
    model_defaults,
    retain_image,
    run_agent,
    validate_source_view,
    worker_fingerprint,
)


def now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def _check_tool_source(runtime: dict[str, Any]) -> Path:
    root = Path(runtime["trailmark"]).resolve().parents[2]
    revision = command(["git", "-C", str(root), "rev-parse", "HEAD"])
    if revision != BASELINE:
        raise ValueError(f"Pilot requires baseline {BASELINE}, got {revision}")
    if command(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"]):
        raise ValueError("Frozen Trailmark baseline worktree has tracked changes")
    return root


def load_manifest(campaign: Path) -> dict[str, Any]:
    manifest = read_json(campaign / "manifest.json")
    seal = manifest.pop("manifest_sha256")
    if digest(manifest) != seal:
        raise ValueError("Campaign manifest has changed since preparation")
    manifest["manifest_sha256"] = seal
    for name in ("cases", "projects"):
        if digest(read_json(campaign / f"{name}.json")) != manifest[f"{name}_sha256"]:
            raise ValueError(f"Frozen {name} have changed")
    for name, expected in manifest["contract"].items():
        if file_digest(campaign / "contract" / name) != expected:
            raise ValueError(f"Frozen contract changed: {name}")
    return manifest


def _source_view(root: Path, case: dict[str, Any], destination: Path) -> None:
    """Expose selected ordinary files, never repository metadata or instruction files."""
    allowed = {item["path"] for item in [*case["primary"], *case["context"]]}
    for name in allowed:
        relative = Path(name)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or set(relative.parts) & BLOCKED_COMPONENTS
        ):
            raise ValueError(f"Invalid source selection: {name}")
        original = root / relative
        if original.is_symlink() or not original.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Source symlink or path escape: {name}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if file_digest(original) != file_digest(target):
                raise ValueError(f"Frozen source view changed: {name}")
        else:
            shutil.copyfile(original, target)
    actual = {
        str(path.relative_to(destination)) for path in destination.rglob("*") if path.is_file()
    }
    if actual != allowed:
        raise ValueError("Source view contains unexpected files")
    validate_source_view(destination, destination.parent / "job-output")


def _view_hash(destination: Path) -> str:
    return digest(
        [
            {"path": path.relative_to(destination).as_posix(), "sha256": file_digest(path)}
            for path in sorted(destination.rglob("*"))
            if path.is_file()
        ]
    )


def prepare(
    campaign: Path,
    *,
    tool_python: Path,
    image: str,
    workers: int = 4,
    validator_python: Path | None = None,
    context: str = DEFAULT_CONTEXT,
) -> dict[str, Any]:
    """Freeze scopes, source bytes, runtime identity and role inputs before observation."""
    campaign = campaign.resolve()
    if (campaign / "manifest.json").exists():
        existing = load_manifest(campaign)
        if (
            str(tool_python.absolute()) != existing["tool_python"]
            or image != existing["worker_image"]
        ):
            raise ValueError("Prepared campaign runtime differs; use a new campaign directory")
        retain_image(existing["worker_image"], context=existing["docker_context"])
        return existing
    if workers < 1:
        raise ValueError("Workers must be positive")
    campaign.mkdir(parents=True, exist_ok=True)
    retained_image = retain_image(image, context=context)
    image = retained_image["image"]
    projects = (
        read_json(campaign / "projects.json")
        if (campaign / "projects.json").exists()
        else checkout_projects(campaign, workers)
    )
    cases = select_cases(projects)
    if (campaign / "scope-seal.json").exists() and read_json(campaign / "scope-seal.json")[
        "cases_sha256"
    ] != digest(cases):
        raise ValueError("Previously frozen scopes differ from source selections")
    if (campaign / "cases.json").exists() and read_json(campaign / "cases.json") != cases:
        raise ValueError("Existing frozen case selections differ")
    write_json(campaign / "cases.json", cases)
    projects_by_id = {project["id"]: project for project in projects}
    for project in projects:
        for root in project["roots"].values():
            if digest(source_inventory(Path(root))) != project["source_hash"]:
                raise ValueError(f"Source inventory changed: {root}")
    for case in cases:
        root = Path(projects_by_id[case["project"]]["roots"]["sources"])
        source_bundle(root, case)
        _source_view(root, case, campaign / "source-views" / case["id"])
    tool_python = tool_python.absolute()
    runtime = json.loads(
        command(
            [
                str(tool_python),
                "-I",
                "-c",
                "import json,sys,trailmark; from trailmark.parse import supported_languages; "
                "print(json.dumps({'executable':sys.executable,'version':sys.version,"
                "'trailmark':trailmark.__file__,'languages':supported_languages()}))",
            ]
        )
    )
    tool_root = _check_tool_source(runtime)
    contract = {}
    for name in (
        "README.md",
        "docs/languages.md",
        "docs/graph-model.md",
        "docs/call-resolution.md",
        "docs/parser-coverage.md",
    ):
        path = campaign / "contract" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text((tool_root / name).read_text())
        contract[name] = file_digest(path)
    manifest = {
        "schema_version": 1,
        "created_at": now(),
        "baseline": BASELINE,
        "seed": SEED,
        "selection": "purposeful ordinary/stress strata; no population estimates",
        "cases_sha256": digest(cases),
        "projects_sha256": digest(projects),
        "cases": cases,
        "projects": projects,
        "source_views": {
            case["id"]: _view_hash(campaign / "source-views" / case["id"]) for case in cases
        },
        "frontend_status": {
            language: "pilot" if language in {c["language"] for c in cases} else "not_evaluated"
            for language in runtime["languages"]
        },
        "tool_python": str(tool_python),
        "tool_runtime": runtime,
        "validator_python": str(validator_python.absolute())
        if validator_python
        else sys.executable,
        "worker_image": image,
        "worker_image_retention_tag": retained_image["retention_tag"],
        "worker_fingerprint": worker_fingerprint(),
        "docker_context": context,
        **model_defaults(),
        "reference_schema_sha256": digest(REFERENCE_SCHEMA),
        "adjudication_schema_sha256": digest(ADJUDICATION_SCHEMA),
        "prompt_sha256": file_digest(Path(__file__).with_name("prompts.py")),
        "prompt_payload_sha256": file_digest(Path(__file__).with_name("prompt_payload.py")),
        "judge_encoding": ENCODING_VERSION,
        "judge_projection": PROJECTION_VERSION,
        "contract": contract,
        "limits": {
            "workers": workers,
            "reader_timeout": 900,
            "judge_timeout": 900,
            "measurement_timeout": 1200,
            "max_attempts": 2,
        },
        "stop_rule": "Stop after 32 pilot cases; review evidence before expansion or parser fixes",
    }
    manifest["manifest_sha256"] = digest(manifest)
    write_json(campaign / "manifest.json", manifest)
    return manifest


def select(
    manifest: dict[str, Any],
    *,
    languages: list[str] | None = None,
    case_ids: list[str] | None = None,
    shard: tuple[int, int] = (1, 1),
) -> list[dict[str, Any]]:
    cases = manifest["cases"]
    if languages and set(languages) - {case["language"] for case in cases}:
        raise ValueError("Unknown or non-pilot language selector")
    if case_ids and set(case_ids) - {case["id"] for case in cases}:
        raise ValueError("Unknown case selector")
    return [
        case
        for case in cases
        if (not languages or case["language"] in languages)
        and (not case_ids or case["id"] in case_ids)
        and case_partition(case["id"], *shard)
    ]


def _validate_protocol(manifest: dict[str, Any]) -> None:
    actual = {
        "worker_fingerprint": worker_fingerprint(),
        "reference_schema_sha256": digest(REFERENCE_SCHEMA),
        "adjudication_schema_sha256": digest(ADJUDICATION_SCHEMA),
        "prompt_sha256": file_digest(Path(__file__).with_name("prompts.py")),
        "prompt_payload_sha256": file_digest(Path(__file__).with_name("prompt_payload.py")),
        "judge_encoding": ENCODING_VERSION,
        "judge_projection": PROJECTION_VERSION,
    }
    for key, value in actual.items():
        if manifest.get(key) != value:
            raise ValueError(f"Protocol implementation changed after freeze: {key}")
    _check_tool_source(manifest["tool_runtime"])


@contextlib.contextmanager
def _artifact_lock(destination: Path, name: str = ".job.lock") -> Generator[None, None, None]:
    """Reject overlapping writers; locks release after crashes without deleting evidence."""
    destination.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(destination / name, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError(f"Artifact writer already running: {destination}") from error
        yield
    finally:
        os.close(descriptor)


def _verified_job(path: Path, case_id: str, role: str) -> dict[str, Any]:
    state = read_json(path)
    output = state.get("output_path")
    if state["status"] == "ok" and not output:
        raise ValueError(f"Successful job has no sealed response: {path}")
    if output:
        response = Path(output)
        if not response.resolve().is_relative_to(path.parent.resolve()):
            raise ValueError(f"Agent response escapes its job directory: {path}")
        if file_digest(response) != state.get("output_sha256"):
            raise ValueError(f"Frozen agent response changed: {path}")
        if state["status"] == "ok":
            validator = validate_adjudication if role == "judge" else validate_reference
            validator(read_json(response), case_id)
    return state


def verify_reference_states(campaign: Path, case: dict[str, Any]) -> list[dict[str, Any]]:
    """Verify both frozen source readings, including their actual response bytes."""
    destination = campaign / "cases" / case["id"]
    seal_path = destination / "reference-seal.json"
    if not seal_path.is_file():
        raise ValueError(f"Missing pre-reveal reference seal: {case['id']}")
    states = []
    for role in ("reader-A", "reader-B"):
        path = destination / role / "job.json"
        if not path.is_file():
            raise ValueError(f"Missing frozen reader state: {case['id']}/{role}")
        state = _verified_job(path, case["id"], role)
        if state["status"] == "running":
            raise ValueError(f"Unfinished source reading: {case['id']}/{role}")
        states.append(state)
    expected = {"case_id": case["id"], "reader_states": [digest(state) for state in states]}
    if read_json(seal_path) != expected:
        raise ValueError(f"Frozen source references changed: {case['id']}")
    return states


def verify_measurement(campaign: Path, project_id: str) -> dict[str, Any]:
    """Read measurement and independent validation only after verifying their seals."""
    destination = campaign / "measurements" / project_id
    seal_path = destination / "seal.json"
    if not seal_path.is_file():
        raise ValueError(f"Missing measurement seal: {project_id}")
    seal = read_json(seal_path)
    if file_digest(destination / "measurement.json") != seal["sha256"]:
        raise ValueError(f"Frozen measurement changed: {project_id}")
    validation = destination / "validation" / "validation.json"
    if not seal.get("validation_sha256") or file_digest(validation) != seal["validation_sha256"]:
        raise ValueError(f"Frozen independent validation changed: {project_id}")
    return read_json(destination / "measurement.json")


def _comparison_seal(campaign: Path, case: dict[str, Any]) -> dict[str, str]:
    destination = campaign / "cases" / case["id"]
    seal = {
        "sha256": file_digest(destination / "comparisons.json"),
        "reference_seal_sha256": file_digest(destination / "reference-seal.json"),
        "measurement_seal_sha256": file_digest(
            campaign / "measurements" / case["project"] / "seal.json"
        ),
    }
    navigation = destination / "public-navigation-seal.json"
    if navigation.exists():
        seal["public_navigation_seal_sha256"] = file_digest(navigation)
    return seal


def _verified_adjudication(campaign: Path, case: dict[str, Any]) -> dict[str, Any] | None:
    from tests.graph_eval.compare import validate_adjudication_claims
    from tests.graph_eval.report import read_adjudications

    destination = campaign / "cases" / case["id"]
    journal = read_adjudications(destination / "adjudications.jsonl")
    exported = destination / "adjudication.json"
    if not journal:
        if exported.exists():
            raise ValueError("Adjudication export has no append-only journal")
        return None
    state = _verified_job(destination / "judge" / "job.json", case["id"], "judge")
    if state["status"] != "ok":
        raise ValueError("Adjudication journal has no successful sealed judge response")
    initial = journal[0]["adjudication"]
    if initial != read_json(Path(state["output_path"])):
        raise ValueError("Initial adjudication differs from the sealed judge response")
    for correction in journal[1:]:
        provenance = correction.get("provenance", {})
        if not all(provenance.get(key) for key in ("reviewer", "reason", "reviewed_at")):
            raise ValueError("Adjudication correction lacks review provenance")
    adjudication = journal[-1]["adjudication"]
    comparisons = read_json(destination / "comparisons.json")
    validate_adjudication_claims(
        adjudication,
        {claim["claim_id"] for comparison in comparisons for claim in comparison["discrepancies"]},
    )
    if exported.exists() and read_json(exported) != adjudication:
        raise ValueError("Adjudication export differs from its append-only journal")
    return adjudication


def _agent_job(
    campaign: Path,
    manifest: dict[str, Any],
    case: dict[str, Any],
    role: str,
    prompt: str,
    schema: dict[str, Any],
) -> dict[str, Any]:
    with _artifact_lock(campaign / "cases" / case["id"] / role):
        return _agent_job_unlocked(campaign, manifest, case, role, prompt, schema)


def _agent_job_unlocked(
    campaign: Path,
    manifest: dict[str, Any],
    case: dict[str, Any],
    role: str,
    prompt: str,
    schema: dict[str, Any],
) -> dict[str, Any]:
    source = campaign / "source-views" / case["id"]
    validate_source_view(source, campaign / "cases" / case["id"])
    if _view_hash(source) != manifest["source_views"][case["id"]]:
        raise ValueError("Frozen mounted source bytes changed")
    destination = campaign / "cases" / case["id"] / role
    identity = digest(
        {
            "case": case["input_hash"],
            "prompt": prompt,
            "schema": schema,
            "image": manifest["worker_image"],
            "model": manifest["model"],
            "reasoning": manifest["reasoning"],
            "worker": manifest["worker_fingerprint"],
        }
    )
    state_path = destination / "job.json"
    if state_path.exists():
        state = _verified_job(state_path, case["id"], role)
        if state["input_hash"] != identity:
            raise ValueError(f"Frozen job inputs changed: {case['id']}/{role}")
        if state["status"] == "running":
            raise ValueError(f"Interrupted job needs inspection: {destination}")
        if (
            state.get("output_path")
            and file_digest(Path(state["output_path"])) != state["output_sha256"]
        ):
            raise ValueError(f"Frozen agent response changed: {destination}")
        return state
    state: dict[str, Any] = {
        "input_hash": identity,
        "role": role,
        "status": "running",
        "started_at": now(),
    }
    write_json(state_path, state)
    started = time.monotonic()
    try:
        result = run_agent(
            campaign / "source-views" / case["id"],
            prompt,
            schema,
            destination / "worker",
            manifest["model"],
            manifest["reasoning"],
            role,
            manifest["limits"]["judge_timeout" if role == "judge" else "reader_timeout"],
            context=manifest["docker_context"],
            image=manifest["worker_image"],
        )
        state.update(status=result["status"], worker=result)
        if result["status"] == "ok":
            response = read_json(Path(result["response_path"]))
            state.update(
                output_path=result["response_path"],
                output_sha256=file_digest(Path(result["response_path"])),
            )
            validator = validate_adjudication if role == "judge" else validate_reference
            validator(response, case["id"])
    except (ValueError, OSError) as error:
        state.update(status="invalid_output", error=str(error))
    state.update(finished_at=now(), elapsed_seconds=round(time.monotonic() - started, 3))
    write_json(state_path, state)
    print(f"{case['id']} {role}: {state['status']}", flush=True)
    return state


def run_readers(
    campaign: Path, manifest: dict[str, Any], cases: list[dict[str, Any]], workers: int
) -> None:
    projects = {project["id"]: project for project in manifest["projects"]}
    jobs = []
    for case in cases:
        root = Path(projects[case["project"]]["roots"]["sources"])
        prompt = reference_prompt(case, source_bundle(root, case))
        jobs.extend((case, role, prompt) for role in ("reader-A", "reader-B"))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(_agent_job, campaign, manifest, case, role, prompt, REFERENCE_SCHEMA)
            for case, role, prompt in jobs
        ]
        for future in concurrent.futures.as_completed(futures):
            future.result()
    for case in cases:
        states = [
            read_json(campaign / "cases" / case["id"] / role / "job.json")
            for role in ("reader-A", "reader-B")
        ]
        seal = {"case_id": case["id"], "reader_states": [digest(state) for state in states]}
        path = campaign / "cases" / case["id"] / "reference-seal.json"
        if path.exists() and read_json(path) != seal:
            raise ValueError("Frozen source references changed")
        write_json(path, seal)


def run_measurements(
    campaign: Path, manifest: dict[str, Any], cases: list[dict[str, Any]], workers: int
) -> None:
    projects = {project["id"]: project for project in manifest["projects"]}

    for project_id in {case["project"] for case in cases}:
        for project_case in manifest["cases"]:
            if project_case["project"] == project_id:
                verify_reference_states(campaign, project_case)

    def measure(project_id: str) -> None:
        with _artifact_lock(campaign / "measurements" / project_id, ".measurement.lock"):
            measure_locked(project_id)

    def measure_locked(project_id: str) -> None:
        project = projects[project_id]
        root = Path(project["roots"]["measurement-sources"])
        destination = campaign / "measurements" / project_id
        if (destination / "seal.json").exists():
            verify_measurement(campaign, project_id)
            return
        if (destination / "measurement.json").exists():
            raise ValueError(f"Interrupted measurement needs inspection: {destination}")
        if digest(source_inventory(root)) != project["source_hash"]:
            raise ValueError("Measurement source bytes changed")
        result = measure_project(
            root,
            project["language"],
            destination,
            python=Path(manifest["tool_python"]),
            timeout=manifest["limits"]["measurement_timeout"],
        )
        files = [
            scope["path"]
            for case in manifest["cases"]
            if case["project"] == project_id
            for scope in case["primary"]
        ]
        validate_files(
            root,
            project["language"],
            files,
            destination / "validation",
            python=Path(manifest["validator_python"]),
        )
        write_json(
            destination / "seal.json",
            {
                "sha256": file_digest(destination / "measurement.json"),
                "validation_sha256": file_digest(destination / "validation" / "validation.json"),
            },
        )
        print(f"{project_id} measurement: {result['status']}", flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(measure, sorted({case["project"] for case in cases})):
            del result


def _prefix_claims(value: Any, prefix: str) -> Any:
    if isinstance(value, list):
        return [_prefix_claims(item, prefix) for item in value]
    if isinstance(value, dict):
        return {
            key: prefix + item if key == "claim_id" else _prefix_claims(item, prefix)
            for key, item in value.items()
        }
    return value


def _projection(measurement: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    """Select graph evidence; the prompt builder separately excerpts global query context."""
    graph = measurement.get("graph", {})
    nodes = graph.get("nodes", {})

    def inside(location: dict[str, Any] | None) -> bool:
        if not location:
            return False
        return any(
            location.get("file_path", location.get("path")) == scope["path"]
            and location.get("start_line", location.get("line", 0)) >= scope["start_line"]
            and location.get("end_line", 0) <= scope["end_line"]
            for scope in [*case["primary"], *case["context"]]
        )

    selected = {ident for ident, node in nodes.items() if inside(node.get("location"))}
    edges = [
        edge
        for edge in graph.get("edges", [])
        if inside(edge.get("location")) or edge.get("source") in selected
    ]
    selected.update(edge["source"] for edge in edges)
    selected.update(edge["target"] for edge in edges)
    ownership = [
        edge
        for edge in graph.get("edges", [])
        if edge.get("kind") == "contains" and edge.get("target") in selected
    ]
    selected.update(edge["source"] for edge in ownership)
    return {
        "status": measurement["status"],
        "graph": {
            "nodes": {ident: nodes[ident] for ident in sorted(selected) if ident in nodes},
            "edges": list({digest(edge): edge for edge in [*edges, *ownership]}.values()),
        },
        "diagnostics": [
            item
            for item in measurement.get("diagnostics", [])
            if inside(item.get("location", item))
        ],
        "auto_detection": measurement.get("auto_detection"),
        "deterministic": measurement.get("deterministic"),
        "query_answers": measurement.get("query_answers", []),
    }


def _save_judge_prompt(
    destination: Path,
    built: AdjudicationPrompt,
    prompt: str,
    output_schema: dict[str, Any],
) -> None:
    """Freeze reviewable logical/presentation evidence before any worker launch."""
    metadata = {
        **built.provenance,
        "full_prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "full_prompt_utf8_bytes": len(prompt.encode("utf-8")),
        "output_schema_sha256": digest(output_schema),
        "prompt_payload_sha256": file_digest(Path(__file__).with_name("prompt_payload.py")),
    }
    for name, value in (
        ("prompt-evidence.json", built.logical_payload),
        ("prompt-presentation.json", built.presentation),
        ("prompt-provenance.json", metadata),
    ):
        path = destination / "judge" / name
        if path.exists() and read_json(path) != value:
            raise ValueError(f"Frozen judge presentation changed: {name}")
        write_json(path, value)


def case_result(campaign: Path, case: dict[str, Any]) -> dict[str, Any]:
    from tests.graph_eval.agreement import reader_agreement

    destination = campaign / "cases" / case["id"]
    row: dict[str, Any] = {
        "case_id": case["id"],
        "language": case["language"],
        "status": "not_run",
        "readers": [],
        "comparisons": [],
        "adjudication": None,
        "measurement_status": "not_run",
    }
    for role in ("reader-A", "reader-B"):
        path = destination / role / "job.json"
        row["readers"].append(
            _verified_job(path, case["id"], role) if path.exists() else {"status": "not_run"}
        )
    measurement_path = campaign / "measurements" / case["project"] / "measurement.json"
    if measurement_path.exists():
        row["measurement_status"] = verify_measurement(campaign, case["project"])["status"]
    for key, filename in (
        ("comparisons", "comparisons.json"),
        ("reference_seal", "reference-seal.json"),
        ("replay", "replay.json"),
    ):
        if (destination / filename).exists():
            row[key] = read_json(destination / filename)
    if row.get("reference_seal") is not None:
        verify_reference_states(campaign, case)
        if all(reader["status"] == "ok" for reader in row["readers"]):
            references = [read_json(Path(reader["output_path"])) for reader in row["readers"]]
            row["reader_agreement"] = reader_agreement(*references)
            for state, reference in zip(row["readers"], references, strict=True):
                state["reference_status"] = reference["status"]
    if (destination / "public-navigation.json").exists():
        from tests.graph_eval.public_queries import verify_public_navigation

        row["public_navigation"] = verify_public_navigation(campaign, case)
    if (destination / "comparisons.json").exists():
        verify_reference_states(campaign, case)
        verify_measurement(campaign, case["project"])
        seal_path = destination / "comparisons-seal.json"
        if not seal_path.is_file() or read_json(seal_path) != _comparison_seal(campaign, case):
            raise ValueError("Frozen comparisons changed")
    row["adjudication"] = _verified_adjudication(campaign, case)
    validation = campaign / "measurements" / case["project"] / "validation" / "validation.json"
    if validation.exists():
        row["validation"] = read_json(validation)
    judge = destination / "judge" / "job.json"
    if judge.exists():
        row["judge_job"] = _verified_job(judge, case["id"], "judge")
    if row["adjudication"] is not None:
        row["status"] = "adjudicated"
    elif judge.exists():
        row["status"] = "judge_" + read_json(judge)["status"]
    elif row["comparisons"]:
        row["status"] = "compared"
    elif all(reader["status"] == "ok" for reader in row["readers"]):
        row["status"] = "references_frozen"
    elif any(reader["status"] != "not_run" for reader in row["readers"]):
        row["status"] = "incomplete_references"
    return row


def run_judges(
    campaign: Path, manifest: dict[str, Any], cases: list[dict[str, Any]], workers: int
) -> None:
    from tests.graph_eval.compare import compare_reference, validate_adjudication_claims
    from tests.graph_eval.report import append_adjudication, read_adjudications

    def judge(case: dict[str, Any]) -> None:
        with _artifact_lock(campaign / "cases" / case["id"], ".judge.lock"):
            judge_locked(case)

    def judge_locked(case: dict[str, Any]) -> None:
        destination = campaign / "cases" / case["id"]
        row = case_result(campaign, case)
        if row["adjudication"] is not None:
            return
        if not all(reader["status"] == "ok" for reader in row["readers"]):
            return
        verify_reference_states(campaign, case)
        measurement = verify_measurement(campaign, case["project"])
        if measurement["status"] != "ok":
            return
        readers = [read_json(Path(reader["output_path"])) for reader in row["readers"]]
        comparisons = [
            _prefix_claims(compare_reference(reader, measurement, case), f"reader-{label}:")
            for label, reader in zip(("A", "B"), readers, strict=True)
        ]
        navigation = row.get("public_navigation")
        if navigation:
            claims = [
                {**record, "issues": ["public_navigation_" + record["status"]]}
                for record in navigation["records"]
                if record["status"] in {"discrepancy", "unsupported", "error"}
            ]
            comparisons.append({"basis": "public_query_api", "discrepancies": claims})
        comparison_path = destination / "comparisons.json"
        if comparison_path.exists() and read_json(comparison_path) != comparisons:
            raise ValueError("Comparison result changed after freeze")
        write_json(comparison_path, comparisons)
        seal_path = destination / "comparisons-seal.json"
        seal = _comparison_seal(campaign, case)
        if seal_path.exists() and read_json(seal_path) != seal:
            raise ValueError("Comparison inputs changed after freeze")
        write_json(seal_path, seal)
        summary = _projection(measurement, case)
        summary["independent_validation"] = row.get("validation")
        summary["public_navigation"] = navigation
        summary["replay_command"] = (
            f"python -m tests.graph_eval replay {campaign} --case {case['id']}"
        )
        contract = "\n\n".join(
            f"--- Documented contract: {name} ---\n{(campaign / 'contract' / name).read_text()}"
            for name in manifest["contract"]
        )
        built = build_adjudication_prompt(case, *readers, summary, comparisons)
        prompt = built.text + "\n\n" + contract
        required_ids = built.logical_payload["required_claim_ids"]
        output_schema = adjudication_schema(required_ids)
        _save_judge_prompt(destination, built, prompt, output_schema)
        result = _agent_job(campaign, manifest, case, "judge", prompt, output_schema)
        if result["status"] == "ok":
            output = read_json(Path(result["output_path"]))
            try:
                validate_adjudication_claims(
                    output,
                    set(required_ids),
                )
            except ValueError as error:
                result.update(status="invalid_output", error=str(error))
                write_json(destination / "judge" / "job.json", result)
                return
            journal = destination / "adjudications.jsonl"
            history = read_adjudications(journal)
            if not history:
                append_adjudication(journal, output)
            elif history[-1]["adjudication"] != output:
                raise ValueError("Existing adjudication journal differs from judge output")
            write_json(destination / "adjudication.json", output)
            _verified_adjudication(campaign, case)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(judge, cases):
            del result


def run(
    campaign: Path,
    *,
    phase: str = "all",
    workers: int | None = None,
    languages: list[str] | None = None,
    case_ids: list[str] | None = None,
    shard: tuple[int, int] = (1, 1),
) -> None:
    manifest = load_manifest(campaign)
    _validate_protocol(manifest)
    selected = select(manifest, languages=languages, case_ids=case_ids, shard=shard)
    count = manifest["limits"]["workers"] if workers is None else workers
    if count < 1:
        raise ValueError("Workers must be positive")
    if phase in {"all", "readers"}:
        run_readers(campaign, manifest, selected, count)
    if phase in {"all", "measure"}:
        run_measurements(campaign, manifest, selected, count)
        from tests.graph_eval.public_queries import run_navigation

        run_navigation(campaign, manifest, selected, count)
    if phase in {"all", "judge"}:
        run_judges(campaign, manifest, selected, count)


def report(campaign: Path) -> str:
    from tests.graph_eval.report import generate_report

    manifest = load_manifest(campaign)
    rows = [case_result(campaign, case) for case in manifest["cases"]]
    write_json(campaign / "results.json", rows)
    text = generate_report(manifest, rows)
    (campaign / "report.md").write_text(text)
    return text


def replay(campaign: Path, case_id: str) -> dict[str, Any]:
    """Repeat observation without rerunning models or executing their proposed commands."""
    from tests.graph_eval.compare import compare_reference

    manifest = load_manifest(campaign)
    _validate_protocol(manifest)
    case = select(manifest, case_ids=[case_id])[0]
    project = next(project for project in manifest["projects"] if project["id"] == case["project"])
    verify_reference_states(campaign, case)
    case_result(campaign, case)
    original = verify_measurement(campaign, project["id"])
    if (
        digest(source_inventory(Path(project["roots"]["measurement-sources"])))
        != project["source_hash"]
    ):
        raise ValueError("Replay source bytes changed")
    destination = campaign / "replays" / case_id / now().replace(":", "-")
    fresh = measure_project(
        Path(project["roots"]["measurement-sources"]),
        case["language"],
        destination,
        python=Path(manifest["tool_python"]),
    )
    comparisons = []
    for role in ("reader-A", "reader-B"):
        path = campaign / "cases" / case_id / role / "job.json"
        if path.exists():
            state = read_json(path)
            if state["status"] == "ok":
                comparisons.append(
                    compare_reference(read_json(Path(state["output_path"])), fresh, case)
                )
    result = {
        "case_id": case_id,
        "status": fresh["status"],
        "graph_identical": digest(fresh.get("graph")) == digest(original.get("graph")),
        "source_identical": fresh.get("source_inventory") == original.get("source_inventory"),
        "measurement": str(destination / "measurement.json"),
        "comparisons": comparisons,
        "scope": "Replays observation; does not prove an agent's causal diagnosis",
    }
    write_json(destination / "replay.json", result)
    write_json(campaign / "cases" / case_id / "replay.json", result)
    return result
