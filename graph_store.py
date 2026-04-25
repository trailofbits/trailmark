"""Rustworkx-backed graph storage with query methods."""

from __future__ import annotations

import rustworkx as rx

from trailmark.models.annotations import (
    Annotation,
    AnnotationKind,
    EntrypointTag,
)
from trailmark.models.edges import CodeEdge, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit


class GraphStore:
    """Indexed graph store backed by rustworkx for fast traversals."""

    def __init__(self, graph: CodeGraph) -> None:
        self._graph = graph
        self._digraph: rx.PyDiGraph[str, CodeEdge] = rx.PyDiGraph()
        self._call_digraph: rx.PyDiGraph[str, CodeEdge] = rx.PyDiGraph()
        self._id_to_idx: dict[str, int] = {}
        self._idx_to_id: dict[int, str] = {}
        self._call_id_to_idx: dict[str, int] = {}
        self._call_idx_to_id: dict[int, str] = {}
        self._build_index()

    def _build_index(self) -> None:
        """Populate the rustworkx digraph from the CodeGraph."""
        for node_id in self._graph.nodes:
            idx = self._digraph.add_node(node_id)
            call_idx = self._call_digraph.add_node(node_id)
            self._id_to_idx[node_id] = idx
            self._idx_to_id[idx] = node_id
            self._call_id_to_idx[node_id] = call_idx
            self._call_idx_to_id[call_idx] = node_id

        for edge in self._graph.edges:
            src_idx = self._id_to_idx.get(edge.source_id)
            tgt_idx = self._id_to_idx.get(edge.target_id)
            if src_idx is not None and tgt_idx is not None:
                self._digraph.add_edge(src_idx, tgt_idx, edge)
            if edge.kind == EdgeKind.CALLS:
                call_src_idx = self._call_id_to_idx.get(edge.source_id)
                call_tgt_idx = self._call_id_to_idx.get(edge.target_id)
                if call_src_idx is not None and call_tgt_idx is not None:
                    self._call_digraph.add_edge(call_src_idx, call_tgt_idx, edge)

    def _idx(self, node_id: str) -> int | None:
        return self._id_to_idx.get(node_id)

    def _call_idx(self, node_id: str) -> int | None:
        return self._call_id_to_idx.get(node_id)

    def _node(self, node_id: str) -> CodeUnit | None:
        return self._graph.nodes.get(node_id)

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
    ) -> list[list[str]]:
        """Find all simple paths between two nodes (up to max_depth)."""
        src_idx = self._call_idx(src_id)
        dst_idx = self._call_idx(dst_id)
        if src_idx is None or dst_idx is None:
            return []
        raw_paths: list[list[int]] = rx.digraph_all_simple_paths(
            self._call_digraph,
            src_idx,
            dst_idx,
            cutoff=max_depth,
        )
        return [[self._call_idx_to_id[i] for i in path] for path in raw_paths]

    def reachable_from(self, node_id: str) -> set[str]:
        """Return all node IDs reachable from the given node."""
        idx = self._call_idx(node_id)
        if idx is None:
            return set()
        descendants = rx.descendants(self._call_digraph, idx)
        return {self._call_idx_to_id[i] for i in descendants}

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
    ) -> list[list[str]]:
        """Find all paths from any entrypoint to the given node."""
        all_paths: list[list[str]] = []
        for ep_id in self._graph.entrypoints:
            paths = self.paths_between(ep_id, node_id, max_depth)
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
        """Find a node by exact ID, name match, or qualified suffix.

        Lookup precedence:
        1. Exact node ID match
        2. Exact name field match
        3. ID ending with ``:name`` (module:function)
        4. ID ending with ``.name`` (class.method)
        """
        if name in self._graph.nodes:
            return self._graph.nodes[name]
        for node_id, node in self._graph.nodes.items():
            if node.name == name or node_id.endswith(f":{name}"):
                return node
            if node_id.endswith(f".{name}"):
                return node
        return None

    def find_node_id(self, name: str) -> str | None:
        """Find a node ID by exact ID or name substring."""
        node = self.find_node(name)
        return node.id if node is not None else None

    def add_subgraph(self, name: str, node_ids: set[str]) -> None:
        """Register a named subgraph (set of node IDs)."""
        self._graph.subgraphs[name] = node_ids

    def subgraph(self, name: str) -> set[str]:
        """Return the node IDs in a named subgraph, or empty set."""
        return self._graph.subgraphs.get(name, set())

    def all_subgraphs(self) -> dict[str, set[str]]:
        """Return all named subgraphs."""
        return dict(self._graph.subgraphs)

    def ancestors_of(self, node_id: str) -> set[str]:
        """Return all node IDs that can reach the given node."""
        idx = self._call_idx(node_id)
        if idx is None:
            return set()
        ancestor_indices = rx.ancestors(self._call_digraph, idx)
        return {self._call_idx_to_id[i] for i in ancestor_indices}
