"""Fresh Codex sessions with only a read-only source interface and provider egress."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import time
import tomllib
import uuid
from pathlib import Path
from typing import Any

from tests.graph_eval.schema import validate_schema

CLI_VERSION = "0.170.0"
DEFAULT_IMAGE = f"trailmark-graph-eval-worker:{CLI_VERSION}"
DEFAULT_CONTEXT = "colima"
BLOCKED_COMPONENTS = frozenset({".git", ".codex", ".agents", "AGENTS.md", "AGENTS.override.md"})
HELPERS = (
    "worker.Dockerfile",
    "worker_launch.mjs",
    "network_proxy.mjs",
    "source_reader.mjs",
    "worker_probe.mjs",
)


def worker_fingerprint() -> str:
    """Invalidate completed jobs when the trusted worker implementation changes."""
    digest = hashlib.sha256()
    for name in ("worker.py", *HELPERS):
        digest.update(name.encode())
        digest.update(Path(__file__).with_name(name).read_bytes())
    return digest.hexdigest()


def model_defaults() -> dict[str, str]:
    """Read only the relevant settings; never export a user's complete configuration."""
    config_path = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"
    config = tomllib.loads(config_path.read_text()) if config_path.is_file() else {}
    return {
        "model": str(config.get("model", "gpt-6.1-sol")),
        "reasoning": str(config.get("model_reasoning_effort", "xhigh")),
    }


def _docker_executable() -> str:
    executable = shutil.which("docker")
    if executable is None:
        raise ValueError("Docker CLI is required; host agent execution is forbidden")
    return executable


def _docker_environment() -> dict[str, str]:
    """Docker subprocesses receive no provider credential environment variables."""
    return {
        key: value
        for key, value in os.environ.items()
        if key not in {"OPENAI_API_KEY", "CODEX_API_KEY"}
    }


def _docker(context: str, *arguments: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [_docker_executable(), "--context", context, *arguments],
        text=True,
        capture_output=True,
        check=True,
        env=_docker_environment(),
        **kwargs,
    )


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def build_image(output_dir: Path, *, context: str = DEFAULT_CONTEXT) -> dict[str, str]:
    """Build from pinned inputs and return the immutable local image ID."""
    output_dir.mkdir(parents=True, exist_ok=True)
    result = _docker(
        context,
        "build",
        "--tag",
        DEFAULT_IMAGE,
        "--file",
        str(Path(__file__).with_name("worker.Dockerfile")),
        str(Path(__file__).parent),
        timeout=600,
    )
    (output_dir / "image-build.log").write_text(result.stdout + result.stderr)
    image = _docker(context, "image", "inspect", DEFAULT_IMAGE, "--format", "{{.Id}}")
    metadata = {
        "image": image.stdout.strip(),
        "cli_version": CLI_VERSION,
        "worker_fingerprint": worker_fingerprint(),
        "docker_context": context,
    }
    _atomic_json(output_dir / "image.json", metadata)
    return metadata


def validate_source_view(source_root: Path, output_dir: Path) -> None:
    """Reject metadata, escaping links, and output directories visible to a reader."""
    root, output = source_root.resolve(), output_dir.resolve()
    if not root.is_dir():
        raise ValueError("Source view is not a directory")
    if output == root or root in output.parents:
        raise ValueError("Worker outputs must be outside the source view")
    for entry in root.rglob("*"):
        if any(part in BLOCKED_COMPONENTS for part in entry.relative_to(root).parts):
            raise ValueError(f"Operational metadata in source view: {entry.relative_to(root)}")
        if entry.is_symlink():
            raise ValueError(f"Source view must not contain symlinks: {entry.relative_to(root)}")


def codex_arguments(model: str, reasoning: str) -> list[str]:
    """The only model-facing filesystem tools are the trusted source MCP server."""
    return [
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "--json",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--cd",
        "/source",
        "--model",
        model,
        "--output-schema",
        "/input/schema.json",
        "--output-last-message",
        "/output/response.json",
        "-c",
        f"model_reasoning_effort={json.dumps(reasoning)}",
        "-c",
        'approval_policy="never"',
        "-c",
        "project_doc_max_bytes=0",
        "-c",
        'web_search="disabled"',
        "-c",
        "features.shell_tool=false",
        "-c",
        "features.view_image=false",
        "-c",
        "features.multi_agent=false",
        "-c",
        "features.multi_agent_v2=false",
        "-c",
        "features.plugins=false",
        "-c",
        "features.remote_plugin=false",
        "-c",
        "features.skill_search=false",
        "-c",
        "features.skill_mcp_dependency_install=false",
        "-c",
        "features.skip_host_skill_discovery=true",
        "-c",
        'mcp_servers.source.command="node"',
        "-c",
        'mcp_servers.source.args=["/opt/graph-eval/source_reader.mjs"]',
        "-c",
        'mcp_servers.source.env={SOURCE_ROOT="/source",SOURCE_AUDIT="/output/source-reads.jsonl"}',
        "-c",
        "mcp_servers.source.required=true",
        "-c",
        'mcp_servers.source.enabled_tools=["source_list","source_read","source_search"]',
        "-",
    ]


class _Network:
    def __init__(self, context: str, image: str) -> None:
        self.context, self.image = context, image
        self.containers: list[str] = []
        suffix = uuid.uuid4().hex[:16]
        self.name, self.proxy = f"graph-eval-{suffix}", f"graph-eval-proxy-{suffix}"

    def __enter__(self) -> _Network:
        _docker(self.context, "network", "create", "--internal", self.name)
        try:
            _docker(
                self.context,
                "run",
                "--detach",
                "--name",
                self.proxy,
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--user",
                "65534:65534",
                "--entrypoint",
                "node",
                self.image,
                "/opt/graph-eval/network_proxy.mjs",
            )
            _docker(self.context, "network", "connect", "--alias", "egress", self.name, self.proxy)
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *_: object) -> None:
        for arguments in (
            *(("rm", "--force", item) for item in self.containers),
            ("rm", "--force", self.proxy),
            ("network", "rm", self.name),
        ):
            with contextlib.suppress(subprocess.SubprocessError):
                _docker(self.context, *arguments, timeout=30)


def _container_arguments(network: _Network, source: Path, inputs: Path, output: Path) -> list[str]:
    uid, gid = os.getuid(), os.getgid()
    return [
        "--interactive",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pids-limit",
        "128",
        "--memory",
        "2g",
        "--cpus",
        "2",
        "--user",
        f"{uid}:{gid}",
        "--network",
        network.name,
        "--tmpfs",
        f"/runtime:rw,nosuid,nodev,uid={uid},gid={gid},mode=0700,size=256m",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,mode=1777,size=128m",  # noqa: S108 - private container tmpfs
        "--mount",
        f"type=bind,src={source},dst=/source,readonly",
        "--mount",
        f"type=bind,src={inputs},dst=/input,readonly",
        "--mount",
        f"type=bind,src={output},dst=/output",
        "--env",
        "HTTPS_PROXY=http://egress:8080",
        "--env",
        "HTTP_PROXY=http://egress:8080",
        "--env",
        "https_proxy=http://egress:8080",
        "--env",
        "http_proxy=http://egress:8080",
        "--env",
        "NO_PROXY=",
        "--env",
        "no_proxy=",
    ]


def _auth_payload() -> dict[str, Any]:
    """Keep credentials in memory until they enter the worker's private stdin."""
    if key := os.environ.get("OPENAI_API_KEY"):
        return {"auth_mode": "apikey", "OPENAI_API_KEY": key}
    source = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "auth.json"
    if not source.is_file():
        raise ValueError("Codex automation requires OPENAI_API_KEY or an existing auth.json")
    data = json.loads(source.read_text())
    mode = data.get("auth_mode", "chatgpt" if data.get("tokens") else "apikey")
    if mode == "apikey" and isinstance(data.get("OPENAI_API_KEY"), str) and data["OPENAI_API_KEY"]:
        return {"auth_mode": mode, "OPENAI_API_KEY": data["OPENAI_API_KEY"]}
    if mode == "chatgpt" and isinstance(data.get("tokens"), dict) and data["tokens"]:
        return {
            name: data[name] for name in ("auth_mode", "tokens", "last_refresh") if name in data
        } | {"auth_mode": mode}
    raise ValueError("Existing Codex authentication is unsupported or incomplete")


def _check_container(inspected: dict[str, Any], network: str) -> dict[str, Any]:
    host = inspected["HostConfig"]
    mounts = inspected["Mounts"]
    if not host["ReadonlyRootfs"] or host["Privileged"] or host["NetworkMode"] != network:
        raise ValueError("Worker container isolation differs from the required policy")
    if set(inspected["NetworkSettings"]["Networks"]) != {network}:
        raise ValueError("Worker has an unexpected network attachment")
    expected = {"/source": False, "/input": False, "/output": True}
    found = {mount["Destination"]: mount["RW"] for mount in mounts if mount["Type"] == "bind"}
    if found != expected:
        raise ValueError("Worker has unexpected host mounts")
    runtime = set(host.get("Tmpfs", {}).get("/runtime", "").split(","))
    if not {"rw", "nosuid", "nodev", "mode=0700"} <= runtime:
        raise ValueError("Worker credentials require a private runtime tmpfs")
    if any(
        item.split("=", 1)[0] in {"OPENAI_API_KEY", "CODEX_API_KEY"}
        for item in inspected["Config"].get("Env", [])
    ):
        raise ValueError("Worker must not receive credential environment variables")
    if not any("no-new-privileges" in option for option in host.get("SecurityOpt", [])):
        raise ValueError("Worker allows privilege escalation")
    if "ALL" not in host.get("CapDrop", []):
        raise ValueError("Worker capabilities were not dropped")
    return {
        "read_only_root": True,
        "mounts": found,
        "internal_network": network,
        "only_source_tools": True,
        "provider_proxy": True,
        "credentials": "stdin_to_private_tmpfs",
    }


def _usage(events: Path) -> dict[str, int]:
    for line in reversed(events.read_text(errors="replace").splitlines()):
        with contextlib.suppress(json.JSONDecodeError):
            event = json.loads(line)
            if event.get("type") == "turn.completed":
                return event.get("usage", {})
    return {}


def run_agent(
    source_root: Path,
    prompt: str,
    schema: dict[str, Any],
    output_dir: Path,
    model: str = "gpt-6.1-sol",
    reasoning: str = "xhigh",
    role: str = "reader",
    timeout: int = 900,
    *,
    context: str = DEFAULT_CONTEXT,
    image: str = DEFAULT_IMAGE,
) -> dict[str, Any]:
    """Run at most two fresh attempts, retrying transport/shape failures only."""
    source_root, output_dir = source_root.resolve(), output_dir.resolve()
    validate_source_view(source_root, output_dir)
    if timeout <= 0:
        raise ValueError("Worker timeout must be positive")
    output_dir.mkdir(parents=True, exist_ok=True)
    image_id = _docker(
        context, "image", "inspect", image, "--format", "{{.Id}}", timeout=30
    ).stdout.strip()
    if not image_id.startswith("sha256:"):
        raise ValueError("Worker image did not resolve to an immutable image ID")
    attempts = []
    for number in (1, 2):
        attempt = output_dir / f"attempt-{number}"
        attempt.mkdir()
        inputs = attempt / "input"
        inputs.mkdir()
        (inputs / "schema.json").write_text(json.dumps(schema))
        (inputs / "prompt.txt").write_text(prompt)
        start = time.monotonic()
        metadata: dict[str, Any] = {
            "attempt": number,
            "role": role,
            "model": model,
            "reasoning": reasoning,
            "image": image_id,
            "worker_fingerprint": worker_fingerprint(),
        }
        container = None
        try:
            with _Network(context, image_id) as network:
                arguments = _container_arguments(network, source_root, inputs, attempt)
                container = _docker(
                    context,
                    "create",
                    *arguments,
                    image_id,
                    *codex_arguments(model, reasoning),
                    timeout=30,
                ).stdout.strip()
                network.containers.append(container)
                inspected = json.loads(_docker(context, "inspect", container, timeout=30).stdout)[0]
                metadata["isolation"] = _check_container(inspected, network.name)
                # Corpus source is never executed; source reads are audited separately.
                with (
                    (attempt / "events.jsonl").open("w") as stdout,
                    (attempt / "stderr.log").open("w") as stderr,
                ):
                    result = subprocess.run(  # noqa: S603
                        [
                            _docker_executable(),
                            "--context",
                            context,
                            "start",
                            "--attach",
                            "--interactive",
                            container,
                        ],
                        input=json.dumps({"auth": _auth_payload(), "prompt": prompt}),
                        text=True,
                        stdout=stdout,
                        stderr=stderr,
                        check=False,
                        env=_docker_environment(),
                        timeout=timeout,
                    )
                exit_code = int(
                    json.loads(_docker(context, "inspect", container, timeout=30).stdout)[0][
                        "State"
                    ]["ExitCode"]
                )
                if result.returncode or exit_code:
                    metadata.update(status="transport_error", exit_code=exit_code)
                else:
                    try:
                        data = json.loads((attempt / "response.json").read_text())
                        validate_schema(data, schema)
                        metadata["status"] = "ok"
                        _atomic_json(output_dir / "response.json", data)
                    except (ValueError, OSError) as error:
                        metadata.update(status="invalid_output", error=str(error))
        except subprocess.TimeoutExpired:
            metadata["status"] = "timeout"
        except (ValueError, subprocess.SubprocessError, OSError) as error:
            metadata.update(status="isolation_error", error=str(error))
        finally:
            if container:
                with contextlib.suppress(subprocess.SubprocessError):
                    _docker(context, "rm", "--force", container, timeout=30)
        metadata["elapsed_seconds"] = round(time.monotonic() - start, 3)
        metadata["usage"] = (
            _usage(attempt / "events.jsonl") if (attempt / "events.jsonl").exists() else {}
        )
        attempts.append(metadata)
        _atomic_json(attempt / "metadata.json", metadata)
        if metadata["status"] not in {"transport_error", "invalid_output"}:
            break
    result = {
        "status": attempts[-1]["status"],
        "role": role,
        "attempts": attempts,
        "response_path": str(output_dir / "response.json")
        if attempts[-1]["status"] == "ok"
        else None,
    }
    _atomic_json(output_dir / "worker.json", result)
    return result


def preflight(
    output_dir: Path,
    *,
    context: str = DEFAULT_CONTEXT,
    image: str = DEFAULT_IMAGE,
    model: str = "gpt-6.1-sol",
    reasoning: str = "xhigh",
    live: bool = False,
) -> dict[str, Any]:
    """Fail closed unless filesystem, source-tool and network boundaries hold."""
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    source, inputs, outputs = (
        output_dir / name for name in ("probe-source", "probe-input", "probe-output")
    )
    for path in (source, inputs, outputs):
        path.mkdir(exist_ok=True)
    (source / "control.txt").write_text("source-view-control\n")
    image_id = _docker(
        context, "image", "inspect", image, "--format", "{{.Id}}", timeout=30
    ).stdout.strip()
    report: dict[str, Any] = {
        "status": "failed",
        "image": image_id,
        "worker_fingerprint": worker_fingerprint(),
    }
    try:
        with _Network(context, image_id) as network:
            container = _docker(
                context,
                "create",
                *_container_arguments(network, source, inputs, outputs),
                "--entrypoint",
                "node",
                image_id,
                "/opt/graph-eval/worker_probe.mjs",
                timeout=30,
            ).stdout.strip()
            network.containers.append(container)
            inspected = json.loads(_docker(context, "inspect", container, timeout=30).stdout)[0]
            _check_container(inspected, network.name)
            result = _docker(context, "start", "--attach", container, timeout=60)
            (output_dir / "probe.log").write_text(result.stdout + result.stderr)
            checks = json.loads(result.stdout.strip().splitlines()[-1])
            if checks.get("cli_version") != f"codex-cli {CLI_VERSION}":
                raise ValueError("Worker CLI version differs from pinned version")
            if not all(value is True for key, value in checks.items() if key != "cli_version"):
                raise ValueError("Worker boundary probe failed")
            report["checks"] = checks
        if live:
            canary = uuid.uuid4().hex
            (source / "canary.txt").write_text(canary + "\n")
            schema = {
                "type": "object",
                "additionalProperties": False,
                "properties": {"canary": {"type": "string"}},
                "required": ["canary"],
            }
            result = run_agent(
                source,
                "This is an isolation control, not a graph evaluation. Use the source_read tool "
                "to read line 1 of canary.txt. Return its exact value in the canary field. "
                "Do not guess the value; it is not in these instructions.",
                schema,
                output_dir / "live-control",
                model,
                reasoning,
                "preflight",
                180,
                context=context,
                image=image_id,
            )
            if result["status"] != "ok":
                raise ValueError(f"Live worker control failed: {result['status']}")
            response = json.loads(Path(result["response_path"]).read_text())
            if response.get("canary") != canary:
                raise ValueError("Live worker did not read its source view")
            report["live_control"] = result
        report["status"] = "ok"
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        report["error"] = str(error)
    _atomic_json(output_dir / "preflight.json", report)
    return report
