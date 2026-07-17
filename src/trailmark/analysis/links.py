"""Load explicit cross-language and external links from repository configuration."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any, NoReturn, cast

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit, NodeKind, NodeOrigin, SourceLocation

LINKS_FILE = Path(".trailmark/links.toml")


def apply_repository_links(graph: CodeGraph, root_path: str) -> None:
    """Add links declared in ``.trailmark/links.toml`` below ``root_path``.

    Invalid configuration is rejected rather than silently weakening the
    resulting graph. Unresolved endpoints are accepted only when explicitly
    marked with ``source_external`` or ``target_external``.
    """
    config_path = Path(root_path).resolve() / LINKS_FILE
    if not config_path.is_file():
        return
    try:
        data = tomllib.loads(config_path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        msg = f"Invalid {LINKS_FILE}: {exc}"
        raise ValueError(msg) from exc

    _validate_top_level(data)
    entries = data.get("link", [])
    if not isinstance(entries, list):
        msg = f"Invalid {LINKS_FILE}: 'link' must be an array of tables"
        raise ValueError(msg)
    for index, raw in enumerate(entries, start=1):
        _apply_link(graph, raw, index, config_path)


def _apply_link(graph: CodeGraph, raw: object, index: int, config_path: Path) -> None:
    if not isinstance(raw, dict):
        _invalid(index, "entry must be a table")
    entry = cast("dict[str, Any]", raw)
    _validate_link_keys(entry, index)
    source_ref = _required_string(entry, "source", index)
    target_ref = _required_string(entry, "target", index)
    source_external = _optional_bool(entry, "source_external", index)
    target_external = _optional_bool(entry, "target_external", index)

    source_id = _resolve_endpoint(
        graph, source_ref, source_external, "source_external", index, config_path
    )
    target_id = _resolve_endpoint(
        graph, target_ref, target_external, "target_external", index, config_path
    )
    kind = _edge_kind(entry.get("kind", "calls"), index)
    confidence = _edge_confidence(entry.get("confidence", "inferred"), index)
    description = entry.get("description")
    if description is not None and not isinstance(description, str):
        _invalid(index, "'description' must be a string")

    attributes = (("configured_by", str(LINKS_FILE)),)
    if description:
        attributes += (("description", description),)
    edge = CodeEdge(
        source_id=source_id,
        target_id=target_id,
        kind=kind,
        confidence=confidence,
        attributes=attributes,
    )
    if edge not in graph.edges:
        graph.edges.append(edge)


def _validate_top_level(data: dict[str, object]) -> None:
    unknown = sorted(key for key in data if key != "link")
    if unknown:
        _invalid(0, f"unknown top-level key {unknown[0]!r}; only 'link' is valid")


def _validate_link_keys(entry: dict[str, Any], index: int) -> None:
    allowed = {
        "source",
        "target",
        "kind",
        "confidence",
        "description",
        "source_external",
        "target_external",
    }
    unknown = sorted(key for key in entry if key not in allowed)
    if unknown:
        _invalid(index, f"unknown key {unknown[0]!r}")


def _required_string(entry: dict[str, Any], key: str, index: int) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        _invalid(index, f"'{key}' must be a non-empty string")
    return value.strip()


def _optional_bool(entry: dict[str, Any], key: str, index: int) -> bool:
    value = entry.get(key, False)
    if not isinstance(value, bool):
        _invalid(index, f"'{key}' must be a boolean")
    return value


def _resolve_endpoint(
    graph: CodeGraph,
    reference: str,
    external: bool,
    flag_name: str,
    index: int,
    config_path: Path,
) -> str:
    if reference in graph.nodes:
        return reference
    matches = [
        node_id
        for node_id, unit in graph.nodes.items()
        if unit.name == reference
        or node_id.endswith(f":{reference}")
        or node_id.endswith(f".{reference}")
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        _invalid(index, f"endpoint {reference!r} is ambiguous: {', '.join(sorted(matches))}")
    if not external:
        _invalid(
            index,
            f"endpoint {reference!r} does not exist (set {flag_name} = true to proxy it)",
        )
    return _add_external_proxy(graph, reference, config_path)


def _add_external_proxy(graph: CodeGraph, reference: str, config_path: Path) -> str:
    escaped = re.sub(r"[^0-9A-Za-z_.@$:-]+", "_", reference.strip()) or "unknown"
    node_id = f"proxy.external:{escaped}"
    graph.nodes.setdefault(
        node_id,
        CodeUnit(
            id=node_id,
            name=reference,
            kind=NodeKind.PROXY,
            location=SourceLocation(str(config_path), 0, 0),
            origin=NodeOrigin.PROXY,
            attributes=(("raw_symbol", reference), ("proxy_kind", "external")),
        ),
    )
    return node_id


def _edge_kind(value: object, index: int) -> EdgeKind:
    if not isinstance(value, str):
        _invalid(index, "'kind' must be a string")
    try:
        return EdgeKind(value)
    except ValueError:
        choices = ", ".join(item.value for item in EdgeKind)
        _invalid(index, f"invalid kind {value!r}; expected one of: {choices}")


def _edge_confidence(value: object, index: int) -> EdgeConfidence:
    if not isinstance(value, str):
        _invalid(index, "'confidence' must be a string")
    try:
        return EdgeConfidence(value)
    except ValueError:
        choices = ", ".join(item.value for item in EdgeConfidence)
        _invalid(index, f"invalid confidence {value!r}; expected one of: {choices}")


def _invalid(index: int, detail: str) -> NoReturn:
    msg = f"Invalid {LINKS_FILE} [[link]] #{index}: {detail}"
    raise ValueError(msg)
