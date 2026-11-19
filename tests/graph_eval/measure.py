"""Measure an installed Trailmark build without importing the harness checkout.

The parent starts this file with ``python -I``.  The worker consequently imports
Trailmark from that interpreter's environment, never from a target repository or
the evaluator's source directory.  Model snapshots supplement the public JSON
export, which intentionally omits edge locations.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import time
import traceback
from enum import Enum
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
PACKAGES = (
    "trailmark",
    "tree-sitter",
    "tree-sitter-language-pack",
    "tree-sitter-sql",
    "rustworkx",
    "numpy",
)


def digest(value: Any) -> str:
    """Hash JSON data without depending on object key insertion order."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _file_digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def _json_value(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _json_value(dataclasses.asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_json_value(item) for item in value), key=lambda item: repr(item))
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Unsupported measurement value: {type(value).__name__}")


def normalize_paths(value: Any, root: Path, *, key: str = "") -> Any:
    """Normalize path fields only; identifiers and source text remain unchanged."""
    if isinstance(value, dict):
        return {name: normalize_paths(item, root, key=name) for name, item in value.items()}
    if isinstance(value, list):
        return [normalize_paths(item, root, key=key) for item in value]
    if isinstance(value, str) and (key in {"path", "root"} or key.endswith("_path")):
        try:
            return Path(value).relative_to(root).as_posix()
        except ValueError:
            return value
    return value


def snapshot(graph: Any, root: Path) -> dict[str, Any]:
    """Retain every model field, using the public edge endpoint vocabulary."""
    result = _json_value(graph)
    for node in result["nodes"].values():
        node["attributes"] = dict(node["attributes"])
    for edge in result["edges"]:
        edge["source"] = edge.pop("source_id")
        edge["target"] = edge.pop("target_id")
        edge["attributes"] = dict(edge["attributes"])
    result["schema_version"] = SCHEMA_VERSION
    return normalize_paths(result, root)


def source_inventory(root: Path) -> dict[str, Any]:
    """Census source independently of Trailmark's discovery and syntax trees."""
    git = subprocess.run(  # noqa: S603
        ["git", "-C", str(root), "ls-files", "-z"],  # noqa: S607
        capture_output=True,
        check=False,
    )
    if git.returncode == 0:
        names = sorted(os.fsdecode(name) for name in git.stdout.split(b"\0") if name)
        method = "git-tracked"
        head = subprocess.run(  # noqa: S603
            ["git", "-C", str(root), "rev-parse", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
        revision = head.stdout.strip() if head.returncode == 0 else None
    else:
        names = sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() or path.is_symlink()
        )
        method, revision = "filesystem", None
    files = []
    for name in names:
        path = root / name
        if path.is_symlink():
            target = os.readlink(path)
            files.append(
                {
                    "path": name,
                    "kind": "symlink",
                    "target": target,
                    "sha256": hashlib.sha256(os.fsencode(target)).hexdigest(),
                }
            )
        elif path.is_file():
            files.append(
                {
                    "path": name,
                    "kind": "file",
                    "bytes": path.stat().st_size,
                    "sha256": _file_digest(path),
                }
            )
        else:
            files.append({"path": name, "kind": "missing", "sha256": None})
    return {"method": method, "revision": revision, "files": files, "tree_sha256": digest(files)}


def runtime_fingerprint(language: str) -> dict[str, Any]:
    """Record installed Python code and applicable grammar artifacts by content."""
    import tree_sitter_language_pack

    import trailmark

    package_root = Path(trailmark.__file__).resolve().parent
    source = {
        p.relative_to(package_root).as_posix(): _file_digest(p)
        for p in sorted(package_root.rglob("*.py"))
    }
    custom = package_root / "tree_sitter_custom"
    grammar_names = {language}
    if language == "rust":
        grammar_names.add("verus")
    if language == "typescript":
        grammar_names.add("tsx")
    grammars = {}
    for name in sorted(grammar_names):
        directory = custom / name
        if directory.is_dir():
            for shared in ("binding.c", "nested_comment.h"):
                path = custom / shared
                if path.is_file():
                    grammars[path.relative_to(package_root).as_posix()] = _file_digest(path)
            for path in sorted(directory.rglob("*")):
                if path.is_file() and path.suffix in {
                    ".c",
                    ".h",
                    ".js",
                    ".json",
                    ".so",
                    ".pyd",
                    ".dylib",
                }:
                    grammars[path.relative_to(package_root).as_posix()] = _file_digest(path)
    cache = Path(tree_sitter_language_pack.cache_dir())
    for name in sorted(grammar_names):
        for path in sorted(cache.glob(f"*tree_sitter_{name}.*")):
            if path.is_file():
                grammars[f"language-pack-cache/{path.name}"] = _file_digest(path)
    if language == "sql":
        import tree_sitter_sql

        sql_root = Path(tree_sitter_sql.__file__).resolve().parent
        for path in sorted(sql_root.rglob("*")):
            if path.is_file() and path.suffix in {".c", ".h", ".so", ".pyd", ".dylib"}:
                grammars[f"tree_sitter_sql/{path.relative_to(sql_root).as_posix()}"] = _file_digest(
                    path
                )
    packages = {}
    for name in PACKAGES:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {
        "python": platform.python_version(),
        "executable": sys.executable,
        "platform": platform.platform(),
        "packages": packages,
        "trailmark_source_sha256": digest(source),
        "trailmark_module_path": str(package_root),
        "trailmark_source_files": source,
        "grammar_files": grammars,
        "grammar_sha256": digest(grammars),
    }


def _run_queries(engine: Any, queries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    methods = {
        name: getattr(engine, name)
        for name in (
            "summary",
            "attack_surface",
            "call_resolution",
            "parse_diagnostics",
            "unresolved_calls",
            "callers_of",
            "callees_of",
            "paths_between",
            "ancestors_of",
            "reachable_from",
            "entrypoint_paths_to",
            "type_references",
            "state_read_by",
            "state_written_by",
        )
    }
    answers = []
    for question in queries:
        operation = question["operation"]
        try:
            if operation not in methods:
                raise ValueError(f"Unsupported read-only query: {operation}")
            answer = methods[operation](**question.get("arguments", {}))
            answers.append(
                {
                    "id": question["id"],
                    "operation": operation,
                    "status": "ok",
                    "answer": _json_value(answer),
                }
            )
        except (TypeError, ValueError, KeyError) as exc:
            answers.append(
                {
                    "id": question["id"],
                    "operation": operation,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return answers


def collect_measurement(
    root: Path, language: str, queries: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Measure raw parsing and the default public facade independently."""
    from trailmark.parse import detect_languages, parse_directory
    from trailmark.query.api import QueryEngine

    root = root.resolve()
    before = source_inventory(root)
    raw = parse_directory(str(root), language=language)
    raw_graph = snapshot(raw, root)
    engine = QueryEngine.from_directory(str(root), language=language)
    # This read-only observation is deliberate: public JSON omits call-site spans.
    public_model = engine._store._graph  # noqa: SLF001
    observed = snapshot(public_model, root)
    public_export = normalize_paths(json.loads(engine.to_json()), root)
    questions = (
        queries
        if queries is not None
        else [
            {"id": name, "operation": name, "arguments": {}}
            for name in ("summary", "call_resolution", "attack_surface")
        ]
    )
    answers = normalize_paths(_run_queries(engine, questions), root)
    second = QueryEngine.from_directory(str(root), language=language)
    repeated = snapshot(second._store._graph, root)  # noqa: SLF001
    hashes = [digest(observed), digest(repeated)]
    after = source_inventory(root)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if hashes[0] == hashes[1] and before == after else "invalid",
        "language": language,
        "source_inventory": before,
        "source_unchanged": before == after,
        "raw_graph": raw_graph,
        "graph": observed,
        "public_export": public_export,
        "auto_detection": {
            "languages": detect_languages(str(root)),
            "scope": "discovery only; explicit language is the primary measurement",
        },
        "diagnostics": normalize_paths(engine.parse_diagnostics(), root),
        "unresolved_calls": normalize_paths(engine.unresolved_calls(), root),
        "query_answers": answers,
        "runtime": runtime_fingerprint(language),
        "repeated_graph_hashes": hashes,
        "deterministic": hashes[0] == hashes[1],
    }


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def measure_project(
    project_root: Path,
    language: str,
    destination: Path,
    *,
    python: Path | None = None,
    timeout: int = 1200,
    queries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run one isolated worker and preserve failure status, stderr, and exit code."""
    destination.mkdir(parents=True, exist_ok=True)
    result_path = destination / "measurement.json"
    # Never accept a stale successful artifact after a failed rerun.
    result_path.unlink(missing_ok=True)
    queries_path = destination / "queries.json"
    _write_json(queries_path, queries)
    command = [
        str(python or Path(sys.executable)),
        "-I",
        str(Path(__file__).resolve()),
        "--worker",
        "--root",
        str(project_root.resolve()),
        "--language",
        language,
        "--output",
        str(result_path.resolve()),
        "--queries",
        str(queries_path.resolve()),
    ]
    started = time.monotonic()
    measurement: dict[str, Any]
    environment = os.environ.copy()
    for key in (
        "PYTHONPATH",
        "PYTHONHOME",
        "ACTIVE_GREMLIN",
        "PYTEST_GREMLINS_SOURCES_FILE",
        "GREMLIN_ROOTDIR",
        "TRAILMARK_UPDATE_SNAPSHOTS",
    ):
        environment.pop(key, None)
    try:
        with (
            (destination / "stdout.log").open("w") as stdout,
            (destination / "stderr.log").open("w") as stderr,
        ):
            result = subprocess.run(  # noqa: S603
                command,
                cwd=destination,
                env=environment,
                stdout=stdout,
                stderr=stderr,
                check=False,
                timeout=timeout,
            )
        if result.returncode == 0 and result_path.is_file():
            measurement = json.loads(result_path.read_text())
        else:
            measurement = {
                "schema_version": SCHEMA_VERSION,
                "status": "crashed",
                "language": language,
                "returncode": result.returncode,
                "error": "Measurement worker failed; see stderr.log",
            }
    except subprocess.TimeoutExpired:
        measurement = {
            "schema_version": SCHEMA_VERSION,
            "status": "timeout",
            "language": language,
            "timeout_seconds": timeout,
        }
    except OSError as exc:
        measurement = {
            "schema_version": SCHEMA_VERSION,
            "status": "error",
            "language": language,
            "error": f"{type(exc).__name__}: {exc}",
        }
    measurement["elapsed_seconds"] = round(time.monotonic() - started, 3)
    measurement["worker_command"] = command
    _write_json(result_path, measurement)
    return measurement


def query_baseline(
    measurement: dict[str, Any], *, expected_graph_hash: str, expected_runtime_hash: str
) -> dict[str, Any]:
    """Bind navigation evidence to the complete original graph and runtime fingerprint."""
    graph_hash = digest(measurement["graph"]) if "graph" in measurement else None
    runtime = measurement.get("runtime")
    runtime_hash = digest(runtime) if isinstance(runtime, dict) and runtime else None
    return {
        "expected_graph_sha256": expected_graph_hash,
        "observed_graph_sha256": graph_hash,
        "graph_identical": graph_hash == expected_graph_hash,
        "expected_runtime_sha256": expected_runtime_hash,
        "observed_runtime_sha256": runtime_hash,
        "runtime_identical": runtime_hash == expected_runtime_hash,
        "scope": "Public API queries from a repeated parse of the frozen source and runtime",
    }


def query_project(
    project_root: Path,
    language: str,
    destination: Path,
    queries: list[dict[str, Any]],
    *,
    expected_graph_hash: str,
    expected_runtime_hash: str,
    python: Path | None = None,
    timeout: int = 1200,
) -> dict[str, Any]:
    """Rebuild with the frozen interpreter and query only an identical graph.

    Trailmark has no lossless public graph loader. Repeating analysis preserves
    the real public API behavior; the full normalized graph must match the
    original observation before these answers can count as navigation evidence.
    Runtime equality covers every recorded interpreter, package, source, and
    grammar fingerprint field; an identical graph alone is insufficient.
    """
    result = measure_project(
        project_root, language, destination, python=python, timeout=timeout, queries=queries
    )
    baseline = query_baseline(
        result, expected_graph_hash=expected_graph_hash, expected_runtime_hash=expected_runtime_hash
    )
    result["query_baseline"] = baseline
    mismatches = [label for label in ("graph", "runtime") if not baseline[f"{label}_identical"]]
    if result["status"] == "ok" and mismatches:
        result["status"] = "invalid"
        result["error"] = (
            f"Query {' and '.join(mismatches)} differs from the original frozen measurement"
        )
    _write_json(destination / "measurement.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true", required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--language", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--queries", type=Path)
    args = parser.parse_args()
    try:
        questions = json.loads(args.queries.read_text()) if args.queries else None
        measurement = collect_measurement(args.root, args.language, questions)
        _write_json(args.output, measurement)
    except Exception:  # An uncaught worker error must retain its complete traceback.
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
