"""Generate Mermaid diagrams from Trailmark code graphs."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import deque
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from trailmark.query.api import QueryEngine

_MAX_NODES = 100

_DIAGRAM_TYPES = (
    "call-graph",
    "class-hierarchy",
    "module-deps",
    "containment",
    "complexity",
    "data-flow",
)

_CONTAINER_KINDS = frozenset({"class", "struct", "interface", "trait", "enum"})

_MEMBER_KINDS = frozenset({"function", "method"})


def sanitize_id(node_id: str) -> str:
    """Convert a Trailmark node ID to a Mermaid-safe identifier."""
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", node_id)
    if not safe:
        return "n_empty"
    if safe[0].isdigit():
        safe = f"n_{safe}"
    return safe


def node_label(node: dict[str, Any]) -> str:
    """Build a human-readable label for a graph node."""
    name = node.get("name", node.get("id", "?"))
    kind = node.get("kind", "")
    cc = node.get("cyclomatic_complexity")
    parts = [name]
    if kind:
        parts.append(kind)
    if cc is not None:
        parts.append(f"CC={cc}")
    return ", ".join(parts)


def edge_style(confidence: str) -> str:
    """Return a Mermaid arrow based on edge confidence."""
    if confidence == "inferred":
        return "-.->"
    if confidence == "uncertain":
        return "-..->"
    return "-->"


def complexity_class(cc: int | None) -> str:
    """Map cyclomatic complexity to a Mermaid style class name."""
    if cc is None or cc < 5:
        return "low"
    if cc <= 10:
        return "medium"
    return "high"


def build_engine(
    target: str,
    language: str,
) -> QueryEngine:
    """Construct a QueryEngine, exiting with guidance on failure."""
    try:
        from trailmark.query.api import QueryEngine
    except ImportError:
        print(
            "Error: trailmark is not installed.\nRun: uv pip install -e /path/to/trailmark",
            file=sys.stderr,
        )
        sys.exit(1)
    return QueryEngine.from_directory(target, language=language)


def _load_graph(engine: QueryEngine) -> dict[str, Any]:
    """Parse the engine's full JSON export into a dict."""
    return json.loads(engine.to_json())


def _suggest_focus(
    graph: dict[str, Any],
    limit: int = 20,
) -> str:
    """Return a comma-separated sample of available node names."""
    names = [n.get("name", nid) for nid, n in list(graph.get("nodes", {}).items())[:limit]]
    return ", ".join(names)


def _find_focus_node(
    graph: dict[str, Any],
    focus: str,
) -> str | None:
    """Resolve a focus name to a node ID, or None."""
    nodes = graph.get("nodes", {})
    if focus in nodes:
        return focus
    for nid, node in nodes.items():
        if node.get("name") == focus or nid.endswith(f":{focus}"):
            return nid
        if nid.endswith(f".{focus}"):
            return nid
    return None


def _require_focus_node(
    graph: dict[str, Any],
    focus: str,
) -> str:
    """Resolve a focus name or exit with a helpful message."""
    nid = _find_focus_node(graph, focus)
    if nid is not None:
        return nid
    print(
        f"Error: node '{focus}' not found.\nAvailable: {_suggest_focus(graph)}",
        file=sys.stderr,
    )
    raise SystemExit(1)


# ── BFS neighbor collection ─────────────────────────────────────


def collect_neighbors(
    engine: QueryEngine,
    focus_name: str,
    depth: int,
) -> tuple[dict[str, dict[str, Any]], list[tuple[str, str, str]]]:
    """BFS from focus node collecting nodes and call edges."""
    graph = _load_graph(engine)
    focus_id = _require_focus_node(graph, focus_name)

    nodes_map = graph.get("nodes", {})
    edges_list = graph.get("edges", [])
    call_fwd: dict[str, list[tuple[str, str]]] = {}
    call_rev: dict[str, list[tuple[str, str]]] = {}
    for e in edges_list:
        if e.get("kind") != "calls":
            continue
        src, tgt = e["source"], e["target"]
        conf = e.get("confidence", "certain")
        call_fwd.setdefault(src, []).append((tgt, conf))
        call_rev.setdefault(tgt, []).append((src, conf))

    collected: dict[str, dict[str, Any]] = {}
    collected_edges: list[tuple[str, str, str]] = []
    queue: deque[tuple[str, int]] = deque([(focus_id, 0)])
    visited: set[str] = {focus_id}

    while queue:
        nid, d = queue.popleft()
        if nid in nodes_map:
            collected[nid] = nodes_map[nid]
        if d >= depth:
            continue
        for tgt, conf in call_fwd.get(nid, []):
            collected_edges.append((nid, tgt, conf))
            if tgt not in visited:
                visited.add(tgt)
                queue.append((tgt, d + 1))
        for src, conf in call_rev.get(nid, []):
            collected_edges.append((src, nid, conf))
            if src not in visited:
                visited.add(src)
                queue.append((src, d + 1))

    return collected, collected_edges


# ── Emitters ─────────────────────────────────────────────────────


def emit_call_graph(
    engine: QueryEngine,
    focus: str | None,
    depth: int,
    direction: str,
) -> str:
    """Emit a Mermaid flowchart of call relationships."""
    if focus:
        nodes, edges = collect_neighbors(engine, focus, depth)
    else:
        graph = _load_graph(engine)
        nodes = graph.get("nodes", {})
        edges = [
            (e["source"], e["target"], e.get("confidence", "certain"))
            for e in graph.get("edges", [])
            if e.get("kind") == "calls"
        ]
        _warn_if_large(nodes)

    return _render_flowchart(nodes, edges, direction)


def emit_class_hierarchy(
    engine: QueryEngine,
    direction: str,
) -> str:
    """Emit a Mermaid classDiagram of inheritance relationships."""
    graph = _load_graph(engine)
    nodes = graph.get("nodes", {})
    edges = graph.get("edges", [])

    type_nodes = {nid: n for nid, n in nodes.items() if n.get("kind") in _CONTAINER_KINDS}
    if not type_nodes:
        return _empty_diagram("classDiagram", "No classes found")

    lines = ["classDiagram"]
    for nid, n in type_nodes.items():
        lines.append(f"    class {sanitize_id(nid)} {{\n        {n.get('kind', '')}\n    }}")

    for e in edges:
        src, tgt = e.get("source"), e.get("target")
        kind = e.get("kind")
        if kind == "inherits" and src in type_nodes and tgt in type_nodes:
            lines.append(f"    {sanitize_id(tgt)} <|-- {sanitize_id(src)}")
        elif kind == "implements" and src in type_nodes and tgt in type_nodes:
            lines.append(f"    {sanitize_id(tgt)} <|.. {sanitize_id(src)}")

    return "\n".join(lines)


def emit_module_deps(
    engine: QueryEngine,
    direction: str,
) -> str:
    """Emit a Mermaid flowchart of module import relationships."""
    graph = _load_graph(engine)
    nodes = graph.get("nodes", {})
    import_edges = [
        (e["source"], e["target"], e.get("confidence", "certain"))
        for e in graph.get("edges", [])
        if e.get("kind") == "imports"
    ]
    if not import_edges:
        return _empty_diagram(f"flowchart {direction}", "No import edges found")

    involved = set()
    for src, tgt, _ in import_edges:
        involved.add(src)
        involved.add(tgt)
    mod_nodes = {nid: n for nid, n in nodes.items() if nid in involved}
    _warn_if_large(mod_nodes)
    return _render_flowchart(mod_nodes, import_edges, direction)


def emit_containment(
    engine: QueryEngine,
    direction: str,
) -> str:
    """Emit a Mermaid classDiagram showing class members."""
    graph = _load_graph(engine)
    nodes = graph.get("nodes", {})
    edges = graph.get("edges", [])

    containers: dict[str, list[str]] = {}
    for e in edges:
        if e.get("kind") != "contains":
            continue
        parent, child = e["source"], e["target"]
        parent_node = nodes.get(parent, {})
        child_node = nodes.get(child, {})
        if parent_node.get("kind") in _CONTAINER_KINDS and child_node.get("kind") in _MEMBER_KINDS:
            containers.setdefault(parent, []).append(child)

    if not containers:
        return _empty_diagram("classDiagram", "No containment found")

    lines = ["classDiagram"]
    for parent_id, children in containers.items():
        parent = nodes[parent_id]
        sid = sanitize_id(parent_id)
        lines.append(f"    class {sid} {{")
        for child_id in children:
            child = nodes.get(child_id, {})
            cname = child.get("name", child_id)
            ret = ""
            if child.get("return_type"):
                rt = child["return_type"]
                ret = f" {rt.get('name', '')}" if isinstance(rt, dict) else f" {rt}"
            lines.append(f"        +{cname}(){ret}")
        lines.append("    }")

    return "\n".join(lines)


def emit_complexity(
    engine: QueryEngine,
    threshold: int,
    direction: str,
) -> str:
    """Emit a flowchart with nodes color-coded by complexity."""
    graph = _load_graph(engine)
    nodes = graph.get("nodes", {})
    edges = graph.get("edges", [])

    cc_nodes = {
        nid: n for nid, n in nodes.items() if (n.get("cyclomatic_complexity") or 0) >= threshold
    }
    if not cc_nodes:
        return _empty_diagram(
            f"flowchart {direction}",
            f"No nodes with CC >= {threshold}",
        )

    cc_ids = set(cc_nodes)
    call_edges = [
        (e["source"], e["target"], e.get("confidence", "certain"))
        for e in edges
        if e.get("kind") == "calls" and e["source"] in cc_ids and e["target"] in cc_ids
    ]

    _warn_if_large(cc_nodes)
    lines = [f"flowchart {direction}"]
    for nid, n in cc_nodes.items():
        sid = sanitize_id(nid)
        label = node_label(n)
        cls = complexity_class(n.get("cyclomatic_complexity"))
        lines.append(f'    {sid}["{label}"]:::{cls}')

    for src, tgt, conf in call_edges:
        lines.append(f"    {sanitize_id(src)} {edge_style(conf)} {sanitize_id(tgt)}")

    lines.append("    classDef low fill:rgba(40,167,69,0.2),stroke:#28a745,color:#28a745")
    lines.append("    classDef medium fill:rgba(255,193,7,0.2),stroke:#e6a817,color:#e6a817")
    lines.append("    classDef high fill:rgba(220,53,69,0.2),stroke:#dc3545,color:#dc3545")

    return "\n".join(lines)


def emit_data_flow(
    engine: QueryEngine,
    focus: str | None,
    depth: int,
    direction: str,
) -> str:
    """Emit a flowchart showing paths from entrypoints."""
    surface = engine.attack_surface()
    if not surface:
        return _empty_diagram(
            f"flowchart {direction}",
            "No entrypoints found",
        )

    graph = _load_graph(engine)
    nodes = graph.get("nodes", {})
    ep_ids = {ep["node_id"] for ep in surface}

    all_path_nodes: dict[str, dict[str, Any]] = {}
    all_path_edges: list[tuple[str, str, str]] = []

    targets = _resolve_data_flow_targets(engine, graph, focus, ep_ids)

    for ep_id in ep_ids:
        for tgt_id in targets:
            paths = engine.paths_between(ep_id, tgt_id)
            for path in paths:
                _collect_path(path, nodes, all_path_nodes, all_path_edges)

    if not all_path_nodes:
        return _empty_diagram(
            f"flowchart {direction}",
            "No paths from entrypoints to targets",
        )

    _warn_if_large(all_path_nodes)
    lines = [f"flowchart {direction}"]
    for nid, n in all_path_nodes.items():
        sid = sanitize_id(nid)
        label = node_label(n)
        if nid in ep_ids:
            lines.append(f'    {sid}(["{label}"]):::entrypoint')
        else:
            lines.append(f'    {sid}["{label}"]')

    seen_edges: set[tuple[str, str]] = set()
    for src, tgt, conf in all_path_edges:
        key = (src, tgt)
        if key not in seen_edges:
            seen_edges.add(key)
            lines.append(f"    {sanitize_id(src)} {edge_style(conf)} {sanitize_id(tgt)}")

    lines.append("    classDef entrypoint fill:rgba(0,123,255,0.2),stroke:#007bff,color:#007bff")

    return "\n".join(lines)


def _resolve_data_flow_targets(
    engine: QueryEngine,
    graph: dict[str, Any],
    focus: str | None,
    ep_ids: set[str],
) -> list[str]:
    """Determine target node IDs for data-flow diagrams."""
    if focus:
        return [_require_focus_node(graph, focus)]
    hotspots = engine.complexity_hotspots(threshold=10)
    return [h["id"] for h in hotspots if h["id"] not in ep_ids][:10]


def _collect_path(
    path: list[str],
    nodes: dict[str, dict[str, Any]],
    out_nodes: dict[str, dict[str, Any]],
    out_edges: list[tuple[str, str, str]],
) -> None:
    """Add nodes and edges from a single path to collectors."""
    for nid in path:
        if nid not in out_nodes and nid in nodes:
            out_nodes[nid] = nodes[nid]
    for i in range(len(path) - 1):
        out_edges.append((path[i], path[i + 1], "certain"))


# ── Shared rendering helpers ─────────────────────────────────────


def _render_flowchart(
    nodes: dict[str, dict[str, Any]],
    edges: list[tuple[str, str, str]],
    direction: str,
) -> str:
    """Render a generic Mermaid flowchart from nodes and edges."""
    lines = [f"flowchart {direction}"]
    for nid, n in nodes.items():
        sid = sanitize_id(nid)
        label = node_label(n)
        lines.append(f'    {sid}["{label}"]')
    for src, tgt, conf in edges:
        lines.append(f"    {sanitize_id(src)} {edge_style(conf)} {sanitize_id(tgt)}")
    return "\n".join(lines)


def _empty_diagram(header: str, message: str) -> str:
    """Return a minimal diagram with a note about missing data."""
    sid = sanitize_id(message.replace(" ", "_"))
    return f"{header}\n    {sid}[{message}]"


def _warn_if_large(nodes: dict[str, Any]) -> None:
    """Print a warning if the node set exceeds the cap."""
    if len(nodes) > _MAX_NODES:
        print(
            f"Warning: {len(nodes)} nodes exceeds {_MAX_NODES}. "
            "Consider using --focus to scope the diagram.",
            file=sys.stderr,
        )


# ── CLI entry point ──────────────────────────────────────────────


def add_diagram_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the diagram CLI options on a parser or subparser.

    Shared by the standalone ``trailmark.diagram`` entry point and the
    ``trailmark diagram`` subcommand so both expose an identical interface.
    """
    parser.add_argument("--target", "-t", required=True, help="Directory to analyze")
    parser.add_argument("--language", "-l", default="python", help="Source language")
    parser.add_argument(
        "--type",
        "-T",
        required=True,
        choices=_DIAGRAM_TYPES,
        dest="diagram_type",
        help="Diagram type",
    )
    parser.add_argument("--focus", "-f", default=None, help="Focus node name")
    parser.add_argument("--depth", "-d", type=int, default=2, help="BFS depth")
    parser.add_argument(
        "--direction",
        default="TB",
        choices=("TB", "LR"),
        help="Layout direction",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=10,
        help="Complexity threshold",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    p = argparse.ArgumentParser(description="Generate Mermaid diagrams from Trailmark graphs.")
    add_diagram_arguments(p)
    return p.parse_args(argv)


def render_diagram(engine: QueryEngine, args: argparse.Namespace) -> str:
    """Dispatch to the emitter for ``args.diagram_type`` and return Mermaid text.

    ``args.diagram_type`` is constrained to ``_DIAGRAM_TYPES`` by argparse, so
    the final branch handles ``containment`` without a fallback path.
    """
    if args.diagram_type == "call-graph":
        return emit_call_graph(engine, args.focus, args.depth, args.direction)
    if args.diagram_type == "complexity":
        return emit_complexity(engine, args.threshold, args.direction)
    if args.diagram_type == "data-flow":
        return emit_data_flow(engine, args.focus, args.depth, args.direction)
    if args.diagram_type == "class-hierarchy":
        return emit_class_hierarchy(engine, args.direction)
    if args.diagram_type == "module-deps":
        return emit_module_deps(engine, args.direction)
    return emit_containment(engine, args.direction)


def main(argv: list[str] | None = None) -> int:
    """Entry point: parse args, build graph, emit diagram."""
    args = parse_args(argv)
    engine = build_engine(args.target, args.language)
    print(render_diagram(engine, args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
