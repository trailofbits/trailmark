"""Conservative source resolution and explicit, hash-bound byte mappings."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path

from trailmark.models.binary import BinarySelection, BinarySourceMapping
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind, NodeOrigin


@dataclass(frozen=True)
class ExplicitMapping:
    source_node_id: str
    selection: BinarySelection
    slice_index: int | None = None


def explicit_mappings(path: str | None, sha256: str, graph: CodeGraph) -> list[ExplicitMapping]:
    if path is None:
        return []
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or data.get("sha256") != sha256:
        raise ValueError("Explicit binary mapping SHA-256 does not match the artifact")
    rows = data.get("mappings")
    if not isinstance(rows, list):
        raise ValueError("Explicit binary mappings must be an array")
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Explicit binary mapping must be an object")
        node_id = row.get("source_node_id")
        node = graph.nodes.get(node_id) if isinstance(node_id, str) else None
        if node is None or node.origin != NodeOrigin.SOURCE:
            raise ValueError(f"Explicit binary mapping has an unknown source node: {node_id!r}")
        start, end = row.get("start"), row.get("end")
        if isinstance(start, str):
            start = int(start, 0)
        if isinstance(end, str):
            end = int(end, 0)
        selection = BinarySelection(start, end, row.get("address_kind", "va"))
        index = row.get("slice_index")
        if index is not None and (
            not isinstance(index, int) or isinstance(index, bool) or index < 0
        ):
            raise ValueError("Explicit mapping slice_index must be a nonnegative integer")
        result.append(ExplicitMapping(node.id, selection, index))
    return result


def _path(path: str, root: str) -> str:
    path = path.replace("\\", "/")
    if not path.startswith("/") and not re.match(r"^[A-Za-z]:/", path):
        path = os.path.join(root, path)
    path = os.path.normpath(path).replace("\\", "/")
    return path.casefold() if re.match(r"^[A-Za-z]:/", path) else path


def remap_path(path: str, prefixes: tuple[tuple[str, str], ...]) -> str:
    path = path.replace("\\", "/")
    for old, new in sorted(prefixes, key=lambda pair: -len(pair[0])):
        old = old.replace("\\", "/").rstrip("/")
        if path == old or path.startswith(old + "/"):
            return new.rstrip("/\\") + path[len(old) :]
    return path


class SourceResolver:
    def __init__(self, graph: CodeGraph, prefixes: tuple[tuple[str, str], ...]) -> None:
        self.root = os.path.abspath(graph.root_path or ".")
        self.prefixes = prefixes
        self.nodes = [
            node
            for node in graph.nodes.values()
            if node.origin == NodeOrigin.SOURCE
            and node.kind in (NodeKind.FUNCTION, NodeKind.METHOD)
        ]
        self.by_path: dict[str, list[str]] = {}
        self.by_symbol: dict[str, set[str]] = {}
        self.by_id = {node.id: node for node in self.nodes}
        for node in self.nodes:
            self.by_path.setdefault(_path(node.location.file_path, self.root), []).append(node.id)
            for alias in (node.id, node.name, node.id.rsplit(":", 1)[-1]):
                self.by_symbol.setdefault(alias, set()).add(node.id)

    def location(self, path: str, line: int) -> str | None:
        if line <= 0 or path in ("", "??"):
            return None
        path = _path(remap_path(path, self.prefixes), self.root)
        candidates = [
            self.by_id[node_id]
            for node_id in self.by_path.get(path, [])
            if self.by_id[node_id].location.start_line
            <= line
            <= self.by_id[node_id].location.end_line
        ]
        if not candidates:
            return None
        size = min(node.location.end_line - node.location.start_line for node in candidates)
        best = [
            node.id
            for node in candidates
            if node.location.end_line - node.location.start_line == size
        ]
        return best[0] if len(best) == 1 else None

    def symbol(self, names: tuple[str, ...], *, macho: bool = False) -> str | None:
        matches: set[str] = set()
        for name in names:
            aliases = {name, name.replace("::", ".")}
            # Remove a demangled parameter list, retaining operator() names.
            aliases.add(re.sub(r"\([^()]*\)(?: const)?$", "", name).replace("::", "."))
            if macho and name.startswith("_") and not name.startswith(("_Z", "_R")):
                aliases.add(name[1:])
            for alias in aliases:
                matches.update(self.by_symbol.get(alias, ()))
        return next(iter(matches)) if len(matches) == 1 else None


class MappingBuilder:
    """Coalesce adjacent occurrences without bridging gaps or inline contexts."""

    def __init__(self) -> None:
        self.rows: list[BinarySourceMapping] = []
        self.latest: dict[tuple[object, ...], int] = {}

    def add(self, mapping: BinarySourceMapping) -> None:
        key = (
            mapping.range_id,
            mapping.source_node_id,
            mapping.method,
            mapping.file_path,
            mapping.line,
            mapping.column,
            mapping.function,
            mapping.inline_depth,
        )
        previous = self.latest.get(key)
        if previous is not None and self.rows[previous].end == mapping.start:
            self.rows[previous] = replace(self.rows[previous], end=mapping.end)
        else:
            self.latest[key] = len(self.rows)
            self.rows.append(mapping)

    def finish(self) -> list[BinarySourceMapping]:
        result = []
        for row in self.rows:
            identity = repr(
                (
                    row.range_id,
                    row.source_node_id,
                    row.start,
                    row.end,
                    row.method,
                    row.file_path,
                    row.line,
                    row.column,
                    row.function,
                    row.inline_depth,
                )
            )
            result.append(
                replace(row, id="mapping_" + hashlib.sha256(identity.encode()).hexdigest())
            )
        return result
