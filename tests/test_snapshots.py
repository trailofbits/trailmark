import hashlib
import json
import subprocess
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from trailmark.models import (
    AnnotationKind,
    BinaryArtifact,
    BinaryDiagnostic,
    BinaryLimits,
    BinaryRange,
    BinarySourceMapping,
    CodeEdge,
    CodeGraph,
    CodeUnit,
    EdgeKind,
    EntrypointKind,
    EntrypointTag,
    ExceptionEvent,
    ExceptionEventKind,
    NodeKind,
    NodeOrigin,
    SourceLocation,
)
from trailmark.models.entrypoints import EntrypointEvidence
from trailmark.query.api import QueryEngine
from trailmark.storage.graph_store import GraphStore


def _engine(tmp_path: Path) -> QueryEngine:
    (tmp_path / "app.py").write_text(
        "def helper(value: str) -> str:\n    raise ValueError(value)\n"
        "def action(value: str):\n    return helper(value)\n"
    )
    engine = QueryEngine.from_directory(str(tmp_path))
    graph = engine._store._graph
    engine.annotate(
        "app:action", AnnotationKind.AUDIT_NOTE, "preserve this evidence", source="manual"
    )
    graph.entrypoints["app:action"] = EntrypointTag(EntrypointKind.API)
    graph.subgraphs["reviewed"] = {"app:helper", "app:action"}
    graph.edges.append(CodeEdge("app:action", "external:Base", EdgeKind.INHERITS))
    graph._parse_state.resolved_type_files.add(str(tmp_path / "app.py"))
    engine.add_entrypoint_evidence(
        EntrypointEvidence(
            "app:action",
            "reference",
            "literal_registration",
            SourceLocation(str(tmp_path / "app.py"), 4, 4),
            source_id="app",
            symbol="action",
            unresolved=("invocation_context",),
        )
    )
    engine._store.rebuild_index()
    return engine


@pytest.mark.mutation_fast
def test_snapshot_restores_complete_queries_without_source(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    snapshot = engine.to_snapshot(source_revision="source-revision", tool_revision="tool-revision")
    (tmp_path / "app.py").unlink()
    restored = QueryEngine.from_snapshot(snapshot)
    assert GraphStore.from_snapshot(json.loads(snapshot)).to_snapshot() == json.loads(snapshot)
    assert restored.to_snapshot() == snapshot
    assert restored.summary() == engine.summary()
    assert restored.callers_of("helper") == engine.callers_of("helper")
    assert restored.annotations_of("action") == engine.annotations_of("action")
    assert restored.attack_surface() == engine.attack_surface()
    assert restored.entrypoint_evidence() == engine.entrypoint_evidence()
    assert restored.subgraph("reviewed") == engine.subgraph("reviewed")
    assert restored.unresolved_relationships() == engine.unresolved_relationships()
    assert restored._store._graph == engine._store._graph
    assert (
        restored._store._graph._parse_state.resolved_type_files
        == engine._store._graph._parse_state.resolved_type_files
    )
    assert all(
        edge.location is not None
        for edge in restored._store._graph.edges
        if edge.kind == EdgeKind.CALLS
    )
    assert restored.exception_events("helper") == engine.exception_events("helper")


def _graph_with_binary_evidence(tmp_path: Path) -> CodeGraph:
    engine = _engine(tmp_path)
    graph = engine._store._graph
    from trailmark.models import ParseDiagnostic

    graph.parse_diagnostics.append(
        ParseDiagnostic(
            "c", "c", SourceLocation("partial.c", 2, 5), "syntax_error", "partial coverage"
        )
    )
    graph.binary_artifacts["image"] = BinaryArtifact(
        "image",
        "missing-image",
        "a" * 64,
        "elf",
        "aarch64",
        64,
        "little",
        4096,
        0,
        1024,
        tool_versions=(("objdump", "fixture"),),
        scanned_ranges=((4096, 4100),),
        diagnostics=(BinaryDiagnostic("partial", "coverage unknown"),),
    )
    graph.binary_ranges["range"] = BinaryRange("range", "image", "app:helper", 4096, 4100, 0, 0)
    graph.binary_source_mappings["mapping"] = BinarySourceMapping(
        "mapping",
        "range",
        "app:helper",
        4096,
        4100,
        "debug_line",
        line=1,
    )
    return graph


@pytest.mark.mutation_fast
def test_snapshot_retains_diagnostics_and_binary_evidence(tmp_path: Path) -> None:
    graph = _graph_with_binary_evidence(tmp_path)
    snapshot = graph.to_snapshot()
    assert CodeGraph.from_snapshot(snapshot) == graph
    assert GraphStore.from_snapshot(snapshot).to_snapshot() == snapshot
    assert isinstance(
        CodeGraph.from_snapshot(snapshot).nodes["app:helper"].exception_events[0], ExceptionEvent
    )
    assert (
        CodeGraph.from_snapshot(snapshot).nodes["app:helper"].exception_events[0].kind
        == ExceptionEventKind.RAISE
    )


@pytest.mark.mutation_fast
def test_source_fingerprints_are_captured_before_export_and_can_be_verified(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    snapshot = engine.to_snapshot()
    QueryEngine.from_snapshot(snapshot, verify_sources=True)
    (tmp_path / "app.py").write_text("different source\n")
    assert (
        json.loads(engine.to_snapshot())["provenance"]["source_sha256"]
        == json.loads(snapshot)["provenance"]["source_sha256"]
    )
    with pytest.raises(ValueError, match="Source digest mismatch"):
        QueryEngine.from_snapshot(snapshot, verify_sources=True)
    QueryEngine.from_snapshot(snapshot)


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    "change",
    [
        lambda s: s.update(format="topology"),
        lambda s: s.update(snapshot_version=2),
        lambda s: s.update(snapshot_version=True),
        lambda s: s.update(trailmark_version="0.0.0"),
        lambda s: s["graph"]["nodes"]["app:helper"].update(kind="unknown"),
        lambda s: s["graph"]["nodes"]["app:helper"].update(id="different"),
        lambda s: s["graph"]["nodes"]["app:helper"]["location"].update(start_line=-1),
        lambda s: s["graph"]["nodes"]["app:helper"]["location"].update(start_line=True),
        lambda s: s["graph"]["annotations"].update(absent=[]),
        lambda s: s["graph"]["subgraphs"].update(invalid=["absent"]),
        lambda s: s.update(unresolved_relationships=[]),
        lambda s: s["provenance"].update(source_sha256="a" * 64),
        lambda s: s["provenance"].update(unfingerprinted_sources=["not-source"]),
    ],
)
def test_malformed_or_incompatible_snapshots_fail_explicitly(
    tmp_path: Path,
    change: Callable[[dict[str, Any]], None],
) -> None:
    snapshot = json.loads(_engine(tmp_path).to_snapshot())
    change(snapshot)
    with pytest.raises(ValueError):
        QueryEngine.from_snapshot(snapshot)


@pytest.mark.mutation_fast
def test_lightweight_json_is_not_a_snapshot(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with pytest.raises(ValueError, match="snapshot fields"):
        QueryEngine.from_snapshot(engine.to_json())
    snapshot = json.loads(engine.to_snapshot())
    restored = QueryEngine.from_snapshot(snapshot)
    snapshot["graph"]["nodes"].clear()
    assert restored.callers_of("helper")


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    "location",
    [
        SourceLocation("app.py", 0, 1),
        SourceLocation("app.py", 2, 1),
        SourceLocation("app.py", 1, 1, -1, 0),
        SourceLocation("app.py", 1, 1, 0, -1),
        SourceLocation("app.py", 1, 1, 2, 1),
    ],
)
def test_snapshot_rejects_invalid_location_boundaries(
    tmp_path: Path, location: SourceLocation
) -> None:
    graph = _engine(tmp_path)._store._graph
    graph.nodes["app:helper"] = replace(graph.nodes["app:helper"], location=location)
    with pytest.raises(ValueError, match="source location"):
        CodeGraph.from_snapshot(graph.to_snapshot())


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    "location",
    [
        SourceLocation("app.py", 1, 1, 0, 0),
        SourceLocation("app.py", 1, 2, 5, 0),
        SourceLocation("app.py", 1, 1, None, 0),
        SourceLocation("app.py", 1, 1, 0, None),
    ],
)
def test_snapshot_preserves_valid_location_boundaries(
    tmp_path: Path, location: SourceLocation
) -> None:
    graph = _engine(tmp_path)._store._graph
    graph.nodes["app:helper"] = replace(graph.nodes["app:helper"], location=location)
    assert CodeGraph.from_snapshot(graph.to_snapshot()).nodes["app:helper"].location == location


@pytest.mark.mutation_fast
@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan"), complex(1, 2)])
def test_snapshot_rejects_unsupported_metadata_values(tmp_path: Path, value: object) -> None:
    graph = _engine(tmp_path)._store._graph
    graph.source_policy["invalid"] = value
    with pytest.raises(ValueError, match="Unsupported snapshot value"):
        graph.to_snapshot()


@pytest.mark.mutation_fast
def test_snapshot_preserves_float_attributes_and_converts_integer_timeouts(tmp_path: Path) -> None:
    graph = _graph_with_binary_evidence(tmp_path)
    graph.nodes["app:helper"] = replace(
        graph.nodes["app:helper"], attributes=(("score", 0.25), ("enabled", False), ("count", 0))
    )
    graph.binary_artifacts["image"] = replace(
        graph.binary_artifacts["image"], limits=BinaryLimits(tool_timeout=2, import_timeout=3)
    )
    restored = CodeGraph.from_snapshot(graph.to_snapshot())
    assert restored.nodes["app:helper"].attributes == (
        ("score", 0.25),
        ("enabled", False),
        ("count", 0),
    )
    limits = restored.binary_artifacts["image"].limits
    assert type(limits.tool_timeout) is float
    assert type(limits.import_timeout) is float
    assert (limits.tool_timeout, limits.import_timeout) == (2.0, 3.0)


@pytest.mark.mutation_fast
@pytest.mark.parametrize("value", [[], {1: "value"}])
def test_snapshot_reports_invalid_typed_dictionary(tmp_path: Path, value: object) -> None:
    snapshot: dict[str, Any] = _engine(tmp_path)._store._graph.to_snapshot()
    snapshot["graph"]["source_file_counts"] = value
    with pytest.raises(ValueError, match="source_file_counts: expected an object with string keys"):
        CodeGraph.from_snapshot(snapshot)


@pytest.mark.mutation_fast
def test_snapshot_reports_invalid_literal_at_its_field(tmp_path: Path) -> None:
    snapshot: dict[str, Any] = _engine(tmp_path)._store._graph.to_snapshot()
    evidence = next(iter(snapshot["graph"]["entrypoint_evidence"].values()))
    evidence["basis"] = "not-a-basis"
    with pytest.raises(ValueError, match=r"entrypoint_evidence\..*\.basis: invalid literal"):
        CodeGraph.from_snapshot(snapshot)


@pytest.mark.mutation_fast
@pytest.mark.parametrize("field", ["source_sha256", "tool_sha256"])
@pytest.mark.parametrize("digest", ["a" * 63, "g" * 64])
def test_snapshot_rejects_invalid_provenance_digests(
    tmp_path: Path, field: str, digest: str
) -> None:
    snapshot: dict[str, Any] = _engine(tmp_path)._store._graph.to_snapshot()
    snapshot["provenance"][field] = digest
    with pytest.raises(ValueError, match=f"Invalid {field}"):
        CodeGraph.from_snapshot(snapshot)


@pytest.mark.mutation_fast
@pytest.mark.parametrize("digest", ["a" * 63, "g" * 64])
def test_snapshot_rejects_invalid_source_digest_even_with_matching_inventory(
    tmp_path: Path, digest: str
) -> None:
    snapshot: dict[str, Any] = _engine(tmp_path)._store._graph.to_snapshot()
    snapshot["graph"]["source_digests"] = {"app.py": digest}
    snapshot["provenance"]["source_sha256"] = hashlib.sha256(
        json.dumps({"app.py": digest}, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with pytest.raises(ValueError, match="Invalid source file digest"):
        CodeGraph.from_snapshot(snapshot)


@pytest.mark.mutation_fast
def test_snapshot_source_digest_does_not_depend_on_insertion_order() -> None:
    first = CodeGraph(source_digests={"b.py": "b" * 64, "a.py": "a" * 64})
    second = CodeGraph(source_digests={"a.py": "a" * 64, "b.py": "b" * 64})
    expected = hashlib.sha256(
        b'{"a.py":"' + b"a" * 64 + b'","b.py":"' + b"b" * 64 + b'"}'
    ).hexdigest()
    first_snapshot: dict[str, Any] = first.to_snapshot()
    second_snapshot: dict[str, Any] = second.to_snapshot()
    assert first_snapshot["provenance"]["source_sha256"] == expected
    assert second_snapshot["provenance"]["source_sha256"] == expected


@pytest.mark.mutation_fast
@pytest.mark.parametrize("count", [0, 1])
def test_snapshot_accepts_nonnegative_file_counts(count: int) -> None:
    graph = CodeGraph(source_file_counts={"python": count})
    assert CodeGraph.from_snapshot(graph.to_snapshot()).source_file_counts == {"python": count}


@pytest.mark.mutation_fast
def test_snapshot_rejects_negative_file_counts() -> None:
    graph = CodeGraph(source_file_counts={"python": -1})
    with pytest.raises(ValueError, match="Invalid source file count"):
        CodeGraph.from_snapshot(graph.to_snapshot())


@pytest.mark.mutation_fast
def test_snapshot_rejects_integer_in_string_field(tmp_path: Path) -> None:
    snapshot: dict[str, Any] = _engine(tmp_path)._store._graph.to_snapshot()
    snapshot["graph"]["nodes"]["app:helper"]["name"] = 1
    with pytest.raises(ValueError, match=r"app:helper\.name: expected"):
        CodeGraph.from_snapshot(snapshot)


def test_snapshot_rejects_boolean_timeout(tmp_path: Path) -> None:
    snapshot: dict[str, Any] = _graph_with_binary_evidence(tmp_path).to_snapshot()
    snapshot["graph"]["binary_artifacts"]["image"]["limits"]["tool_timeout"] = True
    with pytest.raises(ValueError, match=r"tool_timeout: expected"):
        CodeGraph.from_snapshot(snapshot)


@pytest.mark.mutation_fast
@pytest.mark.parametrize("field", ["node_id", "source_id"])
def test_snapshot_rejects_missing_evidence_endpoints(tmp_path: Path, field: str) -> None:
    graph = _engine(tmp_path)._store._graph
    evidence = next(iter(graph.entrypoint_evidence.values()))
    graph.entrypoint_evidence.clear()
    graph.add_entrypoint_evidence(replace(evidence, **{field: "absent"}))
    with pytest.raises(ValueError, match="Invalid entrypoint evidence"):
        CodeGraph.from_snapshot(graph.to_snapshot())


@pytest.mark.mutation_fast
@pytest.mark.parametrize("start,end", [(0, 1), (0, 2), (1, 2)])
def test_snapshot_preserves_binary_range_boundaries(tmp_path: Path, start: int, end: int) -> None:
    graph = _graph_with_binary_evidence(tmp_path)
    graph.binary_artifacts["image"] = replace(
        graph.binary_artifacts["image"], image_base=0, scanned_ranges=((0, end),)
    )
    graph.binary_ranges["range"] = replace(
        graph.binary_ranges["range"], start=start, end=end, rva=start, file_offset=start
    )
    graph.binary_source_mappings["mapping"] = replace(
        graph.binary_source_mappings["mapping"], start=start, end=end
    )
    restored = CodeGraph.from_snapshot(graph.to_snapshot())
    assert (restored.binary_ranges["range"].start, restored.binary_ranges["range"].end) == (
        start,
        end,
    )


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    "changes",
    [{"start": -1}, {"start": 4100}, {"artifact_id": "absent"}, {"node_id": "absent"}],
)
def test_snapshot_rejects_invalid_binary_ranges(tmp_path: Path, changes: dict[str, Any]) -> None:
    graph = _graph_with_binary_evidence(tmp_path)
    graph.binary_ranges["range"] = replace(graph.binary_ranges["range"], **changes)
    with pytest.raises(ValueError, match="Invalid binary range or endpoint"):
        CodeGraph.from_snapshot(graph.to_snapshot())


@pytest.mark.mutation_fast
@pytest.mark.parametrize("field", ["range_id", "source_node_id"])
def test_snapshot_rejects_missing_binary_mapping_endpoints(tmp_path: Path, field: str) -> None:
    graph = _graph_with_binary_evidence(tmp_path)
    graph.binary_source_mappings["mapping"] = replace(
        graph.binary_source_mappings["mapping"], **{field: "absent"}
    )
    with pytest.raises(ValueError, match="Invalid binary source mapping endpoint"):
        CodeGraph.from_snapshot(graph.to_snapshot())


def test_snapshot_lists_only_unfingerprinted_source_nodes(tmp_path: Path) -> None:
    graph = _engine(tmp_path)._store._graph
    source = graph.nodes["app:helper"]
    graph.nodes["unfingerprinted"] = replace(
        source, id="unfingerprinted", location=SourceLocation("missing.py", 1, 1)
    )
    graph.nodes["synthetic"] = replace(
        source,
        id="synthetic",
        origin=NodeOrigin.SYNTHETIC,
        location=SourceLocation("generated", 1, 1),
    )
    snapshot: dict[str, Any] = graph.to_snapshot()
    assert snapshot["provenance"]["unfingerprinted_sources"] == ["missing.py"]
    CodeGraph.from_snapshot(snapshot)
    snapshot["provenance"]["unfingerprinted_sources"] = []
    with pytest.raises(ValueError, match="Unfingerprinted source inventory"):
        CodeGraph.from_snapshot(snapshot)


@pytest.mark.mutation_fast
@pytest.mark.parametrize("returncode,expected", [(0, "revision"), (1, None), (128, None)])
def test_snapshot_records_only_successful_git_revisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, returncode: int, expected: str | None
) -> None:
    monkeypatch.setattr("trailmark.models.snapshot.shutil.which", lambda _: "/bin/git")
    monkeypatch.setattr(
        "trailmark.models.snapshot.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, returncode, "revision\n", ""),
    )
    snapshot: dict[str, Any] = CodeGraph(root_path=str(tmp_path)).to_snapshot()
    assert snapshot["provenance"]["source_revision"] == expected
    assert snapshot["provenance"]["tool_revision"] == expected


def test_snapshot_keeps_explicit_revision_overrides_when_reexported(tmp_path: Path) -> None:
    graph = _engine(tmp_path)._store._graph
    snapshot = graph.to_snapshot(source_revision="source", tool_revision="tool")
    restored = CodeGraph.from_snapshot(snapshot)
    updated: dict[str, Any] = restored.to_snapshot(source_revision="", tool_revision="new-tool")
    assert updated["provenance"]["source_revision"] == ""
    assert updated["provenance"]["tool_revision"] == "new-tool"
    assert restored.to_snapshot() == snapshot


@pytest.mark.mutation_fast
def test_snapshot_v1_preserves_known_entrypoint_evidence_identity() -> None:
    """An opaque ID persisted by v1 must still identify the same observation."""
    evidence = EntrypointEvidence(
        "app:action",
        "reference",
        "literal_registration",
        SourceLocation("app.py", 4, 4),
        source_id="app",
        symbol="action",
        unresolved=("invocation_context",),
    )
    known_id = "entrypoint:e51abe598116465d9510d42c"
    graph = CodeGraph(
        nodes={
            "app": CodeUnit("app", "app", NodeKind.MODULE, SourceLocation("app.py", 1, 4)),
            "app:action": CodeUnit(
                "app:action", "action", NodeKind.FUNCTION, SourceLocation("app.py", 4, 4)
            ),
        }
    )
    graph.add_entrypoint_evidence(evidence)
    snapshot: dict[str, Any] = graph.to_snapshot()
    observation = next(iter(snapshot["graph"]["entrypoint_evidence"].values()))
    snapshot["graph"]["entrypoint_evidence"] = {known_id: observation}
    restored = CodeGraph.from_snapshot(snapshot)
    assert restored.entrypoint_evidence[known_id] == evidence
