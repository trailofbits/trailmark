"""Rustworkx-backed graph storage with query methods."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import fields, replace

import rustworkx as rx

from trailmark.models.annotations import (
    Annotation,
    AnnotationKind,
    EntrypointTag,
)
from trailmark.models.binary import BinaryArtifact, BinaryRange, BinarySourceMapping
from trailmark.models.diagnostics import ParseDiagnostic
from trailmark.models.edges import CodeEdge, EdgeKind
from trailmark.models.entrypoints import EntrypointEvidence
from trailmark.models.graph import CodeGraph
from trailmark.models.lookup import resolve_node_id
from trailmark.models.nodes import CodeUnit, NodeKind, NodeOrigin
from trailmark.storage.binary_index import IntervalIndex

_CALL_EDGE_KINDS = frozenset({EdgeKind.CALLS})


def _validate_max_depth(max_depth: int) -> None:
    if max_depth < 1:
        msg = "max_depth must be at least 1 (measured in edges)"
        raise ValueError(msg)


class GraphStore:
    """Index a shared mutable graph with rustworkx for fast traversals.

    Direct node or edge edits to the supplied graph require ``rebuild_index``.
    Annotation and subgraph edits do not affect the topology indexes.
    """

    def __init__(self, graph: CodeGraph) -> None:
        self._graph = graph
        for fact in tuple(graph.entrypoint_evidence.values()):
            self.add_entrypoint_evidence(fact)
        self.rebuild_index()

    def source_file_counts(self) -> dict[str, int]:
        """Return inspected source-file counts by selected parser language."""
        return dict(self._graph.source_file_counts)

    def source_policy(self) -> dict[str, object]:
        """Return a detached copy of source selection and file-level decisions."""
        return deepcopy(self._graph.source_policy)

    def entrypoint_evidence(self, node_id: str | None = None) -> list[EntrypointEvidence]:
        """Return immutable discovery facts, optionally scoped to a known node."""
        return [
            fact
            for fact in self._graph.entrypoint_evidence.values()
            if node_id is None or fact.node_id == node_id
        ]

    def add_entrypoint_evidence(self, evidence: EntrypointEvidence) -> None:
        """Validate referenced nodes without promoting candidates to entrypoints."""
        for node_id in (evidence.node_id, evidence.source_id):
            if node_id is not None and node_id not in self._graph.nodes:
                raise ValueError(f"Unknown entrypoint evidence node: {node_id}")
        self._graph.add_entrypoint_evidence(evidence)

    def parse_diagnostics(self) -> list[ParseDiagnostic]:
        """Return source analysis diagnostics as immutable model objects."""
        return list(self._graph.parse_diagnostics)

    def rebuild_index(self) -> None:
        """Rebuild all rustworkx indexes after graph nodes or edges change."""
        self._digraph: rx.PyDiGraph[str, CodeEdge] = rx.PyDiGraph()
        self._id_to_idx: dict[str, int] = {}
        self._idx_to_id: dict[int, str] = {}
        self._projection_cache: dict[
            frozenset[EdgeKind],
            tuple[
                rx.PyDiGraph[str, CodeEdge],
                dict[str, int],
                dict[int, str],
            ],
        ] = {}
        self._build_index()
        self._binary_ranges = {
            artifact: IntervalIndex(
                row for row in self._graph.binary_ranges.values() if row.artifact_id == artifact
            )
            for artifact in self._graph.binary_artifacts
        }
        self._binary_mappings = {
            artifact: IntervalIndex(
                mapping
                for mapping in self._graph.binary_source_mappings.values()
                if mapping.range_id in self._graph.binary_ranges
                and self._graph.binary_ranges[mapping.range_id].artifact_id == artifact
            )
            for artifact in self._graph.binary_artifacts
        }
        self._binary_node_ranges: dict[str, set[str]] = {}
        self._binary_range_mappings: dict[str, list[BinarySourceMapping]] = {}
        for row in self._graph.binary_ranges.values():
            self._binary_node_ranges.setdefault(row.node_id, set()).add(row.id)
        for mapping in self._graph.binary_source_mappings.values():
            self._binary_range_mappings.setdefault(mapping.range_id, []).append(mapping)
            if mapping.source_node_id is not None:
                self._binary_node_ranges.setdefault(mapping.source_node_id, set()).add(
                    mapping.range_id
                )
        (
            self._call_digraph,
            self._call_id_to_idx,
            self._call_idx_to_id,
        ) = self._projection_for(_CALL_EDGE_KINDS)

    def add_binary_graph(self, incoming: CodeGraph) -> None:
        """Validate and commit native evidence atomically, deduplicating reimports."""
        staged = deepcopy(self._graph)
        incoming = deepcopy(incoming)
        for key, node in incoming.nodes.items():
            previous = staged.nodes.get(key)
            if key != node.id or (
                previous is not None and replace(previous, location=node.location) != node
            ):
                raise ValueError(f"Binary node collides with an existing node: {key}")
        for key, artifact in incoming.binary_artifacts.items():
            if (
                key != artifact.id
                or len(artifact.sha256) != 64
                or any(c not in "0123456789abcdef" for c in artifact.sha256)
            ):
                raise ValueError("Malformed binary artifact identity")
            previous = staged.binary_artifacts.get(key)
            if previous is not None:
                diagnostics = tuple(dict.fromkeys(previous.diagnostics + artifact.diagnostics))
                incoming.binary_artifacts[key] = replace(
                    artifact,
                    diagnostics=diagnostics,
                    complete=not diagnostics,
                    scanned_ranges=tuple(
                        sorted(set(previous.scanned_ranges + artifact.scanned_ranges))
                    ),
                )
        for key, row in incoming.binary_ranges.items():
            previous = staged.binary_ranges.get(key)
            if previous is not None and replace(previous, symbols=row.symbols) == row:
                incoming.binary_ranges[key] = replace(
                    row, symbols=tuple(sorted(set(previous.symbols + row.symbols)))
                )
                staged.binary_ranges[key] = incoming.binary_ranges[key]
        existing = set(staged.edges)
        incoming.edges = list(
            dict.fromkeys(edge for edge in incoming.edges if edge not in existing)
        )
        staged.merge(incoming)
        for key, row in staged.binary_ranges.items():
            artifact = staged.binary_artifacts.get(row.artifact_id)
            node = staged.nodes.get(row.node_id)
            if (
                key != row.id
                or artifact is None
                or node is None
                or node.origin != NodeOrigin.BINARY
                or node.kind not in (NodeKind.FUNCTION, NodeKind.BINARY_REGION)
                or row.start < artifact.image_base
                or row.end <= row.start
                or row.rva != row.start - artifact.image_base
                or row.file_offset < artifact.slice_offset
                or row.file_offset + row.end - row.start
                > artifact.slice_offset + artifact.slice_size
            ):
                raise ValueError(f"Invalid binary range: {key}")
        for key, mapping in staged.binary_source_mappings.items():
            row = staged.binary_ranges.get(mapping.range_id)
            source = staged.nodes.get(mapping.source_node_id) if mapping.source_node_id else None
            if (
                key != mapping.id
                or row is None
                or not row.start <= mapping.start < mapping.end <= row.end
                or (
                    mapping.source_node_id is not None
                    and (source is None or source.origin != NodeOrigin.SOURCE)
                )
                or mapping.method not in ("debug", "symbol", "explicit")
                or (mapping.line is not None and mapping.line <= 0)
            ):
                raise ValueError(f"Invalid binary source mapping: {key}")
        if any(
            edge.source_id not in staged.nodes or edge.target_id not in staged.nodes
            for edge in incoming.edges
        ):
            raise ValueError("Binary edge has an unknown endpoint")
        validated = GraphStore(staged)
        for item in fields(staged):
            setattr(self._graph, item.name, getattr(staged, item.name))
        for key, value in validated.__dict__.items():
            if key != "_graph":
                setattr(self, key, value)

    def binary_artifacts(self) -> list[BinaryArtifact]:
        """Return immutable artifact records in stable identity order."""
        return [self._graph.binary_artifacts[key] for key in sorted(self._graph.binary_artifacts)]

    def binary_artifact(self, artifact_id: str) -> BinaryArtifact | None:
        return self._graph.binary_artifacts.get(artifact_id)

    def binary_ranges_for_artifact(self, artifact_id: str) -> list[BinaryRange]:
        index = self._binary_ranges.get(artifact_id)
        return list(index.rows) if index else []

    def binary_mappings_for_artifact(self, artifact_id: str) -> list[BinarySourceMapping]:
        index = self._binary_mappings.get(artifact_id)
        return list(index.rows) if index else []

    def binary_ranges_for_node(
        self,
        node_id: str,
        *,
        artifact: str | None = None,
        line: int | None = None,
    ) -> list[tuple[BinaryRange, tuple[BinarySourceMapping, ...]]]:
        if node_id not in self._graph.nodes:
            return []
        result = []
        for range_id in self._binary_node_ranges.get(node_id, ()):
            row = self._graph.binary_ranges[range_id]
            if artifact is not None and row.artifact_id != artifact:
                continue
            mappings = tuple(
                mapping
                for mapping in self._binary_range_mappings.get(row.id, ())
                if (mapping.source_node_id == node_id or row.node_id == node_id)
                and (line is None or mapping.line == line)
            )
            if row.node_id == node_id and line is None:
                result.append((row, mappings))
            else:
                spans: list[tuple[int, int]] = []
                for mapping in sorted(mappings, key=lambda item: (item.start, item.end)):
                    if spans and spans[-1][1] >= mapping.start:
                        spans[-1] = (spans[-1][0], max(spans[-1][1], mapping.end))
                    else:
                        spans.append((mapping.start, mapping.end))
                for start, end in spans:
                    result.append(
                        (
                            replace(
                                row,
                                start=start,
                                end=end,
                                rva=row.rva + start - row.start,
                                file_offset=row.file_offset + start - row.start,
                            ),
                            tuple(
                                mapping
                                for mapping in mappings
                                if mapping.start < end and start < mapping.end
                            ),
                        )
                    )
        return sorted(result, key=lambda pair: (pair[0].artifact_id, pair[0].start, pair[0].end))

    def source_at_address(
        self,
        artifact_id: str,
        address: int,
        *,
        address_kind: str = "va",
    ) -> list[BinarySourceMapping]:
        if address_kind not in ("va", "rva", "file_offset"):
            raise ValueError("address_kind must be va, rva, or file_offset")
        if not isinstance(address, int) or isinstance(address, bool) or address < 0:
            raise ValueError("address must be a nonnegative integer")
        artifact = self.binary_artifact(artifact_id)
        if artifact is None:
            return []
        if address_kind == "rva":
            address += artifact.image_base
        elif address_kind == "file_offset":
            addresses = {
                row.start + address - row.file_offset
                for row in self.binary_ranges_for_artifact(artifact_id)
                if row.file_offset <= address < row.file_offset + row.end - row.start
            }
            if len(addresses) > 1:
                raise ValueError("File offset maps to ambiguous virtual addresses")
            if not addresses:
                return []
            address = next(iter(addresses))
        index = self._binary_mappings.get(artifact_id)
        return index.containing(address, address + 1) if index else []

    def _build_index(self) -> None:
        """Populate the rustworkx digraph from the CodeGraph."""
        for node_id in self._graph.nodes:
            idx = self._digraph.add_node(node_id)
            self._id_to_idx[node_id] = idx
            self._idx_to_id[idx] = node_id

        for edge in self._graph.edges:
            src_idx = self._id_to_idx.get(edge.source_id)
            tgt_idx = self._id_to_idx.get(edge.target_id)
            if src_idx is not None and tgt_idx is not None:
                self._digraph.add_edge(src_idx, tgt_idx, edge)

    def _projection_for(
        self,
        edge_kinds: frozenset[EdgeKind] | None,
    ) -> tuple[
        rx.PyDiGraph[str, CodeEdge],
        dict[str, int],
        dict[int, str],
    ]:
        """Return a cached projection; ``None`` means the default CALLS projection."""
        key = _CALL_EDGE_KINDS if edge_kinds is None else edge_kinds
        cached = self._projection_cache.get(key)
        if cached is not None:
            return cached

        # Traversals return node paths; repeated call sites share one adjacency.
        # Keep their individual edges in the raw graph and the full index.
        # Projection payloads are last-site records, not aggregate evidence.
        digraph: rx.PyDiGraph[str, CodeEdge] = rx.PyDiGraph(multigraph=False)
        id_to_idx: dict[str, int] = {}
        idx_to_id: dict[int, str] = {}
        for node_id in self._graph.nodes:
            idx = digraph.add_node(node_id)
            id_to_idx[node_id] = idx
            idx_to_id[idx] = node_id

        for edge in self._graph.edges:
            if edge.kind not in key:
                continue
            src_idx = id_to_idx.get(edge.source_id)
            tgt_idx = id_to_idx.get(edge.target_id)
            if src_idx is not None and tgt_idx is not None:
                digraph.add_edge(src_idx, tgt_idx, edge)

        result = (digraph, id_to_idx, idx_to_id)
        self._projection_cache[key] = result
        return result

    def _idx(self, node_id: str) -> int | None:
        return self._id_to_idx.get(node_id)

    def _node(self, node_id: str) -> CodeUnit | None:
        return self._graph.nodes.get(node_id)

    def unit(self, node_id: str) -> CodeUnit | None:
        """Return the graph unit for ``node_id`` if it exists."""
        return self._node(node_id)

    def call_edges(self) -> list[CodeEdge]:
        """Return calls with endpoints present in the indexed graph."""
        return [
            edge
            for edge in self._graph.edges
            if edge.kind == EdgeKind.CALLS
            and edge.source_id in self._id_to_idx
            and edge.target_id in self._id_to_idx
        ]

    def unresolved_calls(self, node_id: str | None = None) -> list[CodeEdge]:
        """Return unresolved call sites, optionally restricted to one caller."""
        if node_id is not None and self._idx(node_id) is None:
            return []
        return [
            edge
            for edge in self._graph.unresolved_calls()
            if edge.source_id in self._id_to_idx
            and edge.target_id in self._id_to_idx
            and (node_id is None or edge.source_id == node_id)
        ]

    def callers_of(self, node_id: str) -> list[CodeUnit]:
        """Return all nodes that call the given node."""
        target_idx = self._idx(node_id)
        if target_idx is None:
            return []
        pred_ids: list[str] = self._digraph.predecessors(target_idx)
        return self._filter_by_edge_kind(
            pred_ids,
            node_id,
            EdgeKind.CALLS,
            reverse=True,
        )

    def callees_of(self, node_id: str) -> list[CodeUnit]:
        """Return all nodes called by the given node."""
        source_idx = self._idx(node_id)
        if source_idx is None:
            return []
        succ_ids: list[str] = self._digraph.successors(source_idx)
        return self._filter_by_edge_kind(
            succ_ids,
            node_id,
            EdgeKind.CALLS,
            reverse=False,
        )

    def state_read_by(self, node_id: str) -> list[CodeUnit]:
        """Return distinct state nodes directly read by a unit."""
        return self._access_neighbors(node_id, EdgeKind.READS, reverse=False)

    def state_written_by(self, node_id: str) -> list[CodeUnit]:
        """Return distinct state nodes directly written by a unit."""
        return self._access_neighbors(node_id, EdgeKind.WRITES, reverse=False)

    def readers_of(self, node_id: str) -> list[CodeUnit]:
        """Return distinct units that directly read a state node."""
        return self._access_neighbors(node_id, EdgeKind.READS, reverse=True)

    def writers_of(self, node_id: str) -> list[CodeUnit]:
        """Return distinct units that directly write a state node."""
        return self._access_neighbors(node_id, EdgeKind.WRITES, reverse=True)

    def _access_neighbors(
        self,
        node_id: str,
        kind: EdgeKind,
        *,
        reverse: bool,
    ) -> list[CodeUnit]:
        idx = self._idx(node_id)
        if idx is None:
            return []
        neighbors: list[str] = (
            self._digraph.predecessors(idx) if reverse else self._digraph.successors(idx)
        )
        return self._filter_by_edge_kind(sorted(set(neighbors)), node_id, kind, reverse=reverse)

    def _filter_by_edge_kind(
        self,
        neighbor_ids: list[str],
        anchor_id: str,
        kind: EdgeKind,
        *,
        reverse: bool,
    ) -> list[CodeUnit]:
        """Filter neighbors by edge kind between them and anchor."""
        anchor_idx = self._id_to_idx[anchor_id]
        result: list[CodeUnit] = []
        for neighbor_id in neighbor_ids:
            neighbor_idx = self._id_to_idx.get(neighbor_id)
            if neighbor_idx is None:
                continue
            if reverse:
                src, tgt = neighbor_idx, anchor_idx
            else:
                src, tgt = anchor_idx, neighbor_idx
            edges = self._digraph.get_all_edge_data(src, tgt)
            if any(e.kind == kind for e in edges):
                node = self._node(neighbor_id)
                if node is not None:
                    result.append(node)
        return result

    def paths_between(
        self,
        src_id: str,
        dst_id: str,
        max_depth: int = 20,
        edge_kinds: frozenset[EdgeKind] | None = None,
    ) -> list[list[str]]:
        """Find nontrivial simple paths of at most ``max_depth`` edges.

        CALLS is the default relation. ``max_depth`` must be positive.
        """
        _validate_max_depth(max_depth)
        digraph, id_to_idx, idx_to_id = self._projection_for(edge_kinds)
        src_idx = id_to_idx.get(src_id)
        dst_idx = id_to_idx.get(dst_id)
        if src_idx is None or dst_idx is None or src_idx == dst_idx:
            return []
        raw_paths: list[list[int]] = rx.digraph_all_simple_paths(
            digraph,
            src_idx,
            dst_idx,
            # rustworkx bounds path vertices; our public depth counts edges.
            cutoff=max_depth + 1,
        )
        return [[idx_to_id[i] for i in path] for path in raw_paths]

    def reachable_from(
        self,
        node_id: str,
        edge_kinds: frozenset[EdgeKind] | None = None,
    ) -> set[str]:
        """Return node IDs reachable from the given node over CALLS by default."""
        digraph, id_to_idx, idx_to_id = self._projection_for(edge_kinds)
        idx = id_to_idx.get(node_id)
        if idx is None:
            return set()
        descendants = rx.descendants(digraph, idx)
        return {idx_to_id[i] for i in descendants}

    def nodes_with_annotation(
        self,
        kind: AnnotationKind,
    ) -> list[CodeUnit]:
        """Return all nodes that have an annotation of the given kind."""
        result: list[CodeUnit] = []
        for node_id, anns in self._graph.annotations.items():
            for ann in anns:
                if ann.kind == kind:
                    node = self._node(node_id)
                    if node is not None:
                        result.append(node)
                    break
        return result

    def all_entrypoints(self) -> list[tuple[str, EntrypointTag]]:
        """Return all entrypoint-tagged nodes."""
        return list(self._graph.entrypoints.items())

    def entrypoint_paths_to(
        self,
        node_id: str,
        max_depth: int = 20,
        edge_kinds: frozenset[EdgeKind] | None = None,
    ) -> list[list[str]]:
        """Find entrypoint paths of at most ``max_depth`` edges (positive)."""
        _validate_max_depth(max_depth)
        all_paths: list[list[str]] = []
        for ep_id in self._graph.entrypoints:
            paths = self.paths_between(
                ep_id,
                node_id,
                max_depth,
                edge_kinds=edge_kinds,
            )
            all_paths.extend(paths)
        return all_paths

    def nodes_by_complexity(
        self,
        min_complexity: int,
    ) -> list[CodeUnit]:
        """Return nodes with cyclomatic complexity >= threshold."""
        return [
            node
            for node in self._graph.nodes.values()
            if node.cyclomatic_complexity is not None
            and node.cyclomatic_complexity >= min_complexity
        ]

    def add_annotation(
        self,
        node_id: str,
        annotation: Annotation,
    ) -> bool:
        """Add an annotation to an existing node.

        Returns False if the node does not exist.
        """
        if self._node(node_id) is None:
            return False
        self._graph.add_annotation(node_id, annotation)
        return True

    def annotations_for(self, node_id: str) -> list[Annotation]:
        """Return all annotations for a node, or empty list."""
        return list(self._graph.annotations.get(node_id, []))

    def clear_annotations(
        self,
        node_id: str,
        kind: AnnotationKind | None = None,
    ) -> bool:
        """Clear annotations for a node.

        Returns False if the node does not exist.
        """
        if self._node(node_id) is None:
            return False
        self._graph.clear_annotations(node_id, kind)
        return True

    def find_node(self, name: str) -> CodeUnit | None:
        """Find a node by exact ID, then prefer declarations over proxies.

        Within declarations and then proxy fallback, try exact names, colon
        suffixes, and dot suffixes. Ties retain insertion order. Exact IDs
        always win, including explicit module and proxy IDs.
        """
        node_id = resolve_node_id(
            self._graph.nodes,
            name,
            name_of=lambda node: node.name,
            is_proxy=lambda node: node.kind == NodeKind.PROXY or node.origin == NodeOrigin.PROXY,
        )
        return self._node(node_id) if node_id is not None else None

    def find_node_id(self, name: str) -> str | None:
        """Find a node ID using the lookup precedence of :meth:`find_node`."""
        node = self.find_node(name)
        return node.id if node is not None else None

    def add_subgraph(self, name: str, node_ids: set[str]) -> None:
        """Register a named subgraph (set of node IDs)."""
        self._graph.subgraphs[name] = node_ids

    def subgraph(self, name: str) -> set[str]:
        """Return a copy of the node IDs in a named subgraph, or empty set."""
        return set(self._graph.subgraphs.get(name, ()))

    def all_subgraphs(self) -> dict[str, set[str]]:
        """Return all named subgraphs with copied membership sets."""
        return {name: set(ids) for name, ids in self._graph.subgraphs.items()}

    def subgraph_edges(
        self,
        name: str,
        edge_kinds: frozenset[EdgeKind] | None = None,
    ) -> list[CodeEdge]:
        """Return induced edges whose endpoints are both in the named subgraph."""
        node_ids = self.subgraph(name)
        if not node_ids:
            return []
        return [
            edge
            for edge in self._graph.edges
            if edge.source_id in node_ids
            and edge.target_id in node_ids
            and (edge_kinds is None or edge.kind in edge_kinds)
        ]

    def connect_subgraphs(
        self,
        source: str,
        target: str,
        max_depth: int = 20,
        edge_kinds: frozenset[EdgeKind] | None = None,
    ) -> list[list[str]]:
        """Connect named subgraphs with paths of at most ``max_depth`` edges.

        ``max_depth`` must be positive. Shared nodes also yield trivial paths.
        """
        _validate_max_depth(max_depth)
        source_ids = self.subgraph(source)
        target_ids = self.subgraph(target)
        if not source_ids or not target_ids:
            return []

        paths: list[list[str]] = []
        seen: set[tuple[str, ...]] = set()
        for src_id in sorted(source_ids):
            if src_id in target_ids:
                path = (src_id,)
                if path not in seen:
                    seen.add(path)
                    paths.append([src_id])
            for dst_id in sorted(target_ids):
                if src_id == dst_id:
                    continue
                for path in self.paths_between(
                    src_id,
                    dst_id,
                    max_depth,
                    edge_kinds=edge_kinds,
                ):
                    key = tuple(path)
                    if key not in seen:
                        seen.add(key)
                        paths.append(path)
        return paths

    def ancestors_of(
        self,
        node_id: str,
        edge_kinds: frozenset[EdgeKind] | None = None,
    ) -> set[str]:
        """Return node IDs that can reach the given node over CALLS by default."""
        digraph, id_to_idx, idx_to_id = self._projection_for(edge_kinds)
        idx = id_to_idx.get(node_id)
        if idx is None:
            return set()
        ancestor_indices = rx.ancestors(digraph, idx)
        return {idx_to_id[i] for i in ancestor_indices}
