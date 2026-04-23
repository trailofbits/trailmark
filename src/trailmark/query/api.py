"""High-level query API for Trailmark code graphs."""

from __future__ import annotations

import importlib
import json
from dataclasses import asdict
from pathlib import Path
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
    "swift": ("trailmark.parsers.swift", "SwiftParser"),
    "objc": ("trailmark.parsers.objc", "ObjCParser"),
    "kotlin": ("trailmark.parsers.kotlin", "KotlinParser"),
}

# Extensions used for language auto-detection. Kept in sync with each parser's
# internal _EXTENSIONS tuple. Shared extensions (e.g., `.h` between C and C++)
# are handled by prioritizing the more specific language — C++ is tried before
# plain C when both report files.
_LANGUAGE_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "python": (".py",),
    "javascript": (".js", ".jsx", ".mjs", ".cjs"),
    "typescript": (".ts", ".tsx"),
    "php": (".php",),
    "ruby": (".rb",),
    "c": (".c",),
    "cpp": (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"),
    "c_sharp": (".cs",),
    "java": (".java",),
    "go": (".go",),
    "rust": (".rs",),
    "solidity": (".sol",),
    "cairo": (".cairo",),
    "circom": (".circom",),
    "haskell": (".hs",),
    "erlang": (".erl",),
    "masm": (".masm",),
    "swift": (".swift",),
    # `.h` files can be C, ObjC, or C++; auto-detect only fires on .m/.mm.
    # The parser itself still walks .h when invoked with language="objc".
    "objc": (".m", ".mm"),
    "kotlin": (".kt", ".kts"),
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


def _resolve_languages(path: str, spec: str) -> list[str]:
    """Expand a ``language`` argument into a concrete list of languages.

    Accepts:
    - ``"auto"`` — detect from file extensions under ``path``.
    - ``"python,rust"`` — comma-separated explicit list.
    - ``"python"`` — single language (the common case; returned as a
      single-element list).
    """
    if spec == "auto":
        detected = detect_languages(path)
        if not detected:
            msg = f"No supported languages detected under {path}"
            raise ValueError(msg)
        return detected
    names = [name.strip() for name in spec.split(",") if name.strip()] if "," in spec else [spec]
    for name in names:
        if name not in _PARSER_MAP:
            msg = f"Unsupported language: {name}"
            raise ValueError(msg)
    return names


def _parse_and_merge(path: str, languages: list[str]) -> CodeGraph:
    """Parse ``path`` with each language's parser and merge into one graph."""
    if len(languages) == 1:
        # Preserves pre-polyglot behavior exactly for the common case.
        return _get_parser(languages[0]).parse_directory(path)

    merged = CodeGraph(
        language="polyglot",
        root_path=str(Path(path).resolve()),
    )
    for lang in languages:
        sub = _get_parser(lang).parse_directory(path)
        merged.merge(sub)
    # merge() doesn't touch `language`; preserve the polyglot marker.
    merged.language = "polyglot"
    return merged


def detect_languages(path: str) -> list[str]:
    """Return the sorted list of languages with at least one file under ``path``.

    Detection walks the directory once, classifies each file by extension,
    and returns the languages that have at least one match. Order is the
    order languages are registered in ``_LANGUAGE_EXTENSIONS``, which
    roughly corresponds to popularity and keeps deterministic behavior.
    """
    import os

    root = Path(path)
    if not root.exists():
        return []

    ext_to_language: dict[str, str] = {}
    for lang, exts in _LANGUAGE_EXTENSIONS.items():
        for ext in exts:
            # When languages share an extension (none currently do, but
            # guard against it), the FIRST registration wins.
            ext_to_language.setdefault(ext, lang)

    found: set[str] = set()
    for dirpath, _dirs, files in os.walk(root):
        # Skip common vendor / generated dirs to keep detection snappy.
        if _should_skip_dir(dirpath):
            continue
        for name in files:
            ext = _file_extension(name)
            if ext in ext_to_language:
                found.add(ext_to_language[ext])
        if len(found) == len(_LANGUAGE_EXTENSIONS):
            break

    return [lang for lang in _LANGUAGE_EXTENSIONS if lang in found]


_SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "env",
        ".tox",
        "dist",
        "build",
        "target",
        ".mutants",
        "mutants",
    }
)


def _should_skip_dir(dirpath: str) -> bool:
    """Return True for directories we should exclude from language detection."""
    parts = Path(dirpath).parts
    return any(part in _SKIP_DIR_NAMES for part in parts)


def _file_extension(name: str) -> str:
    """Return the lowercase extension including leading dot, or ''."""
    dot = name.rfind(".")
    if dot < 0:
        return ""
    return name[dot:].lower()


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

        ``language`` accepts a specific language name (e.g. ``"python"``,
        ``"rust"``, ``"solidity"``), ``"auto"`` to detect and merge every
        language with at least one matching file under ``path``, or a
        comma-separated list like ``"python,rust"`` for an explicit set.

        Entrypoint detection runs automatically so that ``attack_surface()``
        and the entrypoint-dependent preanalysis passes have data to work
        with. Pass ``detect_entrypoints_=False`` to skip it (e.g. when the
        caller wants to drive detection separately).
        """
        languages = _resolve_languages(path, language)
        graph = _parse_and_merge(path, languages)
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
