"""High-level query API for Trailmark code graphs."""

from __future__ import annotations

import importlib
import json
from dataclasses import asdict
from typing import Any

from trailmark.analysis.augment import augment_from_sarif, augment_from_weaudit
from trailmark.analysis.diff import compute_diff
from trailmark.analysis.entrypoints import detect_entrypoints
from trailmark.analysis.preanalysis import run_preanalysis
from trailmark.models.annotations import Annotation, AnnotationKind
from trailmark.models.edges import CodeEdge
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit
from trailmark.parsers.base import LanguageParser
from trailmark.storage.graph_store import GraphStore

_PARSER_MAP: dict[str, tuple[str, str]] = {
    "python": ("trailmark.parsers.python", "PythonParser"),
    "javascript": ("trailmark.parsers.javascript", "JavaScriptParser"),
    "typescript": ("trailmark.parsers.typescript", "TypeScriptParser"),
    "php": ("trailmark.parsers.php", "PHPParser"),
    "ruby": ("trailmark.parsers.ruby", "RubyParser"),
    "c": ("trailmark.parsers.c", "CParser"),
    "cpp": ("trailmark.parsers.cpp", "CppParser"),
    "c_sharp": ("trailmark.parsers.csharp", "CSharpParser"),
    "java": ("trailmark.parsers.java", "JavaParser"),
    "go": ("trailmark.parsers.go", "GoParser"),
    "rust": ("trailmark.parsers.rust", "RustParser"),
    "solidity": ("trailmark.parsers.solidity", "SolidityParser"),
    "cairo": ("trailmark.parsers.cairo", "CairoParser"),
    "circom": ("trailmark.parsers.circom", "CircomParser"),
    "haskell": ("trailmark.parsers.haskell", "HaskellParser"),
    "erlang": ("trailmark.parsers.erlang", "ErlangParser"),
    "masm": ("trailmark.parsers.masm", "MasmParser"),
}

_SUPPORTED_LANGUAGES = frozenset(_PARSER_MAP.keys())


def _get_parser(language: str) -> LanguageParser:
    """Lazily import and instantiate a parser for the given language."""
    entry = _PARSER_MAP.get(language)
    if entry is None:
        msg = f"Unsupported language: {language}"
        raise ValueError(msg)
    module = importlib.import_module(entry[0])
    cls = getattr(module, entry[1])
    return cls()


class QueryEngine:
    """Facade for building and querying code graphs."""

    def __init__(self, store: GraphStore) -> None:
        self._store = store

    @classmethod
    def from_directory(
        cls,
        path: str,
        language: str = "python",
        *,
        detect_entrypoints_: bool = True,
    ) -> QueryEngine:
        """Parse a directory and return a ready-to-query engine.

        Entrypoint detection runs automatically so that ``attack_surface()``
        and the entrypoint-dependent preanalysis passes have data to work
        with. Pass ``detect_entrypoints_=False`` to skip it (e.g. when the
        caller wants to drive detection separately).
        """
        parser = _get_parser(language)
        graph = parser.parse_directory(path)
        if detect_entrypoints_:
            graph.entrypoints.update(detect_entrypoints(graph, path))
        store = GraphStore(graph)
        return cls(store)

    @classmethod
    def from_graph(cls, graph: CodeGraph) -> QueryEngine:
        """Create an engine from a pre-built CodeGraph."""
        store = GraphStore(graph)
        return cls(store)

    def diff_against(self, other: QueryEngine) -> dict[str, Any]:
        """Return a structured diff of ``self`` relative to ``other``.

        ``other`` is treated as the "before" state, ``self`` as "after".
        See ``trailmark.analysis.diff.compute_diff`` for the returned
        schema.
        """
        return compute_diff(other._store._graph, self._store._graph)  # noqa: SLF001

    def callers_of(self, name: str) -> list[dict[str, Any]]:
        """Find all callers of a function/method by name."""
        node_id = self._store.find_node_id(name)
        if node_id is None:
            return []
        return [_unit_to_dict(u) for u in self._store.callers_of(node_id)]

    def callees_of(self, name: str) -> list[dict[str, Any]]:
        """Find all functions/methods called by the named unit."""
        node_id = self._store.find_node_id(name)
        if node_id is None:
            return []
        return [_unit_to_dict(u) for u in self._store.callees_of(node_id)]

    def ancestors_of(self, name: str) -> list[dict[str, Any]]:
        """Find every function/method that can transitively reach ``name``.

        The dual of ``callees_of`` extended transitively: given a sensitive
        sink, this surfaces every function that could eventually call it,
        directly or indirectly. Useful for upward slicing during audits.
        """
        node_id = self._store.find_node_id(name)
        if node_id is None:
            return []
        ancestor_ids = self._store.ancestors_of(node_id)
        result: list[dict[str, Any]] = []
        for aid in ancestor_ids:
            unit = self._store._graph.nodes.get(aid)  # noqa: SLF001
            if unit is not None:
                result.append(_unit_to_dict(unit))
        return result

    def reachable_from(self, name: str) -> list[dict[str, Any]]:
        """Find every function/method transitively reachable from ``name``.

        The transitive closure of ``callees_of``.
        """
        node_id = self._store.find_node_id(name)
        if node_id is None:
            return []
        reachable_ids = self._store.reachable_from(node_id)
        result: list[dict[str, Any]] = []
        for rid in reachable_ids:
            unit = self._store._graph.nodes.get(rid)  # noqa: SLF001
            if unit is not None:
                result.append(_unit_to_dict(unit))
        return result

    def paths_between(
        self,
        src: str,
        dst: str,
    ) -> list[list[str]]:
        """Find all call paths between two nodes."""
        src_id = self._store.find_node_id(src)
        dst_id = self._store.find_node_id(dst)
        if src_id is None or dst_id is None:
            return []
        return self._store.paths_between(src_id, dst_id)

    def entrypoint_paths_to(
        self,
        name: str,
        max_depth: int = 20,
    ) -> list[list[str]]:
        """Find call paths from any entrypoint to ``name``.

        Answers the canonical attack-surface question: "given this sink,
        what concrete entrypoint paths can reach it?" Returns a list of
        id-path lists, one per reachable entrypoint.
        """
        node_id = self._store.find_node_id(name)
        if node_id is None:
            return []
        return self._store.entrypoint_paths_to(node_id, max_depth=max_depth)

    def nodes_with_annotation(
        self,
        kind: AnnotationKind,
    ) -> list[dict[str, Any]]:
        """Return every node tagged with the given annotation kind."""
        return [_unit_to_dict(u) for u in self._store.nodes_with_annotation(kind)]

    def functions_that_raise(
        self,
        exception_name: str,
    ) -> list[dict[str, Any]]:
        """Return functions/methods whose parser-detected exception list
        includes the named exception.

        Looks at the ``exception_types`` field parsers populate when they
        extract ``raise``/``throw`` statements. Match is by type name
        (``TypeRef.name``) — modules/generics are ignored.
        """
        result: list[dict[str, Any]] = []
        for unit in self._store._graph.nodes.values():  # noqa: SLF001
            for exc in unit.exception_types:
                if exc.name == exception_name:
                    result.append(_unit_to_dict(unit))
                    break
        return result

    def attack_surface(self) -> list[dict[str, Any]]:
        """List all entrypoints with their trust levels."""
        return [
            {
                "node_id": node_id,
                "trust_level": tag.trust_level.value,
                "kind": tag.kind.value,
                "asset_value": tag.asset_value.value,
                "description": tag.description,
            }
            for node_id, tag in self._store.all_entrypoints()
        ]

    def complexity_hotspots(
        self,
        threshold: int = 10,
    ) -> list[dict[str, Any]]:
        """Find functions with high cyclomatic complexity."""
        nodes = self._store.nodes_by_complexity(threshold)
        return [_unit_to_dict(u) for u in _sort_by_complexity(nodes)]

    def annotate(
        self,
        name: str,
        kind: AnnotationKind,
        description: str,
        source: str = "manual",
    ) -> bool:
        """Add an annotation to a node by name.

        Returns False if the node is not found.
        """
        node_id = self._store.find_node_id(name)
        if node_id is None:
            return False
        annotation = Annotation(
            kind=kind,
            description=description,
            source=source,
        )
        return self._store.add_annotation(node_id, annotation)

    def annotations_of(
        self,
        name: str,
        kind: AnnotationKind | None = None,
    ) -> list[dict[str, Any]]:
        """Get annotations for a node, optionally filtered by kind."""
        node_id = self._store.find_node_id(name)
        if node_id is None:
            return []
        annotations = self._store.annotations_for(node_id)
        if kind is not None:
            annotations = [a for a in annotations if a.kind == kind]
        return [_annotation_to_dict(a) for a in annotations]

    def clear_annotations(
        self,
        name: str,
        kind: AnnotationKind | None = None,
    ) -> bool:
        """Remove annotations from a node.

        Returns False if the node is not found.
        """
        node_id = self._store.find_node_id(name)
        if node_id is None:
            return False
        return self._store.clear_annotations(node_id, kind)

    def summary(self) -> dict[str, Any]:
        """Return a summary of the code graph."""
        store = self._store
        graph = store._graph  # noqa: SLF001
        funcs = [n for n in graph.nodes.values() if n.kind.value in ("function", "method")]
        call_edges = [e for e in graph.edges if e.kind.value == "calls"]
        return {
            "total_nodes": len(graph.nodes),
            "functions": len(funcs),
            "classes": sum(1 for n in graph.nodes.values() if n.kind.value == "class"),
            "call_edges": len(call_edges),
            "dependencies": graph.dependencies,
            "entrypoints": len(graph.entrypoints),
        }

    def preanalysis(self) -> dict[str, Any]:
        """Run all pre-analysis passes and return a summary.

        Computes blast radius, entry point enumeration, privilege
        boundary crossings, and taint propagation. Results are
        stored as annotations and subgraphs on the graph.
        """
        return run_preanalysis(self._store)

    def augment_sarif(self, sarif_path: str) -> dict[str, Any]:
        """Parse a SARIF file and augment the graph with findings."""
        return augment_from_sarif(self._store, sarif_path)

    def augment_weaudit(self, weaudit_path: str) -> dict[str, Any]:
        """Parse a weAudit file and augment the graph with findings."""
        return augment_from_weaudit(self._store, weaudit_path)

    def findings(
        self,
        kind: AnnotationKind | None = None,
    ) -> list[dict[str, Any]]:
        """Return all nodes with finding or audit_note annotations.

        If kind is provided, filters to that specific kind.
        Otherwise returns nodes with either FINDING or AUDIT_NOTE.
        """
        finding_kinds = {AnnotationKind.FINDING, AnnotationKind.AUDIT_NOTE}
        if kind is not None:
            finding_kinds = {kind}
        graph = self._store._graph  # noqa: SLF001
        results: list[dict[str, Any]] = []
        for node_id, anns in graph.annotations.items():
            matching = [a for a in anns if a.kind in finding_kinds]
            if not matching:
                continue
            node = graph.nodes.get(node_id)
            if node is None:
                continue
            entry = _unit_to_dict(node)
            entry["findings"] = [_annotation_to_dict(a) for a in matching]
            results.append(entry)
        return results

    def subgraph(self, name: str) -> list[dict[str, Any]]:
        """Return nodes in a named subgraph."""
        node_ids = self._store.subgraph(name)
        graph = self._store._graph  # noqa: SLF001
        return [_unit_to_dict(graph.nodes[nid]) for nid in sorted(node_ids) if nid in graph.nodes]

    def subgraph_names(self) -> list[str]:
        """Return all registered subgraph names."""
        return sorted(self._store.all_subgraphs())

    def to_json(self, indent: int = 2) -> str:
        """Serialize the full graph to JSON."""
        graph = self._store._graph  # noqa: SLF001
        data = {
            "language": graph.language,
            "root_path": graph.root_path,
            "summary": self.summary(),
            "nodes": {nid: _unit_to_dict(node) for nid, node in graph.nodes.items()},
            "edges": [_edge_to_dict(e) for e in graph.edges],
            "subgraphs": {name: sorted(ids) for name, ids in graph.subgraphs.items()},
        }
        return json.dumps(data, indent=indent, default=str)


def _annotation_to_dict(ann: Annotation) -> dict[str, Any]:
    """Convert an Annotation to a serializable dict."""
    return {
        "kind": ann.kind.value,
        "description": ann.description,
        "source": ann.source,
    }


def _unit_to_dict(unit: CodeUnit) -> dict[str, Any]:
    """Convert a CodeUnit to a serializable dict."""
    d = asdict(unit)
    d["kind"] = unit.kind.value
    return d


def _edge_to_dict(edge: CodeEdge) -> dict[str, Any]:
    """Convert a CodeEdge to a serializable dict."""
    return {
        "source": edge.source_id,
        "target": edge.target_id,
        "kind": edge.kind.value,
        "confidence": edge.confidence.value,
    }


def _sort_by_complexity(nodes: list[CodeUnit]) -> list[CodeUnit]:
    """Sort nodes by cyclomatic complexity, descending."""
    return sorted(
        nodes,
        key=lambda n: n.cyclomatic_complexity or 0,
        reverse=True,
    )
