"""Import external binary-analysis graphs into Trailmark graphs."""

from __future__ import annotations

import json
import os
import re
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from trailmark.analysis.proxies import _add_proxy_node, ensure_proxy_nodes
from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit, NodeKind, NodeOrigin, SourceLocation
from trailmark.storage.graph_store import GraphStore


def augment_from_binary_graph(
    store: GraphStore,
    graph_path: str,
    *,
    connect_sources: bool = True,
) -> dict[str, Any]:
    """Import a Trailmark binary graph JSON file.

    Expected shape is intentionally small: an ``artifact`` object, a
    ``functions`` list, and a ``calls`` list. Unknown fields are ignored.
    Invalid records or colliding node IDs raise ``ValueError``. Input is
    processed before committing changes, so failed imports preserve the graph.
    """
    with open(graph_path) as f:
        data = json.load(f)

    if not isinstance(data, dict):
        msg = "Binary graph must be a JSON object"
        raise ValueError(msg)
    artifact = data.get("artifact", {})
    if not isinstance(artifact, dict):
        msg = "Binary graph 'artifact' must be an object"
        raise ValueError(msg)
    functions = _records(data, "functions")
    calls = _records(data, "calls")
    for index, call in enumerate(calls):
        for endpoint in ("source", "target"):
            _validate_call_ref(call.get(endpoint), f"Binary graph calls[{index}].{endpoint}")

    current = store._graph  # noqa: SLF001
    graph = CodeGraph(
        nodes=current.nodes.copy(),
        edges=current.edges.copy(),
        root_path=current.root_path,
    )
    artifact_identity = _artifact_identity(artifact, graph_path)
    artifact_key = _sanitize_identifier(artifact_identity)
    architecture = _optional_str(artifact.get("architecture") or artifact.get("arch"))
    artifact_hash = _optional_str(artifact.get("hash") or artifact.get("sha256"))
    architecture, artifact_hash = _artifact_metadata(
        current, artifact_key, artifact_identity, architecture, artifact_hash
    )
    artifact_path = _optional_str(artifact.get("path")) or graph_path

    ref_counts = _function_ref_counts(functions)
    ref_to_id: dict[str, str] = {}
    binary_ids: list[str] = []
    imported_ids: set[str] = set()

    for function in functions:
        node_id = _binary_node_id(artifact_key, function, ref_counts)
        unit = _binary_unit(
            node_id,
            artifact_path,
            function,
            architecture,
            artifact_hash,
            artifact_identity,
        )
        existing = graph.nodes.get(node_id)
        if node_id in imported_ids or (
            existing is not None
            and (
                existing.origin != NodeOrigin.BINARY
                or existing.name != unit.name
                or dict(existing.attributes).get("artifact_identity", artifact_identity)
                != artifact_identity
            )
        ):
            msg = f"Binary functions collide at node ID {node_id!r}"
            raise ValueError(msg)
        binary_ids.append(node_id)
        imported_ids.add(node_id)
        graph.nodes[node_id] = unit
        for ref in _function_refs(function):
            if ref_counts.get(ref, 0) > 1:
                continue
            ref_to_id[ref] = node_id

    call_edges = 0
    external_proxies = 0
    for call in calls:
        source_id = _resolve_call_ref(call.get("source"), ref_to_id)
        target_id = _resolve_call_ref(call.get("target"), ref_to_id)
        if source_id is None:
            continue
        if target_id is None:
            target_raw = _ref_text(call.get("target"))
            if not target_raw:
                continue
            target_id = _external_proxy_id(target_raw)
            if target_id not in graph.nodes:
                external_proxies += 1
            _add_proxy_node(
                graph,
                target_id,
                target_raw,
                "external",
                SourceLocation(file_path=artifact_path, start_line=0, end_line=0),
            )
        graph.edges.append(
            CodeEdge(
                source_id=source_id,
                target_id=target_id,
                kind=EdgeKind.CALLS,
                confidence=_confidence(call.get("confidence")),
                attributes=_call_attributes(call),
            )
        )
        call_edges += 1

    correspondences = 0
    if connect_sources:
        correspondences = _connect_source_nodes(graph, binary_ids, data, artifact_key)

    ensure_proxy_nodes(graph)
    current.nodes = graph.nodes
    current.edges = graph.edges
    store.add_subgraph(f"binary:{artifact_key}", set(binary_ids))
    store.rebuild_index()
    return {
        "artifact": artifact_key,
        "binary_nodes": len(binary_ids),
        "call_edges": call_edges,
        "external_proxies": external_proxies,
        "correspondences": correspondences,
        "subgraphs_created": [f"binary:{artifact_key}"],
    }


def _records(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """Validate collections before processing records or mutating graph state."""
    value = data.get(key, [])
    if not isinstance(value, list):
        msg = f"Binary graph {key!r} must be an array of objects"
        raise ValueError(msg)
    for index, record in enumerate(value):
        if not isinstance(record, dict):
            msg = f"Binary graph {key!r}[{index}] must be an object"
            raise ValueError(msg)
    return value


def _artifact_identity(artifact: dict[str, Any], graph_path: str) -> str:
    raw = artifact.get("key") or artifact.get("name") or Path(graph_path).stem or "binary"
    return str(raw)


def _artifact_metadata(
    graph: CodeGraph,
    key: str,
    identity: str,
    architecture: str | None,
    artifact_hash: str | None,
) -> tuple[str | None, str | None]:
    """Reject mixed builds and retain known metadata when a later input omits it.

    Check all retained artifact nodes: the named subgraph can have been replaced
    by an earlier partial import and need not contain every old node.
    """
    metadata = {"architecture": architecture, "artifact_hash": artifact_hash}
    for node_id, node in graph.nodes.items():
        if node.origin != NodeOrigin.BINARY or not node_id.startswith(f"bin.{key}:"):
            continue
        attrs = dict(node.attributes)
        if attrs.get("artifact_identity", identity) != identity:
            raise ValueError(f"Binary artifacts collide at artifact ID {key!r}")
        for field, value in metadata.items():
            previous = _optional_str(attrs.get(field))
            if value is not None and previous is not None and value != previous:
                raise ValueError(f"Binary artifact {key!r} has conflicting {field}")
            metadata[field] = value or previous
    return metadata["architecture"], metadata["artifact_hash"]


def _validate_call_ref(value: object, context: str) -> None:
    if (
        value is None
        or isinstance(value, str)
        or (isinstance(value, int) and not isinstance(value, bool))
    ):
        return
    if isinstance(value, dict):
        for key in ("id", "symbol", "name", "address", "rva"):
            item = value.get(key)
            if item is not None and not (
                isinstance(item, str) or (isinstance(item, int) and not isinstance(item, bool))
            ):
                raise ValueError(f"{context}.{key} must be a string or integer")
        return
    raise ValueError(f"{context} must be a string, integer, or reference object")


def _binary_node_id(
    artifact_key: str,
    function: dict[str, Any],
    ref_counts: dict[str, int],
) -> str:
    symbol = _function_symbol(function)
    if symbol and ref_counts.get(symbol) == 1:
        return f"bin.{artifact_key}:{_sanitize_identifier(symbol)}"
    address = _int_value(function.get("rva"))
    if address is None:
        address = _int_value(function.get("address"))
    if address is None:
        digest = sha256(json.dumps(function, sort_keys=True).encode("utf-8")).hexdigest()
        address = int(digest[:8], 16)
    return f"bin.{artifact_key}:sub_{address:x}"


def _binary_unit(
    node_id: str,
    artifact_path: str,
    function: dict[str, Any],
    architecture: str | None,
    artifact_hash: str | None,
    artifact_identity: str,
) -> CodeUnit:
    symbol = _function_symbol(function) or node_id.rsplit(":", 1)[-1]
    attrs = _function_attributes(function, architecture, artifact_hash)
    return CodeUnit(
        id=node_id,
        name=symbol,
        kind=NodeKind.FUNCTION,
        location=SourceLocation(file_path=artifact_path, start_line=0, end_line=0),
        origin=NodeOrigin.BINARY,
        attributes=attrs + (("artifact_identity", artifact_identity),),
    )


def _function_attributes(
    function: dict[str, Any],
    architecture: str | None,
    artifact_hash: str | None,
) -> tuple[tuple[str, str | int | float | bool | None], ...]:
    attrs: list[tuple[str, str | int | float | bool | None]] = []
    for key in ("address", "rva", "size", "section"):
        if key in function:
            attrs.append((key, function[key]))
    if architecture is not None:
        attrs.append(("architecture", architecture))
    if artifact_hash is not None:
        attrs.append(("artifact_hash", artifact_hash))
    return tuple(attrs)


def _call_attributes(
    call: dict[str, Any],
) -> tuple[tuple[str, str | int | float | bool | None], ...]:
    attrs: list[tuple[str, str | int | float | bool | None]] = []
    for key in ("address", "callsite", "kind"):
        if key in call:
            attrs.append((key, call[key]))
    return tuple(attrs)


def _function_ref_counts(functions: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for function in functions:
        for ref in _function_refs(function):
            counts[ref] = counts.get(ref, 0) + 1
    return counts


def _function_symbol(function: dict[str, Any]) -> str | None:
    return _optional_str(function.get("symbol") or function.get("name"))


def _function_refs(function: dict[str, Any]) -> set[str]:
    refs: set[str] = set()
    for key in ("id", "symbol", "name"):
        value = _optional_str(function.get(key))
        if value:
            refs.add(value)
    for key in ("address", "rva"):
        value = _int_value(function.get(key))
        if value is not None:
            refs.add(str(value))
            refs.add(hex(value))
    return refs


def _resolve_call_ref(value: object, ref_to_id: dict[str, str]) -> str | None:
    if isinstance(value, dict):
        mapping = cast("dict[str, object]", value)
        for key in ("id", "symbol", "name", "address", "rva"):
            resolved = _resolve_call_ref(mapping.get(key), ref_to_id)
            if resolved is not None:
                return resolved
        return None
    text = _ref_text(value)
    if not text:
        return None
    return ref_to_id.get(text)


def _ref_text(value: object) -> str:
    if isinstance(value, dict):
        for key in ("id", "symbol", "name", "address", "rva"):
            text = _ref_text(value.get(key))
            if text:
                return text
        return ""
    if value is None:
        return ""
    if isinstance(value, int):
        return str(value)
    return str(value)


def _external_proxy_id(raw_symbol: str) -> str:
    return f"proxy.external:{_sanitize_identifier(raw_symbol)}"


def _connect_source_nodes(
    graph: CodeGraph,
    binary_ids: list[str],
    data: dict[str, Any],
    artifact_key: str,
) -> int:
    connected = 0
    for function, binary_id in zip(data.get("functions", []), binary_ids, strict=False):
        source_id = _source_node_id(graph, function)
        if source_id is None or source_id not in graph.nodes:
            continue
        graph.edges.append(
            CodeEdge(
                source_id=source_id,
                target_id=binary_id,
                kind=EdgeKind.CORRESPONDS_TO,
                confidence=EdgeConfidence.INFERRED,
                attributes=(("artifact", artifact_key),),
            )
        )
        connected += 1
    return connected


def _source_node_id(graph: CodeGraph, function: dict[str, Any]) -> str | None:
    source = function.get("source")
    if isinstance(source, dict):
        explicit = _optional_str(source.get("node_id"))
        if explicit and _is_source_node(graph, explicit):
            return explicit
        symbol = _optional_str(source.get("symbol") or source.get("name"))
        if symbol:
            found = _find_source_node_id(graph, symbol)
            if found is not None:
                return found
        path = _optional_str(source.get("file") or source.get("path"))
        line = _int_value(source.get("line") or source.get("start_line"))
        if path and line is not None:
            return _node_at_source_line(graph, path, line)

    symbol = _function_symbol(function)
    if symbol:
        return _find_source_node_id(graph, symbol)
    return None


def _is_source_node(graph: CodeGraph, node_id: str) -> bool:
    node = graph.nodes.get(node_id)
    return node is not None and node.origin == NodeOrigin.SOURCE


def _find_source_node_id(graph: CodeGraph, name: str) -> str | None:
    candidates: list[tuple[int, int, str]] = []
    for node_id, node in graph.nodes.items():
        if node.origin != NodeOrigin.SOURCE:
            continue
        match_rank = _source_name_match_rank(node_id, node, name)
        if match_rank is None:
            continue
        candidates.append((match_rank, _source_mapping_kind_rank(node.kind), node_id))
    if not candidates:
        return None
    candidates.sort()
    best_rank, best_kind_rank, best_id = candidates[0]
    if (
        sum(
            1
            for rank, kind_rank, _ in candidates
            if (rank, kind_rank) == (best_rank, best_kind_rank)
        )
        > 1
    ):
        return None
    return best_id


def _source_name_match_rank(node_id: str, node: CodeUnit, name: str) -> int | None:
    if node_id == name:
        return 0
    if node.name == name:
        return 1
    if node_id.endswith(f":{name}"):
        return 2
    if node_id.endswith(f".{name}"):
        return 3
    return None


def _node_at_source_line(graph: CodeGraph, path: str, line: int) -> str | None:
    root = graph.root_path
    matches: list[tuple[int, int, str]] = []
    for node_id, node in graph.nodes.items():
        if node.origin != NodeOrigin.SOURCE:
            continue
        loc = node.location
        if _same_path(loc.file_path, path, root) and loc.start_line <= line <= loc.end_line:
            span = max(loc.end_line - loc.start_line, 0)
            matches.append((span, _source_mapping_kind_rank(node.kind), node_id))
    if not matches:
        return None
    return min(matches)[2]


def _source_mapping_kind_rank(kind: NodeKind) -> int:
    """Prefer executable source nodes over broader containers for line mappings."""
    if kind in (NodeKind.FUNCTION, NodeKind.METHOD):
        return 0
    if kind in (
        NodeKind.CLASS,
        NodeKind.STRUCT,
        NodeKind.INTERFACE,
        NodeKind.TRAIT,
        NodeKind.CONTRACT,
    ):
        return 1
    if kind in (NodeKind.MODULE, NodeKind.NAMESPACE):
        return 2
    return 1


def _same_path(left: str, right: str, root: str) -> bool:
    left_norm = os.path.normpath(left)
    right_norm = os.path.normpath(right)
    if left_norm == right_norm:
        return True
    if root:
        try:
            rel = os.path.relpath(left_norm, root)
        except ValueError:
            rel = left_norm
        return os.path.normpath(rel) == right_norm
    return False


def _confidence(value: object) -> EdgeConfidence:
    if isinstance(value, EdgeConfidence):
        return value
    if isinstance(value, str):
        try:
            return EdgeConfidence(value)
        except ValueError:
            return EdgeConfidence.UNCERTAIN
    return EdgeConfidence.CERTAIN


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _int_value(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError:
            return None
    return None


def _sanitize_identifier(value: str) -> str:
    text = value.strip() or "unknown"
    return re.sub(r"[^0-9A-Za-z_.@$-]+", "_", text)
