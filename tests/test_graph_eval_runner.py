"""Role seals reject changed evidence before observation, adjudication, or resume."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.graph_eval import campaign
from tests.graph_eval.report import append_adjudication
from tests.graph_eval.schema import REFERENCE_SCHEMA
from tests.graph_eval.util import digest, file_digest, read_json, write_json


def test_prepare_requires_an_explicit_image_choice_before_any_work(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    from tests.graph_eval.__main__ import main

    monkeypatch.setattr(
        "sys.argv",
        ["graph-eval", "prepare", str(tmp_path), "--tool-python", "/unused/frozen/python"],
    )
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert "--image" in capsys.readouterr().err
    assert not list(tmp_path.iterdir())


def _case(ident: str = "case") -> dict[str, Any]:
    return {
        "id": ident,
        "project": "project",
        "language": "python",
        "profile": "runtime",
        "input_hash": "frozen-case",
        "primary": [{"path": "module.py", "start_line": 1, "end_line": 1}],
        "context": [],
    }


def _reference(ident: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "case_id": ident,
        "status": "partial",
        "inventory_complete": False,
        "declarations": [],
        "assertions": [],
        "questions": [
            {
                "id": f"q{i}",
                "operation": "ownership",
                "subject": "file:module.py",
                "target": None,
                "expected": [],
                "question": "Children?",
                "evidence": "Partial scope",
            }
            for i in range(3)
        ],
        "notes": [],
    }


def _frozen_readers(root: Path, case: dict[str, Any]) -> list[dict[str, Any]]:
    states = []
    for role in ("reader-A", "reader-B"):
        destination = root / "cases" / case["id"] / role
        response = destination / "worker" / "response.json"
        write_json(response, _reference(case["id"]))
        state = {
            "status": "ok",
            "role": role,
            "output_path": str(response),
            "output_sha256": file_digest(response),
        }
        write_json(destination / "job.json", state)
        states.append(state)
    write_json(
        root / "cases" / case["id"] / "reference-seal.json",
        {"case_id": case["id"], "reader_states": [digest(state) for state in states]},
    )
    return states


def _frozen_measurement(root: Path) -> Path:
    destination = root / "measurements" / "project"
    write_json(destination / "measurement.json", {"status": "ok", "graph": {}})
    write_json(destination / "validation" / "validation.json", {"status": "completed"})
    write_json(
        destination / "seal.json",
        {
            "sha256": file_digest(destination / "measurement.json"),
            "validation_sha256": file_digest(destination / "validation" / "validation.json"),
        },
    )
    return destination


def test_reference_seal_checks_actual_response_bytes(tmp_path: Path) -> None:
    case = _case()
    states = _frozen_readers(tmp_path, case)
    assert campaign.verify_reference_states(tmp_path, case) == states
    write_json(Path(states[0]["output_path"]), _reference("another-case"))
    with pytest.raises(ValueError, match="Frozen agent response changed"):
        campaign.verify_reference_states(tmp_path, case)


def test_manifest_and_case_inventory_cannot_change_after_preparation(tmp_path: Path) -> None:
    for name in ("cases", "projects"):
        write_json(tmp_path / f"{name}.json", [])
    manifest = {"cases_sha256": digest([]), "projects_sha256": digest([]), "contract": {}}
    manifest["manifest_sha256"] = digest(manifest)
    write_json(tmp_path / "manifest.json", manifest)
    assert campaign.load_manifest(tmp_path) == manifest
    write_json(tmp_path / "cases.json", [{"id": "unregistered"}])
    with pytest.raises(ValueError, match="Frozen cases"):
        campaign.load_manifest(tmp_path)
    write_json(tmp_path / "cases.json", [])
    write_json(tmp_path / "manifest.json", {**manifest, "seed": 123})
    with pytest.raises(ValueError, match="Campaign manifest"):
        campaign.load_manifest(tmp_path)


def test_reference_seal_checks_terminal_job_state(tmp_path: Path) -> None:
    case = _case()
    _frozen_readers(tmp_path, case)
    path = tmp_path / "cases" / case["id"] / "reader-A" / "job.json"
    state = read_json(path)
    state["status"] = "running"
    write_json(path, state)
    with pytest.raises(ValueError, match="Unfinished source reading"):
        campaign.verify_reference_states(tmp_path, case)


@pytest.mark.parametrize("artifact", ["measurement.json", "validation/validation.json"])
def test_measurement_seals_both_graph_and_independent_validation(
    tmp_path: Path, artifact: str
) -> None:
    destination = _frozen_measurement(tmp_path)
    assert campaign.verify_measurement(tmp_path, "project")["status"] == "ok"
    write_json(destination / artifact, {"status": "changed"})
    with pytest.raises(ValueError, match="Frozen"):
        campaign.verify_measurement(tmp_path, "project")


def test_measurement_cannot_reveal_graph_before_other_project_case_is_frozen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ordinary, stress = _case("ordinary"), _case("stress")
    _frozen_readers(tmp_path, ordinary)
    called = []
    monkeypatch.setattr(campaign, "measure_project", lambda *_args, **_kwargs: called.append(True))
    manifest = {"cases": [ordinary, stress], "projects": [{"id": "project"}]}
    with pytest.raises(ValueError, match="Missing pre-reveal reference seal: stress"):
        campaign.run_measurements(tmp_path, manifest, [ordinary], workers=1)
    assert called == []


def test_adjudication_export_must_match_journal_and_sealed_judge(tmp_path: Path) -> None:
    case = _case()
    destination = tmp_path / "cases" / case["id"]
    output = {"schema_version": 1, "case_id": case["id"], "decisions": [], "notes": []}
    response = destination / "judge" / "worker" / "response.json"
    write_json(response, output)
    write_json(
        destination / "judge" / "job.json",
        {"status": "ok", "output_path": str(response), "output_sha256": file_digest(response)},
    )
    append_adjudication(destination / "adjudications.jsonl", output)
    write_json(destination / "adjudication.json", output)
    write_json(destination / "comparisons.json", [])
    assert campaign._verified_adjudication(tmp_path, case) == output
    write_json(destination / "adjudication.json", {**output, "notes": ["changed"]})
    with pytest.raises(ValueError, match="differs from its append-only journal"):
        campaign._verified_adjudication(tmp_path, case)
    write_json(destination / "adjudication.json", output)
    write_json(response, {**output, "notes": ["changed"]})
    with pytest.raises(ValueError, match="Frozen agent response changed"):
        campaign._verified_adjudication(tmp_path, case)


def test_unjournaled_adjudication_cannot_enter_report(tmp_path: Path) -> None:
    case = _case()
    write_json(tmp_path / "cases" / case["id"] / "adjudication.json", {})
    with pytest.raises(ValueError, match="no append-only journal"):
        campaign.case_result(tmp_path, case)


@pytest.mark.parametrize("with_provenance", [True, False])
def test_review_corrections_preserve_the_initial_judge_and_require_provenance(
    tmp_path: Path,
    with_provenance: bool,
) -> None:
    case = _case()
    destination = tmp_path / "cases" / case["id"]
    initial = {"schema_version": 1, "case_id": case["id"], "decisions": [], "notes": []}
    response = destination / "judge" / "worker" / "response.json"
    write_json(response, initial)
    write_json(
        destination / "judge" / "job.json",
        {
            "status": "ok",
            "output_path": str(response),
            "output_sha256": file_digest(response),
        },
    )
    write_json(destination / "comparisons.json", [])
    journal = destination / "adjudications.jsonl"
    append_adjudication(journal, initial)
    corrected = {**initial, "notes": ["Source evidence reviewed independently"]}
    provenance = (
        {
            "reviewer": "independent-reviewer",
            "reason": "Source verification",
            "reviewed_at": "2026-11-19T00:00:00Z",
        }
        if with_provenance
        else None
    )
    append_adjudication(journal, corrected, provenance=provenance)
    write_json(destination / "adjudication.json", corrected)
    if with_provenance:
        assert campaign._verified_adjudication(tmp_path, case) == corrected
        assert read_json(response) == initial
    else:
        with pytest.raises(ValueError, match="lacks review provenance"):
            campaign._verified_adjudication(tmp_path, case)


def test_changed_comparisons_cannot_enter_report(tmp_path: Path) -> None:
    case = _case()
    _frozen_readers(tmp_path, case)
    _frozen_measurement(tmp_path)
    destination = tmp_path / "cases" / case["id"]
    write_json(destination / "comparisons.json", [])
    write_json(destination / "comparisons-seal.json", campaign._comparison_seal(tmp_path, case))
    assert campaign.case_result(tmp_path, case)["comparisons"] == []
    write_json(destination / "comparisons.json", [{"fabricated": "finding"}])
    with pytest.raises(ValueError, match="Frozen comparisons changed"):
        campaign.case_result(tmp_path, case)


def test_job_lock_rejects_overlapping_writers_and_releases_after_error(tmp_path: Path) -> None:
    with (
        campaign._artifact_lock(tmp_path),
        pytest.raises(ValueError, match="writer already running"),
        campaign._artifact_lock(tmp_path),
    ):
        pass
    with campaign._artifact_lock(tmp_path):
        pass


def test_source_view_copies_only_allowed_bytes_and_rejects_metadata(tmp_path: Path) -> None:
    source = tmp_path / "repository"
    source.mkdir()
    (source / "module.py").write_bytes(b"def entry():\r\n    pass\r\n")
    (source / "AGENTS.md").write_text("untrusted instructions")
    (source / "measurement.json").write_text("graph output")
    destination = tmp_path / "source-view"
    campaign._source_view(source, _case(), destination)
    assert (destination / "module.py").read_bytes() == (source / "module.py").read_bytes()
    assert {p.name for p in destination.rglob("*") if p.is_file()} == {"module.py"}
    case = _case()
    case["primary"][0]["path"] = "AGENTS.md"
    with pytest.raises(ValueError, match="Invalid source selection"):
        campaign._source_view(source, case, tmp_path / "bad-view")


def test_semantic_reference_failure_does_not_trigger_answer_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _case()
    source = tmp_path / "source-views" / case["id"]
    source.mkdir(parents=True)
    (source / "module.py").write_text("pass\n")
    manifest = {
        "worker_image": "sha256:worker",
        "model": "gpt-6.1-sol",
        "reasoning": "xhigh",
        "worker_fingerprint": "frozen",
        "docker_context": "isolated",
        "source_views": {case["id"]: campaign._view_hash(source)},
        "limits": {"reader_timeout": 900},
    }
    calls = []

    def agent(
        _source: Path,
        _prompt: str,
        _schema: dict[str, Any],
        output: Path,
        *_args: Any,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        calls.append(output)
        path = output / "response.json"
        write_json(path, _reference("wrong-case"))
        return {"status": "ok", "response_path": str(path), "attempts": []}

    monkeypatch.setattr(campaign, "run_agent", agent)
    state = campaign._agent_job(
        tmp_path, manifest, case, "reader-A", "read independently", REFERENCE_SCHEMA
    )
    assert state["status"] == "invalid_output"
    assert len(calls) == 1
    assert "different case" in state["error"]
    assert file_digest(Path(state["output_path"])) == state["output_sha256"]
    assert (
        campaign._agent_job(
            tmp_path, manifest, case, "reader-A", "read independently", REFERENCE_SCHEMA
        )
        == state
    )
    assert len(calls) == 1


def test_shards_are_disjoint_and_complete() -> None:
    cases = [_case(str(index)) for index in range(32)]
    manifest = {"cases": cases}
    shards = [{case["id"] for case in campaign.select(manifest, shard=(i, 5))} for i in range(1, 6)]
    assert set.union(*shards) == {case["id"] for case in cases}
    assert sum(map(len, shards)) == len(cases)
