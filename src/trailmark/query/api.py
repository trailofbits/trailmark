"""High-level query API for Trailmark code graphs."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

import trailmark.parse as _parse_api
from trailmark import __version__
from trailmark.analysis.augment import augment_from_sarif, augment_from_weaudit
from trailmark.analysis.binary import augment_from_binary_graph
from trailmark.analysis.diff import compute_diff
from trailmark.analysis.entrypoints import detect_entrypoints
from trailmark.analysis.preanalysis import run_preanalysis
from trailmark.analysis.proxies import ensure_proxy_nodes
from trailmark.analysis.registrations import discover_registrations, mark_registered_entrypoints
from trailmark.models.annotations import Annotation, AnnotationKind
from trailmark.models.edges import CodeEdge, EdgeKind
from trailmark.models.endpoints import EndpointRegistration
from trailmark.models.execution import ExecutionFlow, ExecutionPoint
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit, NodeOrigin, TypeParameter, TypeRef
from trailmark.source_policy import SourcePolicy
from trailmark.storage.graph_store import GraphStore


def detect_languages(path: str) -> list[str]:
    """Detect languages under ``path``.

    Deprecated: import from ``trailmark.parse`` instead.
    """
    return _parse_api.detect_languages(path)


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
        allow_empty: bool = False,
        strict: bool = False,
        source_policy: SourcePolicy | None = None,
    ) -> QueryEngine:
        """Parse a directory and return a ready-to-query engine.

        ``language`` accepts a specific language name (e.g. ``"python"``,
        ``"rust"``, ``"solidity"``), ``"auto"`` to detect and merge every
        language with at least one matching file under ``path``, or a
        comma-separated list like ``"python,rust"`` for an explicit set.

        Missing/non-directory roots and zero inspected source files raise
        ``ValueError``. Set ``allow_empty=True`` for an intentional empty
        input. ``summary()`` reports selected languages and source-file counts.
        ``strict=True`` raises ``ValueError`` for parse or unsupported-syntax
        diagnostics in any selected file.

        Entrypoint detection runs automatically so that ``attack_surface()``
        and the entrypoint-dependent preanalysis passes have data to work
        with. Pass ``detect_entrypoints_=False`` to skip it (e.g. when the
        caller wants to drive detection separately).
        """
        graph = _parse_api.parse_directory(
            path,
            language=language,
            allow_empty=allow_empty,
            strict=strict,
            source_policy=source_policy,
        )
        if detect_entrypoints_:
            graph.entrypoints.update(detect_entrypoints(graph, path))
            discover_registrations(graph)
            mark_registered_entrypoints(graph)
        store = GraphStore(graph)
        return cls(store)

    @classmethod
    def from_graph(cls, graph: CodeGraph) -> QueryEngine:
        """Create an engine retaining the supplied mutable graph.

        Proxy normalization updates ``graph`` in place. Complete direct node
        and edge edits before construction, then use the engine's mutation
        APIs so its topology indexes stay synchronized.
        """
        store = GraphStore(ensure_proxy_nodes(graph))
        return cls(store)

    def diff_against(self, other: QueryEngine) -> dict[str, Any]:
        """Return a structured diff of ``self`` relative to ``other``.

        ``other`` is treated as the "before" state, ``self`` as "after".
        See ``trailmark.analysis.diff.compute_diff`` for the returned
        schema.
        """
        return compute_diff(other._store._graph, self._store._graph)  # noqa: SLF001

    def endpoint_inventory(self, name: str | None = None) -> dict[str, Any]:
        """Export typed registrations and unresolved registration diagnostics.

        This inventory does not equate source declarations with deployment.
        ``name`` restricts bindings to a resolved handler name or node ID.
        """
        node_id = self._store.find_node_id(name) if name is not None else None
        records = (
            []
            if name is not None and node_id is None
            else [r.to_dict() for r in self._store.endpoint_registrations(node_id)]
        )
        return {
            "schema_version": 1,
            "registrations": records,
            "diagnostics": list(self._store._graph.registration_diagnostics),  # noqa: SLF001
        }

    def add_registration(self, registration: EndpointRegistration) -> None:
        """Add an explicitly supplied registration after validating its targets."""
        self._store.add_registration(registration)

    def add_execution_flow(self, flow: ExecutionFlow) -> None:
        """Add an explicitly evidenced, source-ranged execution transition."""
        self._store.add_execution_flow(flow)

    def execution_evidence(self, name: str | None = None) -> list[dict[str, Any]]:
        """Export supplied gate, rejection, middleware and continuation evidence."""
        node_id = self._store.find_node_id(name) if name is not None else None
        if name is not None and node_id is None:
            return []
        return [asdict(flow) for flow in self._store.execution_evidence(node_id)]

    def authentication_paths(
        self,
        roots: tuple[ExecutionPoint, ...],
        target: ExecutionPoint,
        *,
        max_depth: int = 40,
        max_steps: int = 10_000,
    ) -> dict[str, Any]:
        """Query explicit execution facts from nonempty, reviewed pre-auth roots."""
        from trailmark.analysis.execution import authentication_paths

        for point in (*roots, target):
            self._store.validate_execution_point(point)
        return authentication_paths(
            self._store._graph,
            roots,
            target,
            max_depth=max_depth,
            max_steps=max_steps,  # noqa: SLF001
        )

    def import_native_registrations(
        self,
        path: str,
        *,
        framework: str,
        source_root: str | None = None,
        path_mappings: tuple[tuple[str, str], ...] = (),
    ) -> int:
        """Opt in to a native Django/Symfony/Magento/Medusa registry snapshot."""
        from trailmark.analysis._native_registrations import import_native_registrations

        graph = self._store._graph  # noqa: SLF001
        count = import_native_registrations(
            graph, path, framework=framework, source_root=source_root, path_mappings=path_mappings
        )
        mark_registered_entrypoints(graph)
        from trailmark.analysis._graphql_registrations import link_graphql_transports
        from trailmark.analysis._registration_context import RegistrationContext

        link_graphql_transports(RegistrationContext(graph))
        return count

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

    def state_read_by(self, name: str) -> list[dict[str, Any]]:
        """Find persistent state directly read by the named unit."""
        node_id = self._store.find_node_id(name)
        if node_id is None:
            return []
        return [_unit_to_dict(u) for u in self._store.state_read_by(node_id)]

    def state_written_by(self, name: str) -> list[dict[str, Any]]:
        """Find persistent state directly written by the named unit."""
        node_id = self._store.find_node_id(name)
        if node_id is None:
            return []
        return [_unit_to_dict(u) for u in self._store.state_written_by(node_id)]

    def readers_of(self, name: str) -> list[dict[str, Any]]:
        """Find units that directly read the named state node."""
        node_id = self._store.find_node_id(name)
        if node_id is None:
            return []
        return [_unit_to_dict(u) for u in self._store.readers_of(node_id)]

    def writers_of(self, name: str) -> list[dict[str, Any]]:
        """Find units that directly write the named state node."""
        node_id = self._store.find_node_id(name)
        if node_id is None:
            return []
        return [_unit_to_dict(u) for u in self._store.writers_of(node_id)]

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
        for aid in sorted(ancestor_ids):
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
        for rid in sorted(reachable_ids):
            unit = self._store._graph.nodes.get(rid)  # noqa: SLF001
            if unit is not None:
                result.append(_unit_to_dict(unit))
        return result

    def paths_between(
        self,
        src: str,
        dst: str,
        *,
        max_depth: int = 20,
    ) -> list[list[str]]:
        """Find nontrivial call paths of at most ``max_depth`` edges (positive)."""
        src_id = self._store.find_node_id(src)
        dst_id = self._store.find_node_id(dst)
        return self._store.paths_between(
            src_id if src_id is not None else src,
            dst_id if dst_id is not None else dst,
            max_depth=max_depth,
        )

    def connect_subgraphs(
        self,
        source: str,
        target: str,
        *,
        max_depth: int = 20,
        edge_kinds: tuple[str | EdgeKind, ...] = ("calls",),
    ) -> list[list[str]]:
        """Connect named subgraphs with at most ``max_depth`` edges (positive)."""
        kinds = _coerce_edge_kinds(edge_kinds)
        return self._store.connect_subgraphs(
            source,
            target,
            max_depth=max_depth,
            edge_kinds=kinds,
        )

    def entrypoint_paths_to(
        self,
        name: str,
        max_depth: int = 20,
    ) -> list[list[str]]:
        """Find call paths from any entrypoint to ``name``.

        Answers the canonical attack-surface question: "given this sink,
        what concrete entrypoint paths can reach it?" Returns a list of
        nontrivial ID paths bounded by ``max_depth`` edges (positive).
        """
        node_id = self._store.find_node_id(name)
        return self._store.entrypoint_paths_to(
            node_id if node_id is not None else name, max_depth=max_depth
        )

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
        result: list[dict[str, Any]] = []
        for node_id, tag in self._store.all_entrypoints():
            item: dict[str, Any] = {
                "node_id": node_id,
                "trust_level": tag.trust_level.value,
                "kind": tag.kind.value,
                "asset_value": tag.asset_value.value,
                "description": tag.description,
            }
            unit = self._store.unit(node_id)
            if unit is not None and unit.attributes:
                item["attributes"] = dict(unit.attributes)
            result.append(item)
        return result

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
        source_file_counts = store.source_file_counts()
        return {
            "total_nodes": len(graph.nodes),
            "functions": len(funcs),
            "classes": sum(1 for n in graph.nodes.values() if n.kind.value == "class"),
            "proxies": sum(1 for n in graph.nodes.values() if n.kind.value == "proxy"),
            "call_edges": len(call_edges),
            "dependencies": list(graph.dependencies),
            "entrypoints": len(graph.entrypoints),
            "languages": list(source_file_counts),
            "source_files": sum(source_file_counts.values()),
            "source_file_counts": source_file_counts,
            **({"source_policy": graph.source_policy} if graph.source_policy else {}),
            "parse_diagnostics": self.parse_diagnostics(),
        }

    def parse_diagnostics(self) -> list[dict[str, Any]]:
        """Report grammar errors and explicitly unsupported source constructs.

        An empty list means no diagnostic was recorded, not that every language
        feature was modeled. Inspected-file counts remain independent.
        """
        return [asdict(item) for item in self._store.parse_diagnostics()]

    def unresolved_calls(self, name: str | None = None) -> list[dict[str, Any]]:
        """Report unresolved call sites, including raw targets and source spans.

        ``name`` restricts the report to one caller. A missing caller returns
        ``[]``; omitting it reports every unresolved call in the graph.
        """
        node_id = self._store.find_node_id(name) if name is not None else None
        if name is not None and node_id is None:
            return []
        result: list[dict[str, Any]] = []
        for edge in self._store.unresolved_calls(node_id):
            target = self._store.unit(edge.target_id)
            attributes = dict(target.attributes) if target is not None else {}
            raw_target = str(
                dict(edge.attributes).get(
                    "raw_target", attributes.get("raw_symbol", edge.target_id)
                )
            )
            row = _edge_to_dict(edge)
            row["raw_target"] = raw_target
            row["callee"] = raw_target.rsplit(".", 1)[-1].rsplit(":", 1)[-1].strip()
            row["location"] = asdict(edge.location) if edge.location is not None else None
            result.append(row)
        return result

    def call_resolution(self) -> dict[str, Any]:
        """Count graph call sites and group unresolved targets by callee label.

        These counts describe extracted edges, not completeness of the source
        program: opaque macros and implicit calls may contribute no edges.
        """
        calls = self._store.call_edges()
        unresolved = self.unresolved_calls()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in unresolved:
            grouped.setdefault(row["callee"], []).append(row)
        return {
            "call_sites": len(calls),
            "resolved_call_sites": len(calls) - len(unresolved),
            "unresolved_call_sites": len(unresolved),
            "unresolved_targets": len({row["target"] for row in unresolved}),
            "affected_callers": len({row["source"] for row in unresolved}),
            "unresolved_by_callee": [
                {
                    "callee": callee,
                    "call_sites": len(grouped[callee]),
                    "targets": len({row["target"] for row in grouped[callee]}),
                }
                for callee in sorted(grouped, key=lambda name: (-len(grouped[name]), name))
            ],
        }

    def preanalysis(self) -> dict[str, Any]:
        """Run all pre-analysis passes and return a summary.

        Computes blast radius, entry point enumeration, privilege
        boundary crossings, and taint propagation. Results are
        stored as annotations and subgraphs on the graph.
        """
        return run_preanalysis(self._store)

    def augment_sarif(self, sarif_path: str, *, replace_existing: bool = True) -> dict[str, Any]:
        """Import SARIF findings, replacing prior SARIF imports unless disabled."""
        return augment_from_sarif(self._store, sarif_path, replace_existing=replace_existing)

    def augment_weaudit(
        self, weaudit_path: str, *, replace_existing: bool = True
    ) -> dict[str, Any]:
        """Import weAudit findings, replacing prior weAudit imports unless disabled."""
        return augment_from_weaudit(self._store, weaudit_path, replace_existing=replace_existing)

    def augment_binary(
        self,
        graph_path: str,
        *,
        connect_sources: bool = True,
    ) -> dict[str, Any]:
        """Import a Trailmark binary graph JSON file into this graph."""
        return augment_from_binary_graph(
            self._store,
            graph_path,
            connect_sources=connect_sources,
        )

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

    def subgraph_edges(
        self,
        name: str,
        *,
        edge_kinds: tuple[str | EdgeKind, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """Return induced edges whose endpoints are both in a named subgraph."""
        kinds = None if edge_kinds is None else _coerce_edge_kinds(edge_kinds)
        return [_edge_to_dict(e) for e in self._store.subgraph_edges(name, kinds)]

    def subgraph_names(self) -> list[str]:
        """Return all registered subgraph names."""
        return sorted(self._store.all_subgraphs())

    def generic_parameters(self, name: str) -> list[dict[str, Any]]:
        """Return generic type parameters declared by the named node."""
        node_id = self._store.find_node_id(name)
        if node_id is None:
            return []
        graph = self._store._graph  # noqa: SLF001
        unit = graph.nodes.get(node_id)
        if unit is None:
            return []
        return [_type_parameter_to_dict(tp) for tp in unit.type_parameters]

    def type_references(self, name: str) -> list[dict[str, Any]]:
        """Return parameter, return, exception, and generic-bound type refs."""
        node_id = self._store.find_node_id(name)
        if node_id is None:
            return []
        graph = self._store._graph  # noqa: SLF001
        unit = graph.nodes.get(node_id)
        if unit is None:
            return []
        refs = _unit_type_refs(unit)
        return [_type_ref_to_dict(ref) for ref in refs]

    def to_json(self, indent: int = 2) -> str:
        """Export topology, selected node metadata, subgraphs, and summary.

        Includes the exporting Trailmark version and JSON schema version.
        Annotations, entrypoint tags, and call-site locations are omitted;
        this export does not preserve all analysis state for persistence.
        """
        graph = self._store._graph  # noqa: SLF001
        data = {
            "trailmark_version": __version__,
            "schema_version": 1,
            "language": graph.language,
            "root_path": graph.root_path,
            "summary": self.summary(),
            "nodes": {nid: _unit_to_dict(node) for nid, node in graph.nodes.items()},
            "edges": [_edge_to_dict(e) for e in graph.edges],
            "subgraphs": {name: sorted(ids) for name, ids in graph.subgraphs.items()},
            **(
                {"endpoint_inventory": self.endpoint_inventory()}
                if graph.registrations or graph.registration_diagnostics
                else {}
            ),
            **({"source_policy": graph.source_policy} if graph.source_policy else {}),
            **({"execution_evidence": self.execution_evidence()} if graph.execution_flows else {}),
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
    if unit.origin == NodeOrigin.SOURCE:
        d.pop("origin", None)
    else:
        d["origin"] = unit.origin.value
    if not unit.type_parameters:
        d.pop("type_parameters", None)
    if unit.attributes:
        d["attributes"] = _attributes_to_dict(unit.attributes)
    else:
        d.pop("attributes", None)
    return d


def _edge_to_dict(edge: CodeEdge) -> dict[str, Any]:
    """Convert a CodeEdge to a serializable dict."""
    result: dict[str, Any] = {
        "source": edge.source_id,
        "target": edge.target_id,
        "kind": edge.kind.value,
        "confidence": edge.confidence.value,
    }
    if edge.attributes:
        result["attributes"] = _attributes_to_dict(edge.attributes)
    if edge.kind in {EdgeKind.READS, EdgeKind.WRITES} and edge.location is not None:
        result["location"] = asdict(edge.location)
    return result


def _coerce_edge_kinds(values: tuple[str | EdgeKind, ...]) -> frozenset[EdgeKind]:
    """Convert public edge-kind values into EdgeKind members."""
    return frozenset(value if isinstance(value, EdgeKind) else EdgeKind(value) for value in values)


def _attributes_to_dict(attributes: tuple[tuple[str, Any], ...]) -> dict[str, Any]:
    return dict(attributes)


def _type_ref_to_dict(type_ref: TypeRef) -> dict[str, Any]:
    return {
        "name": type_ref.name,
        "module": type_ref.module,
        "generic_args": [_type_ref_to_dict(arg) for arg in type_ref.generic_args],
    }


def _type_parameter_to_dict(type_parameter: TypeParameter) -> dict[str, Any]:
    return {
        "name": type_parameter.name,
        "constraints": [_type_ref_to_dict(t) for t in type_parameter.constraints],
        "default": (
            _type_ref_to_dict(type_parameter.default)
            if type_parameter.default is not None
            else None
        ),
        "variance": type_parameter.variance,
    }


def _unit_type_refs(unit: CodeUnit) -> list[TypeRef]:
    refs: list[TypeRef] = []
    for param in unit.parameters:
        if param.type_ref is not None:
            refs.extend(_flatten_type_ref(param.type_ref))
    if unit.return_type is not None:
        refs.extend(_flatten_type_ref(unit.return_type))
    for exc in unit.exception_types:
        refs.extend(_flatten_type_ref(exc))
    for type_param in unit.type_parameters:
        for constraint in type_param.constraints:
            refs.extend(_flatten_type_ref(constraint))
        if type_param.default is not None:
            refs.extend(_flatten_type_ref(type_param.default))
    return refs


def _flatten_type_ref(type_ref: TypeRef) -> list[TypeRef]:
    refs = [type_ref]
    for arg in type_ref.generic_args:
        refs.extend(_flatten_type_ref(arg))
    return refs


def _sort_by_complexity(nodes: list[CodeUnit]) -> list[CodeUnit]:
    """Sort nodes by cyclomatic complexity, descending."""
    return sorted(
        nodes,
        key=lambda n: n.cyclomatic_complexity or 0,
        reverse=True,
    )
