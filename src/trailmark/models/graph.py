"""Top-level graph container that holds parsed code data."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

from trailmark.models.annotations import Annotation, AnnotationKind, EntrypointTag
from trailmark.models.binary import BinaryArtifact, BinaryRange, BinarySourceMapping
from trailmark.models.diagnostics import ParseDiagnostic
from trailmark.models.edges import CodeEdge, EdgeKind
from trailmark.models.entrypoints import EntrypointEvidence
from trailmark.models.nodes import CodeUnit, NodeKind


@dataclass
class CodeGraph:
    """A complete code graph for a parsed project or file."""

    nodes: dict[str, CodeUnit] = field(default_factory=dict)
    edges: list[CodeEdge] = field(default_factory=list)
    annotations: dict[str, list[Annotation]] = field(
        default_factory=dict,
    )
    entrypoints: dict[str, EntrypointTag] = field(
        default_factory=dict,
    )
    subgraphs: dict[str, set[str]] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    language: str = ""
    root_path: str = ""
    source_file_counts: dict[str, int] = field(default_factory=dict)
    parse_diagnostics: list[ParseDiagnostic] = field(default_factory=list)
    source_policy: dict[str, object] = field(default_factory=dict)
    entrypoint_evidence: dict[str, EntrypointEvidence] = field(default_factory=dict)
    binary_artifacts: dict[str, BinaryArtifact] = field(default_factory=dict)
    binary_ranges: dict[str, BinaryRange] = field(default_factory=dict)
    binary_source_mappings: dict[str, BinarySourceMapping] = field(default_factory=dict)

    def add_entrypoint_evidence(self, evidence: EntrypointEvidence) -> None:
        """Retain distinct source occurrences and make repeated discovery idempotent."""
        self.entrypoint_evidence[evidence.id] = evidence

    def unresolved_calls(self) -> list[CodeEdge]:
        """Return calls with missing targets or explicit unresolved proxies."""
        result: list[CodeEdge] = []
        for edge in self.edges:
            if edge.kind != EdgeKind.CALLS:
                continue
            target = self.nodes.get(edge.target_id)
            if target is None or (
                target.kind == NodeKind.PROXY
                and dict(target.attributes).get("proxy_kind") == "unresolved"
            ):
                result.append(edge)
        return result

    def add_annotation(
        self,
        node_id: str,
        annotation: Annotation,
    ) -> None:
        """Append an annotation to the given node."""
        self.annotations.setdefault(node_id, []).append(annotation)

    def clear_annotations(
        self,
        node_id: str,
        kind: AnnotationKind | None = None,
    ) -> None:
        """Remove annotations for a node, optionally filtered by kind.

        If kind is None, removes all annotations for the node.
        Cleans up empty annotation lists from the dict.
        """
        if node_id not in self.annotations:
            return
        if kind is None:
            del self.annotations[node_id]
        else:
            self.annotations[node_id] = [a for a in self.annotations[node_id] if a.kind != kind]
            if not self.annotations[node_id]:
                del self.annotations[node_id]

    def merge(self, other: CodeGraph) -> None:
        """Merge another graph into this one."""
        for key, artifact in other.binary_artifacts.items():
            previous = self.binary_artifacts.get(key)
            identity_fields = (
                "sha256",
                "slice_offset",
                "slice_size",
                "slice_index",
                "format",
                "architecture",
                "bits",
                "byte_order",
                "image_base",
                "cpu_subtype",
                "abi",
                "mode",
                "build_id",
                "uuid",
            )
            if previous is not None and any(
                getattr(previous, field) != getattr(artifact, field) for field in identity_fields
            ):
                raise ValueError(f"Conflicting binary artifact identity: {key}")
        for name in ("binary_ranges", "binary_source_mappings"):
            current, incoming = getattr(self, name), getattr(other, name)
            for key, evidence in incoming.items():
                if key in current and current[key] != evidence:
                    raise ValueError(f"Conflicting {name} record: {key}")
        if other.source_policy:
            self.source_policy = (
                {
                    "schema_version": 1,
                    "components": [deepcopy(self.source_policy), deepcopy(other.source_policy)],
                }
                if self.source_policy
                else deepcopy(other.source_policy)
            )
        self.nodes.update(other.nodes)
        self.edges.extend(other.edges)
        self.parse_diagnostics.extend(other.parse_diagnostics)
        for node_id, anns in other.annotations.items():
            self.annotations.setdefault(node_id, []).extend(anns)
        self.entrypoints.update(other.entrypoints)
        self.entrypoint_evidence.update(other.entrypoint_evidence)
        self.binary_artifacts.update(other.binary_artifacts)
        self.binary_ranges.update(other.binary_ranges)
        self.binary_source_mappings.update(other.binary_source_mappings)
        for name, ids in other.subgraphs.items():
            self.subgraphs.setdefault(name, set()).update(ids)
        for dep in other.dependencies:
            if dep not in self.dependencies:
                self.dependencies.append(dep)
        for language, count in other.source_file_counts.items():
            self.source_file_counts[language] = self.source_file_counts.get(language, 0) + count


def source_manifests(graph: CodeGraph) -> list[dict[str, object]]:
    """Preserve each original selection policy when already-parsed graphs merge."""
    result = []
    pending = [graph.source_policy]
    while pending:
        manifest = pending.pop()
        components = manifest.get("components")
        if isinstance(components, list):
            pending.extend(reversed([item for item in components if isinstance(item, dict)]))
        elif manifest:
            result.append(manifest)
    return result
