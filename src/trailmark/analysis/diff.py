"""Structural diff between two Trailmark code graphs.

Used by the ``trailmark diff`` CLI and programmatically via
``QueryEngine.diff_against``. Operates on ``CodeGraph`` objects
directly, so callers don't have to export to JSON first.

Produces a security-oriented diff: what nodes, edges, and entrypoints
changed between two snapshots. Designed for PR-review / release-audit
workflows where "did my attack surface change?" is the question.
"""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from trailmark.models.annotations import EntrypointTag
from trailmark.models.edges import CodeEdge
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit


def compute_diff(before: CodeGraph, after: CodeGraph) -> dict[str, Any]:
    """Return a structured diff between two graphs.

    Fields:
        - ``summary_delta``: counts (nodes, functions, classes, call edges,
          entrypoints) with before/after/delta.
        - ``nodes``: ``{added, removed, modified}`` node entries.
        - ``edges``: ``{added, removed}`` edge entries.
        - ``entrypoints``: ``{added, removed, modified}`` entrypoint entries
          with trust/asset before/after.
    """
    return {
        "summary_delta": _diff_summary(before, after),
        "nodes": _diff_nodes(before, after),
        "edges": _diff_edges(before, after),
        "entrypoints": _diff_entrypoints(before, after),
    }


def _diff_summary(before: CodeGraph, after: CodeGraph) -> dict[str, Any]:
    metrics = {
        "nodes": (len(before.nodes), len(after.nodes)),
        "edges": (len(before.edges), len(after.edges)),
        "entrypoints": (len(before.entrypoints), len(after.entrypoints)),
    }
    delta: dict[str, Any] = {}
    for key, (b, a) in metrics.items():
        if b != a:
            delta[key] = {"before": b, "after": a, "delta": a - b}
    return delta


def _diff_nodes(before: CodeGraph, after: CodeGraph) -> dict[str, Any]:
    before_ids = set(before.nodes.keys())
    after_ids = set(after.nodes.keys())

    added_ids = sorted(after_ids - before_ids)
    removed_ids = sorted(before_ids - after_ids)
    shared_ids = before_ids & after_ids

    modified: list[dict[str, Any]] = []
    for nid in sorted(shared_ids):
        changes = _compare_units(before.nodes[nid], after.nodes[nid])
        if changes:
            modified.append({"id": nid, "changes": changes})

    return {
        "added": [_unit_summary(after.nodes[n]) for n in added_ids],
        "removed": [_unit_summary(before.nodes[n]) for n in removed_ids],
        "modified": modified,
    }


def _unit_summary(unit: CodeUnit) -> dict[str, Any]:
    return {
        "id": unit.id,
        "name": unit.name,
        "kind": unit.kind.value,
        "file": unit.location.file_path,
        "cyclomatic_complexity": unit.cyclomatic_complexity,
    }


def _compare_units(before: CodeUnit, after: CodeUnit) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    if before.cyclomatic_complexity != after.cyclomatic_complexity:
        changes["cyclomatic_complexity"] = {
            "before": before.cyclomatic_complexity,
            "after": after.cyclomatic_complexity,
        }
    b_params = tuple(p.name for p in before.parameters)
    a_params = tuple(p.name for p in after.parameters)
    if b_params != a_params:
        changes["parameters"] = {"before": list(b_params), "after": list(a_params)}
    b_span = max(0, before.location.end_line - before.location.start_line + 1)
    a_span = max(0, after.location.end_line - after.location.start_line + 1)
    if b_span != a_span:
        changes["line_span"] = {"before": b_span, "after": a_span}
    return changes


def _diff_edges(before: CodeGraph, after: CodeGraph) -> dict[str, Any]:
    before_set = {_edge_key(e) for e in before.edges}
    after_set = {_edge_key(e) for e in after.edges}
    added = sorted(after_set - before_set)
    removed = sorted(before_set - after_set)
    return {
        "added": [_parse_edge_key(k) for k in added],
        "removed": [_parse_edge_key(k) for k in removed],
    }


def _edge_key(edge: CodeEdge) -> str:
    return f"{edge.source_id}|{edge.target_id}|{edge.kind.value}"


def _parse_edge_key(key: str) -> dict[str, str]:
    source, target, kind = key.split("|", 2)
    return {"source": source, "target": target, "kind": kind}


def _diff_entrypoints(before: CodeGraph, after: CodeGraph) -> dict[str, Any]:
    before_ids = set(before.entrypoints.keys())
    after_ids = set(after.entrypoints.keys())
    added_ids = sorted(after_ids - before_ids)
    removed_ids = sorted(before_ids - after_ids)
    shared_ids = before_ids & after_ids

    modified: list[dict[str, Any]] = []
    for nid in sorted(shared_ids):
        b_tag = before.entrypoints[nid]
        a_tag = after.entrypoints[nid]
        if asdict(b_tag) != asdict(a_tag):
            modified.append(
                {
                    "id": nid,
                    "before": _ep_summary(b_tag),
                    "after": _ep_summary(a_tag),
                }
            )

    return {
        "added": [{"id": nid, **_ep_summary(after.entrypoints[nid])} for nid in added_ids],
        "removed": [{"id": nid, **_ep_summary(before.entrypoints[nid])} for nid in removed_ids],
        "modified": modified,
    }


def _ep_summary(tag: EntrypointTag) -> dict[str, Any]:
    return {
        "kind": tag.kind.value,
        "trust_level": tag.trust_level.value,
        "asset_value": tag.asset_value.value,
        "description": tag.description,
    }


def format_diff(diff: dict[str, Any]) -> str:
    """Render a structured diff as a human-readable report."""
    lines: list[str] = []

    summary = diff.get("summary_delta", {})
    if summary:
        lines.append("Summary:")
        for key in ("nodes", "edges", "entrypoints"):
            if key in summary:
                s = summary[key]
                sign = "+" if s["delta"] >= 0 else ""
                lines.append(f"  {key}: {s['before']} -> {s['after']} ({sign}{s['delta']})")
        lines.append("")

    nodes = diff.get("nodes", {})
    added_nodes = nodes.get("added", [])
    removed_nodes = nodes.get("removed", [])
    modified_nodes = nodes.get("modified", [])

    if added_nodes:
        lines.append(f"Added nodes ({len(added_nodes)}):")
        for n in added_nodes[:20]:
            lines.append(f"  + {n['id']}  ({n['kind']}, {n['file']})")
        if len(added_nodes) > 20:
            lines.append(f"  ... and {len(added_nodes) - 20} more")
        lines.append("")

    if removed_nodes:
        lines.append(f"Removed nodes ({len(removed_nodes)}):")
        for n in removed_nodes[:20]:
            lines.append(f"  - {n['id']}  ({n['kind']}, {n['file']})")
        if len(removed_nodes) > 20:
            lines.append(f"  ... and {len(removed_nodes) - 20} more")
        lines.append("")

    if modified_nodes:
        lines.append(f"Modified nodes ({len(modified_nodes)}):")
        for m in modified_nodes[:20]:
            changes = ", ".join(m["changes"].keys())
            lines.append(f"  ~ {m['id']}  ({changes})")
            if "cyclomatic_complexity" in m["changes"]:
                cc = m["changes"]["cyclomatic_complexity"]
                lines.append(f"      complexity: {cc['before']} -> {cc['after']}")
        if len(modified_nodes) > 20:
            lines.append(f"  ... and {len(modified_nodes) - 20} more")
        lines.append("")

    eps = diff.get("entrypoints", {})
    added_eps = eps.get("added", [])
    removed_eps = eps.get("removed", [])
    modified_eps = eps.get("modified", [])

    if added_eps or removed_eps or modified_eps:
        lines.append("Attack surface:")
        for ep in added_eps:
            lines.append(
                f"  + entrypoint {ep['id']}  "
                f"({ep['kind']}, trust={ep['trust_level']}, asset={ep['asset_value']})"
            )
        for ep in removed_eps:
            lines.append(
                f"  - entrypoint {ep['id']}  "
                f"({ep['kind']}, trust={ep['trust_level']}, asset={ep['asset_value']})"
            )
        for m in modified_eps:
            lines.append(f"  ~ entrypoint {m['id']}")
            if m["before"]["trust_level"] != m["after"]["trust_level"]:
                lines.append(
                    f"      trust: {m['before']['trust_level']} -> {m['after']['trust_level']}"
                )
            if m["before"]["asset_value"] != m["after"]["asset_value"]:
                lines.append(
                    f"      asset: {m['before']['asset_value']} -> {m['after']['asset_value']}"
                )
        lines.append("")

    edges = diff.get("edges", {})
    added_edges = edges.get("added", [])
    removed_edges = edges.get("removed", [])
    if added_edges or removed_edges:
        lines.append(f"Edges: +{len(added_edges)}  -{len(removed_edges)}")
        lines.append("")

    if not lines:
        return "No structural changes."
    return "\n".join(lines).rstrip()


@contextmanager
def git_worktree(repo: Path, ref: str) -> Generator[Path, None, None]:
    """Materialize ``ref`` from ``repo`` as a temporary worktree.

    Uses ``git worktree add`` which is non-destructive to the primary
    working tree. The worktree is removed when the context exits.
    """
    repo = repo.resolve()
    if not (repo / ".git").exists() and not (repo / ".git").is_file():
        msg = f"Not a git repository: {repo}"
        raise ValueError(msg)
    with tempfile.TemporaryDirectory(prefix="trailmark-worktree-") as tmp:
        worktree_path = Path(tmp) / "wt"
        subprocess.run(  # noqa: S603
            ["git", "worktree", "add", "--detach", str(worktree_path), ref],  # noqa: S607
            cwd=str(repo),
            check=True,
            capture_output=True,
        )
        try:
            yield worktree_path
        finally:
            subprocess.run(  # noqa: S603
                ["git", "worktree", "remove", "--force", str(worktree_path)],  # noqa: S607
                cwd=str(repo),
                check=False,
                capture_output=True,
            )
