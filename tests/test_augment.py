"""Tests for SARIF and weAudit augmentation."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from trailmark.analysis.augment import (
    _find_overlapping_nodes,
    _normalize_path,
    augment_from_sarif,
    augment_from_weaudit,
    clear_augmented,
)
from trailmark.models import (
    AnnotationKind,
    CodeGraph,
    CodeUnit,
    NodeKind,
    NodeOrigin,
    SourceLocation,
)
from trailmark.query.api import QueryEngine
from trailmark.storage.graph_store import GraphStore

_ROOT = "/project"


def _loc(path: str, start: int, end: int) -> SourceLocation:
    return SourceLocation(
        file_path=os.path.join(_ROOT, path),
        start_line=start,
        end_line=end,
    )


def _node(node_id: str, path: str, start: int, end: int) -> CodeUnit:
    return CodeUnit(
        id=node_id,
        name=node_id.split(":")[-1],
        kind=NodeKind.FUNCTION,
        location=_loc(path, start, end),
    )


def _build_graph() -> CodeGraph:
    """Build a graph with known file locations for matching."""
    return CodeGraph(
        nodes={
            "mod:func_a": _node("mod:func_a", "src/handler.py", 10, 30),
            "mod:func_b": _node("mod:func_b", "src/handler.py", 35, 50),
            "mod:func_c": _node("mod:func_c", "src/db.py", 5, 25),
            "mod:module": CodeUnit(
                id="mod:module",
                name="module",
                kind=NodeKind.MODULE,
                location=_loc("src/handler.py", 1, 100),
            ),
        },
        edges=[],
        root_path=_ROOT,
    )


def _write_json(tmp_path: Any, name: str, data: dict[str, Any]) -> str:
    path = os.path.join(str(tmp_path), name)
    with open(path, "w") as f:
        json.dump(data, f)
    return path


# ── Path normalization ───────────────────────────────────────────────


class TestNormalizePath:
    def test_relative_path(self) -> None:
        assert _normalize_path("src/handler.py", _ROOT) == "src/handler.py"

    def test_absolute_path(self) -> None:
        result = _normalize_path("/project/src/handler.py", _ROOT)
        assert result == "src/handler.py"

    def test_file_uri(self) -> None:
        result = _normalize_path(
            "file:///project/src/handler.py",
            _ROOT,
        )
        assert result == "src/handler.py"

    def test_unknown_scheme_returns_none(self) -> None:
        assert _normalize_path("https://example.com/file.py", _ROOT) is None

    def test_absolute_path_outside_root(self) -> None:
        result = _normalize_path("/other/file.py", _ROOT)
        # Returns a relative path with .. components
        assert result is not None
        assert ".." in result


# ── Overlapping node matching ────────────────────────────────────────


class TestFindOverlappingNodes:
    def test_exact_match(self) -> None:
        graph = _build_graph()
        nodes = _find_overlapping_nodes(graph, "src/handler.py", 10, 30)
        assert "mod:func_a" in nodes

    def test_partial_overlap(self) -> None:
        graph = _build_graph()
        nodes = _find_overlapping_nodes(graph, "src/handler.py", 25, 40)
        assert "mod:func_a" in nodes
        assert "mod:func_b" in nodes

    def test_no_overlap(self) -> None:
        graph = _build_graph()
        nodes = _find_overlapping_nodes(graph, "src/handler.py", 51, 60)
        # Only module (1-100) matches, not the functions
        ids = [n for n in nodes if "func" in n]
        assert ids == []

    def test_wrong_file(self) -> None:
        graph = _build_graph()
        nodes = _find_overlapping_nodes(graph, "src/other.py", 10, 30)
        assert nodes == []

    def test_tightest_match_first(self) -> None:
        graph = _build_graph()
        # Line 15 overlaps func_a (10-30) and module (1-100)
        nodes = _find_overlapping_nodes(graph, "src/handler.py", 15, 15)
        assert nodes[0] == "mod:func_a"  # Tighter span
        assert "mod:module" in nodes  # Module also matches

    def test_single_line_finding(self) -> None:
        graph = _build_graph()
        nodes = _find_overlapping_nodes(graph, "src/db.py", 10, 10)
        assert "mod:func_c" in nodes


# ── SARIF augmentation ───────────────────────────────────────────────


def _minimal_sarif(
    results: list[dict[str, Any]],
    tool_name: str = "semgrep",
) -> dict[str, Any]:
    return {
        "version": "2.1.0",
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/main/sarif-2.1/schema/sarif-schema-2.1.0.json",
        "runs": [
            {
                "tool": {"driver": {"name": tool_name}},
                "results": results,
            },
        ],
    }


def _sarif_result(
    rule_id: str,
    message: str,
    uri: str,
    start_line: int,
    level: str = "warning",
) -> dict[str, Any]:
    return {
        "ruleId": rule_id,
        "level": level,
        "message": {"text": message},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": uri},
                    "region": {
                        "startLine": start_line,
                        "endLine": start_line,
                    },
                },
            },
        ],
    }


class TestAugmentFromSarif:
    def test_matches_finding_to_node(self, tmp_path: Any) -> None:
        graph = _build_graph()
        store = GraphStore(graph)
        sarif = _minimal_sarif(
            [
                _sarif_result(
                    "exec-detected",
                    "Detected exec()",
                    "src/handler.py",
                    15,
                ),
            ]
        )
        path = _write_json(tmp_path, "results.sarif", sarif)

        result = augment_from_sarif(store, path)
        assert result["matched_findings"] == 1
        assert result["unmatched_findings"] == 0

    def test_annotation_format(self, tmp_path: Any) -> None:
        graph = _build_graph()
        store = GraphStore(graph)
        sarif = _minimal_sarif(
            [
                _sarif_result(
                    "exec-detected",
                    "Detected exec()",
                    "src/handler.py",
                    15,
                ),
            ]
        )
        path = _write_json(tmp_path, "results.sarif", sarif)
        augment_from_sarif(store, path)

        anns = graph.annotations.get("mod:func_a", [])
        finding_anns = [a for a in anns if a.kind == AnnotationKind.FINDING]
        assert len(finding_anns) >= 1
        desc = finding_anns[0].description
        assert "[WARNING]" in desc
        assert "exec-detected" in desc
        assert "semgrep" in desc

    def test_source_field(self, tmp_path: Any) -> None:
        graph = _build_graph()
        store = GraphStore(graph)
        sarif = _minimal_sarif(
            [_sarif_result("r1", "msg", "src/handler.py", 15)],
            tool_name="codeql",
        )
        path = _write_json(tmp_path, "results.sarif", sarif)
        augment_from_sarif(store, path)

        anns = graph.annotations.get("mod:func_a", [])
        assert any(a.source == "sarif:codeql" for a in anns)

    def test_subgraphs_by_level(self, tmp_path: Any) -> None:
        graph = _build_graph()
        store = GraphStore(graph)
        sarif = _minimal_sarif(
            [
                _sarif_result(
                    "r1",
                    "msg",
                    "src/handler.py",
                    15,
                    level="error",
                ),
                _sarif_result(
                    "r2",
                    "msg",
                    "src/db.py",
                    10,
                    level="note",
                ),
            ]
        )
        path = _write_json(tmp_path, "results.sarif", sarif)
        result = augment_from_sarif(store, path)

        assert "sarif:error" in result["subgraphs_created"]
        assert "sarif:note" in result["subgraphs_created"]
        assert "sarif:semgrep" in result["subgraphs_created"]

    def test_unmatched_finding(self, tmp_path: Any) -> None:
        graph = _build_graph()
        store = GraphStore(graph)
        sarif = _minimal_sarif(
            [
                _sarif_result("r1", "msg", "src/nonexistent.py", 1),
            ]
        )
        path = _write_json(tmp_path, "results.sarif", sarif)
        result = augment_from_sarif(store, path)

        assert result["matched_findings"] == 0
        assert result["unmatched_findings"] == 1

    def test_multiple_runs(self, tmp_path: Any) -> None:
        graph = _build_graph()
        store = GraphStore(graph)
        sarif = {
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {"driver": {"name": "tool_a"}},
                    "results": [
                        _sarif_result("r1", "m", "src/handler.py", 15),
                    ],
                },
                {
                    "tool": {"driver": {"name": "tool_b"}},
                    "results": [
                        _sarif_result("r2", "m", "src/db.py", 10),
                    ],
                },
            ],
        }
        path = _write_json(tmp_path, "results.sarif", sarif)
        result = augment_from_sarif(store, path)

        assert result["matched_findings"] == 2
        assert "sarif:tool_a" in result["subgraphs_created"]
        assert "sarif:tool_b" in result["subgraphs_created"]


# ── weAudit augmentation ────────────────────────────────────────────


def _minimal_weaudit(
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "clientRemote": "https://github.com/org/repo",
        "gitRemote": "https://github.com/org/repo",
        "gitSha": "abc123",
        "treeEntries": entries,
        "resolvedEntries": [],
        "auditedFiles": [],
    }


def _weaudit_entry(
    label: str,
    path: str,
    start_line: int,
    end_line: int,
    severity: str = "High",
    entry_type: int = 0,
) -> dict[str, Any]:
    return {
        "label": label,
        "entryType": entry_type,
        "author": "alice",
        "details": {
            "severity": severity,
            "difficulty": "Low",
            "type": "Data Validation",
            "description": f"Description for {label}",
            "exploit": "",
            "recommendation": "",
        },
        "locations": [
            {
                "path": path,
                "startLine": start_line,
                "endLine": end_line,
                "label": label,
                "description": "",
            },
        ],
    }


class TestAugmentFromWeaudit:
    def test_matches_finding_with_line_conversion(
        self,
        tmp_path: Any,
    ) -> None:
        """weAudit uses 0-indexed lines; func_a is at 1-indexed 10-30."""
        graph = _build_graph()
        store = GraphStore(graph)
        # 0-indexed line 14 = 1-indexed line 15, inside func_a (10-30)
        data = _minimal_weaudit(
            [
                _weaudit_entry("SQLi", "src/handler.py", 14, 16),
            ]
        )
        path = _write_json(tmp_path, "alice.weaudit", data)

        result = augment_from_weaudit(store, path)
        assert result["matched_findings"] == 1

    def test_finding_annotation_kind(self, tmp_path: Any) -> None:
        graph = _build_graph()
        store = GraphStore(graph)
        data = _minimal_weaudit(
            [
                _weaudit_entry("SQLi", "src/handler.py", 14, 16),
            ]
        )
        path = _write_json(tmp_path, "alice.weaudit", data)
        augment_from_weaudit(store, path)

        anns = graph.annotations.get("mod:func_a", [])
        assert any(a.kind == AnnotationKind.FINDING for a in anns)

    def test_note_annotation_kind(self, tmp_path: Any) -> None:
        graph = _build_graph()
        store = GraphStore(graph)
        data = _minimal_weaudit(
            [
                _weaudit_entry(
                    "Review this",
                    "src/handler.py",
                    14,
                    16,
                    entry_type=1,
                ),
            ]
        )
        path = _write_json(tmp_path, "alice.weaudit", data)
        augment_from_weaudit(store, path)

        anns = graph.annotations.get("mod:func_a", [])
        assert any(a.kind == AnnotationKind.AUDIT_NOTE for a in anns)

    def test_source_includes_author(self, tmp_path: Any) -> None:
        graph = _build_graph()
        store = GraphStore(graph)
        data = _minimal_weaudit(
            [
                _weaudit_entry("SQLi", "src/handler.py", 14, 16),
            ]
        )
        path = _write_json(tmp_path, "alice.weaudit", data)
        augment_from_weaudit(store, path)

        anns = graph.annotations.get("mod:func_a", [])
        assert any(a.source == "weaudit:alice" for a in anns)

    def test_severity_subgraphs(self, tmp_path: Any) -> None:
        graph = _build_graph()
        store = GraphStore(graph)
        data = _minimal_weaudit(
            [
                _weaudit_entry(
                    "High issue",
                    "src/handler.py",
                    14,
                    16,
                    severity="High",
                ),
                _weaudit_entry(
                    "Low issue",
                    "src/db.py",
                    4,
                    6,
                    severity="Low",
                ),
            ]
        )
        path = _write_json(tmp_path, "alice.weaudit", data)
        result = augment_from_weaudit(store, path)

        assert "weaudit:high" in result["subgraphs_created"]
        assert "weaudit:low" in result["subgraphs_created"]
        assert "weaudit:findings" in result["subgraphs_created"]

    def test_description_format(self, tmp_path: Any) -> None:
        graph = _build_graph()
        store = GraphStore(graph)
        data = _minimal_weaudit(
            [
                _weaudit_entry("SQLi", "src/handler.py", 14, 16),
            ]
        )
        path = _write_json(tmp_path, "alice.weaudit", data)
        augment_from_weaudit(store, path)

        anns = graph.annotations.get("mod:func_a", [])
        finding = next(a for a in anns if a.kind == AnnotationKind.FINDING)
        assert "[HIGH]" in finding.description
        assert "SQLi" in finding.description
        assert "[alice]" in finding.description

    def test_resolved_entries_included(self, tmp_path: Any) -> None:
        graph = _build_graph()
        store = GraphStore(graph)
        data = _minimal_weaudit([])
        data["resolvedEntries"] = [
            _weaudit_entry("Fixed issue", "src/handler.py", 14, 16),
        ]
        path = _write_json(tmp_path, "alice.weaudit", data)
        result = augment_from_weaudit(store, path)

        assert result["matched_findings"] == 1


# ── Clearing ─────────────────────────────────────────────────────────


class TestClearAugmented:
    def test_clear_sarif(self, tmp_path: Any) -> None:
        graph = _build_graph()
        store = GraphStore(graph)
        sarif = _minimal_sarif(
            [
                _sarif_result("r1", "msg", "src/handler.py", 15),
            ]
        )
        path = _write_json(tmp_path, "results.sarif", sarif)

        augment_from_sarif(store, path)
        assert len(graph.annotations) > 0

        clear_augmented(store, "sarif")
        sarif_anns = [
            a for anns in graph.annotations.values() for a in anns if a.source.startswith("sarif")
        ]
        assert sarif_anns == []

    def test_clear_removes_subgraphs(self, tmp_path: Any) -> None:
        graph = _build_graph()
        store = GraphStore(graph)
        sarif = _minimal_sarif(
            [
                _sarif_result("r1", "msg", "src/handler.py", 15),
            ]
        )
        path = _write_json(tmp_path, "results.sarif", sarif)

        augment_from_sarif(store, path)
        assert any(k.startswith("sarif:") for k in graph.subgraphs)

        clear_augmented(store, "sarif")
        assert not any(k.startswith("sarif:") for k in graph.subgraphs)

    def test_reaugment_idempotent(self, tmp_path: Any) -> None:
        graph = _build_graph()
        store = GraphStore(graph)
        sarif = _minimal_sarif(
            [
                _sarif_result("r1", "msg", "src/handler.py", 15),
            ]
        )
        path = _write_json(tmp_path, "results.sarif", sarif)

        augment_from_sarif(store, path)
        first_count = sum(len(anns) for anns in graph.annotations.values())

        # Second augment should clear and re-add (not double up)
        augment_from_sarif(store, path)
        second_count = sum(len(anns) for anns in graph.annotations.values())
        assert first_count == second_count


# ── QueryEngine integration ──────────────────────────────────────────


@pytest.mark.parametrize("source", ["sarif", "weaudit"])
@pytest.mark.parametrize("replace_existing", [False, True])
def test_multiple_finding_imports_have_explicit_replacement_policy(
    tmp_path: Path, source: str, replace_existing: bool
) -> None:
    engine = QueryEngine.from_graph(_build_graph())
    augment = getattr(engine, f"augment_{source}")
    for label, file, line in [("first", "src/handler.py", 15), ("second", "src/db.py", 10)]:
        data = (
            _minimal_sarif([_sarif_result(label, label, file, line)])
            if source == "sarif"
            else _minimal_weaudit([_weaudit_entry(label, file, line - 1, line - 1)])
        )
        path = _write_json(tmp_path, label, data)
        augment(path, replace_existing=replace_existing)

    graph = json.loads(engine.to_json())
    expected = {"mod:func_c"} if replace_existing else {"mod:func_a", "mod:module", "mod:func_c"}
    assert {finding["id"] for finding in engine.findings()} == expected
    severity_subgraph = "sarif:warning" if source == "sarif" else "weaudit:high"
    assert set(graph["subgraphs"][severity_subgraph]) == expected


@pytest.mark.parametrize("source", ["sarif", "weaudit"])
@pytest.mark.parametrize("replace_existing", [False, True])
@pytest.mark.parametrize("failure", ["missing", "malformed_json", "invalid_record"])
def test_failed_finding_import_preserves_existing_graph(
    tmp_path: Path, source: str, replace_existing: bool, failure: str
) -> None:
    engine = QueryEngine.from_graph(_build_graph())
    augment = getattr(engine, f"augment_{source}")
    original = (
        _minimal_sarif([_sarif_result("original", "original", "src/handler.py", 15)])
        if source == "sarif"
        else _minimal_weaudit([_weaudit_entry("original", "src/handler.py", 14, 14)])
    )
    augment(_write_json(tmp_path, "original", original))
    before = engine.to_json()
    findings_before = engine.findings()
    path = tmp_path / "bad"
    if failure == "malformed_json":
        path.write_text("{")
    elif failure == "invalid_record":
        # A valid record preceding the bad record must not be partially committed.
        invalid = (
            _minimal_sarif([_sarif_result("new", "new", "src/db.py", 10), {"level": 1}])
            if source == "sarif"
            else _minimal_weaudit(
                [_weaudit_entry("new", "src/db.py", 9, 9), {"details": {"severity": 1}}]
            )
        )
        path.write_text(json.dumps(invalid))
    expected_error = {
        "missing": FileNotFoundError,
        "malformed_json": json.JSONDecodeError,
        "invalid_record": ValueError,
    }[failure]
    with pytest.raises(expected_error):
        augment(str(path), replace_existing=replace_existing)
    assert engine.to_json() == before
    assert engine.findings() == findings_before


@pytest.mark.parametrize("source", ["sarif", "weaudit"])
@pytest.mark.parametrize("root_kind", ["dot", "relative", "absolute"])
@pytest.mark.parametrize("language", ["python", "auto"])
def test_findings_match_real_parser_paths_for_analysis_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str, root_kind: str, language: str
) -> None:
    monkeypatch.chdir(tmp_path)
    project = tmp_path if root_kind == "dot" else tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "handler.py").write_text("def handler():\n    pass\n")
    root = {"dot": ".", "relative": "project", "absolute": str(project)}[root_kind]
    engine = QueryEngine.from_directory(root, language=language)
    report = (
        _minimal_sarif([_sarif_result("r1", "msg", "src/handler.py", 2)])
        if source == "sarif"
        else _minimal_weaudit([_weaudit_entry("r1", "src/handler.py", 1, 1)])
    )

    result = getattr(engine, f"augment_{source}")(_write_json(tmp_path, "report.json", report))

    assert result["matched_findings"] == 1
    assert any(node["id"].endswith(":handler") for node in engine.findings())


@pytest.mark.parametrize("source", ["sarif", "weaudit"])
def test_findings_attach_to_source_owners_instead_of_shared_proxies(
    tmp_path: Path, source: str
) -> None:
    (tmp_path / "app.py").write_text(
        "def bad():\n    requests.get('bad')\n\ndef main():\n    requests.get('good')\n"
    )
    engine = QueryEngine.from_directory(str(tmp_path))
    report = (
        _minimal_sarif([_sarif_result("r1", "msg", "app.py", 2)])
        if source == "sarif"
        else _minimal_weaudit([_weaudit_entry("r1", "app.py", 1, 1)])
    )

    getattr(engine, f"augment_{source}")(_write_json(tmp_path, "report.json", report))

    assert {node["id"] for node in engine.findings()} == {"app", "app:bad"}


@pytest.mark.parametrize("origin", [NodeOrigin.BINARY, NodeOrigin.SYNTHETIC])
def test_overlapping_non_source_nodes_are_not_finding_owners(origin: NodeOrigin) -> None:
    unit = CodeUnit(
        "external", "external", NodeKind.FUNCTION, _loc("src/handler.py", 10, 30), origin=origin
    )
    graph = _build_graph()
    graph.nodes[unit.id] = unit

    assert _find_overlapping_nodes(graph, "src/handler.py", 15, 15) == ["mod:func_a", "mod:module"]


@pytest.mark.parametrize(
    ("source", "data", "context"),
    [
        ("sarif", [], "SARIF must be an object"),
        ("sarif", {"runs": {}}, "SARIF.runs must be an array"),
        ("sarif", {"runs": [None]}, "SARIF.runs[0] must be an object"),
        ("sarif", _minimal_sarif([{"level": 1}]), "SARIF.runs[0].results[0].level"),
        ("sarif", _minimal_sarif([{"message": []}]), "SARIF.runs[0].results[0].message"),
        ("sarif", _minimal_sarif([{"locations": [None]}]), "SARIF.runs[0].results[0].locations[0]"),
        ("weaudit", [], "weAudit must be an object"),
        ("weaudit", {"treeEntries": {}}, "weAudit.treeEntries must be an array"),
        ("weaudit", {"resolvedEntries": [None]}, "weAudit.resolvedEntries[0]"),
        (
            "weaudit",
            {"treeEntries": [{"details": {"severity": 1}}]},
            "weAudit.treeEntries[0].details.severity",
        ),
        (
            "weaudit",
            {"treeEntries": [{"locations": [{"startLine": "1"}]}]},
            "weAudit.treeEntries[0].locations[0].startLine",
        ),
    ],
)
def test_malformed_finding_fields_raise_contextual_value_error(
    tmp_path: Path, source: str, data: object, context: str
) -> None:
    engine = QueryEngine.from_graph(_build_graph())
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(data))

    with pytest.raises(ValueError) as exc:
        getattr(engine, f"augment_{source}")(str(path))

    assert context in str(exc.value)


class TestQueryEngineAugment:
    def test_augment_sarif_via_engine(self, tmp_path: Any) -> None:
        graph = _build_graph()
        engine = QueryEngine.from_graph(graph)
        sarif = _minimal_sarif(
            [
                _sarif_result("r1", "msg", "src/handler.py", 15),
            ]
        )
        path = _write_json(tmp_path, "results.sarif", sarif)

        result = engine.augment_sarif(path)
        assert result["matched_findings"] == 1

    def test_findings_method(self, tmp_path: Any) -> None:
        graph = _build_graph()
        engine = QueryEngine.from_graph(graph)
        sarif = _minimal_sarif(
            [
                _sarif_result(
                    "r1",
                    "msg",
                    "src/handler.py",
                    15,
                    level="error",
                ),
            ]
        )
        path = _write_json(tmp_path, "results.sarif", sarif)
        engine.augment_sarif(path)

        findings = engine.findings()
        assert len(findings) >= 1
        assert "findings" in findings[0]
        assert findings[0]["findings"][0]["kind"] == "finding"

    def test_findings_filter_by_kind(self, tmp_path: Any) -> None:
        graph = _build_graph()
        engine = QueryEngine.from_graph(graph)

        # Add both SARIF finding and weAudit note
        sarif = _minimal_sarif(
            [
                _sarif_result("r1", "msg", "src/handler.py", 15),
            ]
        )
        sarif_path = _write_json(tmp_path, "results.sarif", sarif)
        engine.augment_sarif(sarif_path)

        weaudit = _minimal_weaudit(
            [
                _weaudit_entry(
                    "Note",
                    "src/db.py",
                    4,
                    6,
                    entry_type=1,
                ),
            ]
        )
        weaudit_path = _write_json(tmp_path, "a.weaudit", weaudit)
        engine.augment_weaudit(weaudit_path)

        notes = engine.findings(kind=AnnotationKind.AUDIT_NOTE)
        findings = engine.findings(kind=AnnotationKind.FINDING)
        assert len(notes) >= 1
        assert len(findings) >= 1
        assert all(f["findings"][0]["kind"] == "audit_note" for f in notes)
