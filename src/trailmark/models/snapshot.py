"""Strict JSON snapshots of the typed graph, independent of lightweight exports."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import types
from copy import deepcopy
from dataclasses import fields, is_dataclass
from enum import Enum
from functools import cache
from pathlib import Path
from typing import (
    Any,
    Literal,
    TypeAliasType,
    Union,
    get_args,
    get_origin,
    get_type_hints,
    overload,
)

from trailmark import __version__
from trailmark.models.exceptions import ExceptionEvent
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit, NodeOrigin, SourceLocation

_FORMAT = "trailmark.analysis"
_VERSION = 1


def _encode(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _encode(getattr(value, field.name))
            for field in fields(value)
            if not field.name.startswith("_")
        }
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("Snapshot dictionary keys must be strings")
        return {key: _encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_encode(item) for item in (sorted(value) if isinstance(value, set) else value)]
    if value is None or type(value) in {str, int, bool}:
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError(f"Unsupported snapshot value: {type(value).__name__}")


@cache
def _hints(expected: type) -> dict[str, Any]:
    return get_type_hints(expected, localns={"ExceptionEvent": ExceptionEvent})


@overload
def _decode[T](
    value: object, expected: type[T], path: str, *, node_origin: str | None = None
) -> T: ...


@overload
def _decode(
    value: object, expected: object, path: str, *, node_origin: str | None = None
) -> object: ...


def _decode(
    value: object, expected: object, path: str, *, node_origin: str | None = None
) -> object:
    if isinstance(expected, TypeAliasType):
        return _decode(value, expected.__value__, path, node_origin=node_origin)
    if expected in {Any, object}:
        return _encode(value)
    origin, arguments = get_origin(expected), get_args(expected)
    if origin in {types.UnionType, Union}:
        for choice in arguments:
            try:
                return _decode(value, choice, path, node_origin=node_origin)
            except ValueError:
                pass
        raise ValueError(f"{path}: value does not match {expected}")
    if origin is Literal:
        if value not in arguments or any(type(value) is not type(item) for item in arguments):
            raise ValueError(f"{path}: invalid literal")
        return value
    if origin in {tuple, list, set}:
        if not isinstance(value, list):
            raise ValueError(f"{path}: expected an array")
        if origin is tuple and arguments[-1:] != (Ellipsis,):
            if len(value) != len(arguments):
                raise ValueError(f"{path}: wrong tuple length")
            decoded = [
                _decode(item, kind, path) for item, kind in zip(value, arguments, strict=True)
            ]
        else:
            decoded = [_decode(item, arguments[0], path) for item in value]
        if origin is set and len(set(decoded)) != len(decoded):
            raise ValueError(f"{path}: duplicate set members")
        return origin(decoded)
    if origin is dict:
        if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
            raise ValueError(f"{path}: expected an object with string keys")
        return {key: _decode(item, arguments[1], f"{path}.{key}") for key, item in value.items()}
    if isinstance(expected, type) and issubclass(expected, Enum):
        try:
            return expected(value)
        except (ValueError, TypeError) as error:
            raise ValueError(f"{path}: invalid {expected.__name__}") from error
    if isinstance(expected, type) and is_dataclass(expected):
        names = {field.name for field in fields(expected) if not field.name.startswith("_")}
        if not isinstance(value, dict) or set(value) != names:
            raise ValueError(f"{path}: missing or unknown {expected.__name__} fields")
        hints = _hints(expected)
        result = expected(
            **{
                key: _decode(
                    item,
                    hints[key],
                    f"{path}.{key}",
                    node_origin=value["origin"]
                    if expected is CodeUnit and key == "location"
                    else None,
                )
                for key, item in value.items()
            }
        )
        if isinstance(result, SourceLocation):
            minimum_line = (
                0
                if node_origin
                in {NodeOrigin.BINARY.value, NodeOrigin.PROXY.value, NodeOrigin.SYNTHETIC.value}
                and result.start_line == result.end_line == 0
                else 1
            )
            if (
                result.start_line < minimum_line
                or result.end_line < result.start_line
                or any(
                    column is not None and column < 0
                    for column in (result.start_col, result.end_col)
                )
                or (
                    result.start_line == result.end_line
                    and result.start_col is not None
                    and result.end_col is not None
                    and result.end_col < result.start_col
                )
            ):
                raise ValueError(f"{path}: invalid source location")
        return result
    if type(value) is expected and (not isinstance(value, float) or math.isfinite(value)):
        return value
    if expected is float and type(value) is int:
        return float(value)
    raise ValueError(f"{path}: expected {expected}")


def _git_revision(root: Path) -> str | None:
    git = shutil.which("git")
    if git is None:
        return None
    try:
        result = subprocess.run(  # noqa: S603 -- fixed read-only command without a shell
            [git, "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _tool_digest() -> str:
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _source_digest(graph: CodeGraph) -> str:
    return hashlib.sha256(
        json.dumps(graph.source_digests, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def dump_snapshot(
    graph: CodeGraph,
    *,
    source_revision: str | None,
    tool_revision: str | None,
) -> dict[str, object]:
    provenance: dict[str, object] = deepcopy(graph._snapshot_provenance)  # noqa: SLF001
    if not provenance:
        root = Path(graph.root_path)
        provenance = {
            "source_revision": source_revision
            or _git_revision(root.parent if root.is_file() else root),
            "tool_revision": tool_revision or _git_revision(Path(__file__).resolve().parents[3]),
            "tool_sha256": _tool_digest(),
        }
    if source_revision is not None:
        provenance["source_revision"] = source_revision
    if tool_revision is not None:
        provenance["tool_revision"] = tool_revision
    provenance["source_sha256"] = _source_digest(graph)
    provenance["unfingerprinted_sources"] = sorted(
        {
            unit.location.file_path
            for unit in graph.nodes.values()
            if unit.origin == NodeOrigin.SOURCE
        }
        - graph.source_digests.keys()
    )
    return {
        "format": _FORMAT,
        "snapshot_version": _VERSION,
        "trailmark_version": __version__,
        "provenance": provenance,
        "graph": _encode(graph),
        "unresolved_relationships": _encode(graph.unresolved_relationships()),
        "resolved_type_files": sorted(graph._parse_state.resolved_type_files),  # noqa: SLF001
    }


def load_snapshot(snapshot: dict[str, object], *, verify_sources: bool) -> CodeGraph:
    try:
        if not isinstance(snapshot, dict) or set(snapshot) != {
            "format",
            "snapshot_version",
            "trailmark_version",
            "provenance",
            "graph",
            "unresolved_relationships",
            "resolved_type_files",
        }:
            raise ValueError("Missing or unknown snapshot fields")
        if (
            snapshot["format"] != _FORMAT
            or type(snapshot["snapshot_version"]) is not int
            or snapshot["snapshot_version"] != _VERSION
        ):
            raise ValueError("Unsupported analysis snapshot format/version")
        if snapshot["trailmark_version"] != __version__:
            raise ValueError("Snapshot was produced by an incompatible Trailmark version")
        graph = _decode(snapshot["graph"], CodeGraph, "graph")
        _validate_graph(graph)
        if snapshot["unresolved_relationships"] != _encode(graph.unresolved_relationships()):
            raise ValueError("Unresolved relationship inventory does not match the graph")
        provenance = _decode(snapshot["provenance"], dict[str, object], "provenance")
        if set(provenance) != {
            "source_revision",
            "source_sha256",
            "unfingerprinted_sources",
            "tool_revision",
            "tool_sha256",
        }:
            raise ValueError("Invalid snapshot provenance")
        for name in ("source_revision", "tool_revision"):
            _decode(provenance[name], str | None, name)
        for name in ("source_sha256", "tool_sha256"):
            digest = _decode(provenance[name], str, name)
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError(f"Invalid {name}")
        if provenance["source_sha256"] != _source_digest(graph):
            raise ValueError("Source fingerprint does not match the graph")
        missing = _decode(
            provenance["unfingerprinted_sources"], list[str], "unfingerprinted_sources"
        )
        expected_missing = sorted(
            {
                unit.location.file_path
                for unit in graph.nodes.values()
                if unit.origin == NodeOrigin.SOURCE
            }
            - graph.source_digests.keys()
        )
        if missing != expected_missing:
            raise ValueError("Unfingerprinted source inventory does not match the graph")
        graph._snapshot_provenance = provenance  # noqa: SLF001
        graph._parse_state.resolved_type_files = _decode(  # noqa: SLF001
            snapshot["resolved_type_files"], set[str], "resolved_type_files"
        )
        if verify_sources:
            if missing:
                raise ValueError("Snapshot has sources without parse-time fingerprints")
            for path, digest in graph.source_digests.items():
                if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
                    raise ValueError(f"Source digest mismatch: {path}")
        return graph
    except (TypeError, KeyError, OSError, RecursionError) as error:
        raise ValueError(f"Invalid analysis snapshot: {error}") from error


def _validate_graph(graph: CodeGraph) -> None:
    if any(
        len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
        for digest in graph.source_digests.values()
    ):
        raise ValueError("Invalid source file digest")
    if any(count < 0 for count in graph.source_file_counts.values()):
        raise ValueError("Invalid source file count")
    for key, unit in graph.nodes.items():
        if key != unit.id:
            raise ValueError("Snapshot node key does not match its ID")
    for name in ("annotations", "entrypoints"):
        if set(getattr(graph, name)) - graph.nodes.keys():
            raise ValueError(f"Unknown node in {name}")
    if any(ids - graph.nodes.keys() for ids in graph.subgraphs.values()):
        raise ValueError("Unknown node in subgraph")
    for key, fact in graph.entrypoint_evidence.items():
        if key != fact.id or any(
            node is not None and node not in graph.nodes for node in (fact.node_id, fact.source_id)
        ):
            raise ValueError("Invalid entrypoint evidence identity or endpoint")
    for name in ("binary_artifacts", "binary_ranges", "binary_source_mappings"):
        if any(key != value.id for key, value in getattr(graph, name).items()):
            raise ValueError(f"Invalid {name} identity")
    for item in graph.binary_ranges.values():
        if (
            item.artifact_id not in graph.binary_artifacts
            or item.node_id not in graph.nodes
            or not 0 <= item.start < item.end
        ):
            raise ValueError("Invalid binary range or endpoint")
    for item in graph.binary_source_mappings.values():
        if item.range_id not in graph.binary_ranges or (
            item.source_node_id is not None and item.source_node_id not in graph.nodes
        ):
            raise ValueError("Invalid binary source mapping endpoint")
