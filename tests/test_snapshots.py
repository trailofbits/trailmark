import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from trailmark.models import (
    AnnotationKind,
    BinaryArtifact,
    BinaryDiagnostic,
    BinaryRange,
    BinarySourceMapping,
    CodeEdge,
    CodeGraph,
    EdgeKind,
    EntrypointKind,
    EntrypointTag,
    ExceptionEvent,
    ExceptionEventKind,
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


def test_snapshot_restores_complete_queries_without_source(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    snapshot = engine.to_snapshot(source_revision="source-revision", tool_revision="tool-revision")
    (tmp_path / "app.py").unlink()
    restored = QueryEngine.from_snapshot(snapshot)
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


def test_snapshot_retains_diagnostics_and_binary_evidence(tmp_path: Path) -> None:
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


def test_lightweight_json_is_not_a_snapshot(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with pytest.raises(ValueError, match="snapshot fields"):
        QueryEngine.from_snapshot(engine.to_json())
    snapshot = json.loads(engine.to_snapshot())
    restored = QueryEngine.from_snapshot(snapshot)
    snapshot["graph"]["nodes"].clear()
    assert restored.callers_of("helper")
