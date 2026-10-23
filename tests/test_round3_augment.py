"""Filesystem names retain URI delimiters and literal percent sequences."""

import json
from pathlib import Path

import pytest

from trailmark.analysis.augment import augment_from_sarif, augment_from_weaudit
from trailmark.models import NodeKind
from trailmark.parse import parse_directory
from trailmark.storage.graph_store import GraphStore


@pytest.mark.parametrize("folder", ["C#", "my%20dir", "what?dir"])
@pytest.mark.parametrize("encoded", [False, True])
@pytest.mark.parametrize("defined_base", [False, True])
def test_sarif_matches_only_the_named_file(
    tmp_path: Path, folder: str, encoded: bool, defined_base: bool
) -> None:
    directory = tmp_path / folder
    directory.mkdir()
    for name in ("a", "z"):
        (directory / f"{name}.py").write_text(f"def f{name}():\n return 1\n")
    uri = (directory / "a.py").as_uri() if encoded else f"{folder}/a.py".replace("%", "%25")
    report = tmp_path / "report.sarif"
    artifact = {"uri": uri}
    if defined_base:
        artifact["uriBaseId"] = "ROOT"
    report.write_text(
        json.dumps(
            {
                "version": "2.1.0",
                "runs": [
                    {
                        "tool": {"driver": {"name": "test"}},
                        "originalUriBaseIds": {"ROOT": {"uri": tmp_path.as_uri() + "/"}},
                        "results": [
                            {
                                "message": {"text": "finding"},
                                "locations": [
                                    {
                                        "physicalLocation": {
                                            "artifactLocation": artifact,
                                            "region": {"startLine": 2},
                                        }
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        )
    )
    graph = parse_directory(str(tmp_path), "python")
    augment_from_sarif(GraphStore(graph), str(report))
    assert {
        graph.nodes[key].name
        for key in graph.annotations
        if graph.nodes[key].kind == NodeKind.FUNCTION
    } == {"fa"}


def test_weaudit_paths_are_filesystem_paths(tmp_path: Path) -> None:
    directory = tmp_path / "C#%20?"
    directory.mkdir()
    (directory / "a.py").write_text("def fa():\n return 1\n")
    (directory / "z.py").write_text("def fz():\n return 1\n")
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "treeEntries": [
                    {
                        "label": "finding",
                        "locations": [{"path": "C#%20?/a.py", "startLine": 1, "endLine": 1}],
                    }
                ]
            }
        )
    )
    graph = parse_directory(str(tmp_path), "python")
    augment_from_weaudit(GraphStore(graph), str(report))
    assert {
        graph.nodes[key].name
        for key in graph.annotations
        if graph.nodes[key].kind == NodeKind.FUNCTION
    } == {"fa"}
