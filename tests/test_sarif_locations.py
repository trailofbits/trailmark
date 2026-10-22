"""Common producer URI bases, bounded chains, and base-directory containment."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from trailmark.analysis._sarif_uris import SarifUris
from trailmark.analysis.augment import augment_from_sarif
from trailmark.parse import parse_directory
from trailmark.storage.graph_store import GraphStore


def test_semgrep_undefined_source_root_base_matches(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "app.py").write_text("import os\ndef run(cmd):\n    os.system(cmd)\n")
    run = {
        "tool": {"driver": {"name": "Semgrep OSS", "rules": [{"id": "os-system"}]}},
        "invocations": [{"executionSuccessful": True}],
        "results": [
            {
                "ruleId": "os-system",
                "level": "warning",
                "message": {"text": "os.system"},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": "src/app.py", "uriBaseId": "%SRCROOT%"},
                            "region": {"startLine": 3, "startColumn": 5, "endLine": 3},
                        }
                    }
                ],
            }
        ],
    }
    report = tmp_path / "semgrep.sarif"
    report.write_text(json.dumps({"version": "2.1.0", "runs": [run]}))
    store = GraphStore(parse_directory(str(tmp_path), "python"))
    summary = augment_from_sarif(store, str(report))
    assert summary["matched_findings"] == 1
    assert summary["unmatched_findings"] == 0


def test_long_base_chain_is_unmatched_without_recursing(tmp_path: Path) -> None:
    run = {
        "originalUriBaseIds": {
            str(i): {"uri": "part/", "uriBaseId": str(i + 1)} for i in range(1500)
        }
    }
    run["originalUriBaseIds"]["1500"] = {"uri": tmp_path.as_uri() + "/"}
    assert SarifUris(run).resolve({"uri": "app.py", "uriBaseId": "0"}) is None


def test_artifact_index_and_base_name_have_separate_namespaces(tmp_path: Path) -> None:
    run = {
        "artifacts": [{"location": {"uri": "app.py", "uriBaseId": "index:0"}}],
        "originalUriBaseIds": {"index:0": {"uri": tmp_path.as_uri() + "/"}},
    }
    assert SarifUris(run).resolve({"index": 0}) == (tmp_path / "app.py").as_uri()


@pytest.mark.parametrize(
    "suffix", ["../app.py", "%2e%2e/app.py", "nested/../../app.py", "\\..\\app.py"]
)
def test_relative_uri_cannot_escape_a_defined_base(suffix: str) -> None:
    resolver = SarifUris({"originalUriBaseIds": {"ROOT": {"uri": "file:///"}}})
    assert resolver.resolve({"uri": suffix, "uriBaseId": "ROOT"}) is None


def test_defined_base_requires_a_directory_uri() -> None:
    resolver = SarifUris({"originalUriBaseIds": {"ROOT": {"uri": "file:///project/src"}}})
    assert resolver.resolve({"uri": "app.py", "uriBaseId": "ROOT"}) is None


def test_successful_base_chains_are_cached_for_repeated_findings() -> None:
    resolver = SarifUris(
        {
            "originalUriBaseIds": {
                "ROOT": {"uri": "file:///project/"},
                "SRC": {"uri": "src/", "uriBaseId": "ROOT"},
            }
        }
    )
    with patch.object(resolver, "_location", wraps=resolver._location) as resolve:
        assert (
            resolver.resolve({"uri": "first.py", "uriBaseId": "SRC"})
            == "file:///project/src/first.py"
        )
        before = resolve.call_count
        for _ in range(100):
            assert (
                resolver.resolve({"uri": "other.py", "uriBaseId": "SRC"})
                == "file:///project/src/other.py"
            )
        assert resolve.call_count == before + 100
