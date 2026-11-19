"""The evaluation reader sees source bytes through a bounded read-only interface."""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.graph_eval.worker import (
    BLOCKED_COMPONENTS,
    _check_container,
    _usage,
    codex_arguments,
    validate_source_view,
)


def test_source_view_rejects_outputs_and_operational_metadata(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "module.py").write_text("def hello(): pass\n")
    validate_source_view(source, tmp_path / "outputs")
    with pytest.raises(ValueError, match="outside"):
        validate_source_view(source, source / "outputs")
    for component in BLOCKED_COMPONENTS:
        forbidden = source / component
        forbidden.write_text("operational metadata")
        with pytest.raises(ValueError, match="Operational metadata"):
            validate_source_view(source, tmp_path / "outputs")
        forbidden.unlink()


def test_source_view_rejects_symlinks_even_within_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "real.py").write_text("pass\n")
    (source / "alias.py").symlink_to(source / "real.py")
    with pytest.raises(ValueError, match="symlinks"):
        validate_source_view(source, tmp_path / "outputs")


def test_codex_configuration_disables_inherited_and_general_tools() -> None:
    arguments = codex_arguments("gpt-6.1-sol", "xhigh")
    for flag in ("--ignore-user-config", "--ignore-rules", "--ephemeral", "--json"):
        assert flag in arguments
    for setting in (
        'web_search="disabled"',
        "features.shell_tool=false",
        "features.view_image=false",
        "features.plugins=false",
        "features.multi_agent=false",
        "project_doc_max_bytes=0",
        "mcp_servers.source.required=true",
    ):
        assert setting in arguments
    assert (
        'mcp_servers.source.enabled_tools=["source_list","source_read","source_search"]'
        in arguments
    )


def _inspection() -> dict[str, Any]:
    return {
        "HostConfig": {
            "ReadonlyRootfs": True,
            "Privileged": False,
            "NetworkMode": "isolated",
            "SecurityOpt": ["no-new-privileges"],
            "CapDrop": ["ALL"],
            "Tmpfs": {"/runtime": "rw,nosuid,nodev,mode=0700,size=256m"},
        },
        "NetworkSettings": {"Networks": {"isolated": {}}},
        "Config": {"Env": ["HOME=/runtime", "CODEX_HOME=/runtime/codex"]},
        "Mounts": [
            {"Type": "bind", "Destination": destination, "RW": writable}
            for destination, writable in {
                "/source": False,
                "/input": False,
                "/output": True,
            }.items()
        ],
    }


def test_container_policy_fails_closed_on_expanded_access() -> None:
    original = _inspection()
    assert _check_container(original, "isolated")["read_only_root"]
    variants = []
    for key, value in (
        ("ReadonlyRootfs", False),
        ("Privileged", True),
        ("NetworkMode", "host"),
        ("SecurityOpt", []),
        ("CapDrop", []),
        ("Tmpfs", {}),
        ("Tmpfs", {"/runtime": "rw,nosuid,nodev,mode=0777"}),
    ):
        candidate = copy.deepcopy(original)
        candidate["HostConfig"][key] = value
        variants.append(candidate)
    candidate = copy.deepcopy(original)
    candidate["NetworkSettings"]["Networks"]["bridge"] = {}
    variants.append(candidate)
    candidate = copy.deepcopy(original)
    candidate["Mounts"][0]["RW"] = True
    variants.append(candidate)
    candidate = copy.deepcopy(original)
    candidate["Mounts"].append({"Type": "bind", "Destination": "/host-home", "RW": False})
    variants.append(candidate)
    candidate = copy.deepcopy(original)
    candidate["Mounts"].append({"Type": "bind", "Destination": "/secrets", "RW": False})
    variants.append(candidate)
    candidate = copy.deepcopy(original)
    candidate["Config"]["Env"].append("OPENAI_API_KEY=unit-test-placeholder")
    variants.append(candidate)
    for candidate in variants:
        with pytest.raises(ValueError):
            _check_container(candidate, "isolated")


def test_usage_ignores_non_json_diagnostics(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text('diagnostic\n{"type":"turn.completed","usage":{"input_tokens":12}}\n')
    assert _usage(events) == {"input_tokens": 12}


def _rpc(
    source: Path, output: Path, calls: list[tuple[str, dict[str, Any]]]
) -> list[dict[str, Any]]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to test the read-only MCP server")
    requests = [
        {
            "jsonrpc": "2.0",
            "id": index,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        for index, (name, arguments) in enumerate(calls)
    ]
    result = subprocess.run(  # noqa: S603 - trusted in-repository source server
        [node, str(Path(__file__).parent / "graph_eval" / "source_reader.mjs")],
        input="".join(json.dumps(item) + "\n" for item in requests),
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
        env={**os.environ, "SOURCE_ROOT": str(source), "SOURCE_AUDIT": str(output)},
    )
    return [json.loads(line)["result"] for line in result.stdout.splitlines()]


def test_source_tools_read_and_search_without_exposing_other_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "module.py").write_text("def caller():\n    return callee()\n")
    (source / "AGENTS.md").write_text("hidden instructions")
    (tmp_path / "secret.txt").write_text("must never be exposed")
    (source / "escape").symlink_to(tmp_path / "secret.txt")
    calls = [
        ("source_list", {"prefix": ""}),
        ("source_read", {"path": "module.py", "start_line": 1, "end_line": 2}),
        ("source_search", {"query": "callee", "prefix": ""}),
    ]
    for name in ("../secret.txt", str(tmp_path / "secret.txt"), "escape", "AGENTS.md"):
        calls.append(("source_read", {"path": name, "start_line": 1, "end_line": 1}))
    calls.extend(
        [
            ("source_read", {"path": "module.py", "start_line": 1, "end_line": 1001}),
            ("exec_command", {"command": "read secrets"}),
        ]
    )
    audit = tmp_path / "reads.jsonl"
    results = _rpc(source, audit, calls)
    assert json.loads(results[0]["content"][0]["text"])["files"] == ["module.py"]
    assert json.loads(results[1]["content"][0]["text"])["lines"][1] == {
        "line": 2,
        "text": "    return callee()",
    }
    assert json.loads(results[2]["content"][0]["text"])["hits"][0]["line"] == 2
    assert all(result["isError"] for result in results[3:])
    records = [json.loads(line) for line in audit.read_text().splitlines()]
    assert [record["status"] for record in records] == ["ok"] * 3 + ["denied"] * 6
    assert "must never be exposed" not in audit.read_text()


def test_authentication_payload_stays_in_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.graph_eval.worker import _auth_payload, _docker_environment

    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-placeholder")
    monkeypatch.setenv("CODEX_API_KEY", "also-not-forwarded")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert _auth_payload() == {"auth_mode": "apikey", "OPENAI_API_KEY": "unit-test-placeholder"}
    assert list(tmp_path.iterdir()) == []
    assert "OPENAI_API_KEY" not in _docker_environment()
    assert "CODEX_API_KEY" not in _docker_environment()


def test_existing_authentication_is_read_without_copying_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.graph_eval.worker import _auth_payload

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    path = tmp_path / "auth.json"
    data = {
        "auth_mode": "chatgpt",
        "tokens": {"access_token": "placeholder"},
        "last_refresh": "2026-11-19T00:00:00Z",
        "unrelated_config": "must not travel",
    }
    path.write_text(json.dumps(data))
    assert _auth_payload() == {
        key: value for key, value in data.items() if key != "unrelated_config"
    }
    assert list(tmp_path.iterdir()) == [path]
    path.write_text(json.dumps({"auth_mode": "unsupported"}))
    with pytest.raises(ValueError, match="unsupported or incomplete"):
        _auth_payload()


@pytest.mark.parametrize(
    ("outcomes", "expected"),
    [
        (["invalid_output", "ok"], "ok"),
        (["transport_error", "ok"], "ok"),
        (["invalid_output", "invalid_output"], "invalid_output"),
        (["timeout"], "timeout"),
        (["ok"], "ok"),
    ],
)
def test_worker_retries_only_transport_and_invalid_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcomes: list[str], expected: str
) -> None:
    from tests.graph_eval import worker

    source = tmp_path / "source"
    source.mkdir()
    output = tmp_path / "output"
    commands: list[tuple[str, ...]] = []
    containers: dict[str, dict[str, Any]] = {}
    starts: list[str] = []
    counter = 0

    def docker(_context: str, *arguments: str, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal counter
        commands.append(arguments)
        stdout = ""
        if arguments[:2] == ("image", "inspect"):
            stdout = "sha256:test-pinned-image"
        elif arguments[0] == "create":
            counter += 1
            stdout = f"worker-{counter}"
            inspected = _inspection()
            network = arguments[arguments.index("--network") + 1]
            inspected["HostConfig"]["NetworkMode"] = network
            inspected["NetworkSettings"]["Networks"] = {network: {}}
            inspected["State"] = {"ExitCode": 0}
            containers[stdout] = inspected
        elif arguments[0] == "inspect":
            stdout = json.dumps([containers[arguments[1]]])
        return subprocess.CompletedProcess(arguments, 0, stdout, "")

    def execute(arguments: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        starts.append(arguments[-1])
        envelope = json.loads(kwargs["input"])
        assert envelope == {
            "auth": {"auth_mode": "apikey", "OPENAI_API_KEY": "unit-test-placeholder"},
            "prompt": "read source",
        }
        assert "unit-test-placeholder" not in str(arguments)
        assert "OPENAI_API_KEY" not in kwargs["env"]
        assert "CODEX_API_KEY" not in kwargs["env"]
        outcome = outcomes[len(starts) - 1]
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(arguments, 10)
        attempt = Path(kwargs["stdout"].name).parent
        response = {"answer": "source-derived"} if outcome == "ok" else {"unexpected": True}
        (attempt / "response.json").write_text(json.dumps(response))
        kwargs["stdout"].write('{"type":"turn.completed","usage":{"output_tokens":7}}\n')
        return subprocess.CompletedProcess(arguments, int(outcome == "transport_error"))

    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-placeholder")
    monkeypatch.setattr(worker, "_docker", docker)
    monkeypatch.setattr(worker, "_docker_executable", lambda: "/unit-test/docker")
    monkeypatch.setattr(worker.subprocess, "run", execute)
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    result = worker.run_agent(source, "read source", schema, output)
    assert result["status"] == expected
    assert len(starts) == len(outcomes)
    assert [attempt["status"] for attempt in result["attempts"]] == outcomes
    assert all(
        attempt["usage"] == {"output_tokens": 7}
        for attempt in result["attempts"]
        if attempt["status"] != "timeout"
    )
    assert len({attempt["isolation"]["internal_network"] for attempt in result["attempts"]}) == len(
        outcomes
    )
    assert all(("rm", "--force", container) in commands for container in starts)
    assert all("/secrets" not in str(command) for command in commands)
    assert all("unit-test-placeholder" not in str(command) for command in commands)
    assert not list(tmp_path.glob(".worker-auth-*"))
    assert all(
        "unit-test-placeholder" not in path.read_text()
        for path in tmp_path.rglob("*")
        if path.is_file()
    )
    assert (output / "response.json").exists() is (expected == "ok")
    assert not list(output.glob("*.tmp"))


@pytest.mark.parametrize("failure", ["missing", "timeout", "unavailable", "invalid_id"])
def test_prelaunch_image_failure_is_terminal_without_provider_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from tests.graph_eval import worker

    source = tmp_path / "source"
    source.mkdir()
    output = tmp_path / "output"
    calls = []

    def docker(_context: str, *arguments: str, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        if failure == "missing":
            raise subprocess.CalledProcessError(1, ["docker", *arguments])
        if failure == "timeout":
            raise subprocess.TimeoutExpired(["docker", *arguments], 30)
        if failure == "unavailable":
            raise OSError("Docker is unavailable")
        return subprocess.CompletedProcess(arguments, 0, "unresolved-image", "")

    def auth() -> dict[str, Any]:
        raise AssertionError("A prelaunch failure must not access credentials")

    monkeypatch.setattr(worker, "_docker", docker)
    monkeypatch.setattr(worker, "_auth_payload", auth)
    result = worker.run_agent(source, "read source", {}, output)
    assert result["status"] == "isolation_error"
    assert result["attempts"] == []
    assert result["response_path"] is None
    assert json.loads((output / "worker.json").read_text()) == result
    assert len(calls) == 1
    assert calls[0][:2] == ("image", "inspect")
    assert not list(output.glob("attempt-*"))


def test_builds_keep_unique_names_and_digest_retention_tags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.graph_eval import worker

    calls = []
    image = "sha256:" + "a" * 64

    def docker(_context: str, *arguments: str, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        stdout = image if arguments[:2] == ("image", "inspect") else ""
        return subprocess.CompletedProcess(arguments, 0, stdout, "")

    monkeypatch.setattr(worker, "_docker", docker)
    first = worker.build_image(tmp_path / "first")
    second = worker.build_image(tmp_path / "second")
    assert first["build_tag"] != second["build_tag"]
    assert first["retention_tag"].endswith("sha256-" + "a" * 64)
    assert ("image", "tag", image, first["retention_tag"]) in calls
    assert all(worker.DEFAULT_IMAGE not in arguments for arguments in calls)


def test_orchestrator_freezes_prelaunch_failure_instead_of_leaving_running_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.graph_eval import campaign, worker

    source = tmp_path / "source-views" / "case"
    source.mkdir(parents=True)
    (source / "module.py").write_text("pass\n")
    manifest = {
        "worker_image": "sha256:unavailable",
        "worker_fingerprint": "frozen",
        "model": "gpt-6.1-sol",
        "reasoning": "xhigh",
        "docker_context": "isolated",
        "source_views": {"case": campaign._view_hash(source)},
        "limits": {"reader_timeout": 900},
    }
    calls = []

    def docker(_context: str, *arguments: str, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        raise subprocess.CalledProcessError(1, ["docker", *arguments])

    monkeypatch.setattr(worker, "_docker", docker)
    monkeypatch.setattr(campaign, "run_agent", worker.run_agent)
    case = {"id": "case", "input_hash": "frozen-input"}
    result = campaign._agent_job(tmp_path, manifest, case, "reader-A", "read source", {})
    assert result["status"] == "isolation_error"
    assert result["worker"]["attempts"] == []
    path = tmp_path / "cases" / "case" / "reader-A" / "job.json"
    assert json.loads(path.read_text()) == result
    assert campaign._agent_job(tmp_path, manifest, case, "reader-A", "read source", {}) == result
    assert len(calls) == 1
