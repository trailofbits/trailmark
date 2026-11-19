"""Controls for preregistered scopes, source integrity, and parallel partitioning."""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from tests.graph_eval import pilot
from tests.graph_eval.util import case_partition, digest


def test_shards_are_complete_disjoint_and_stable_under_reordering() -> None:
    cases = [f"language--project-{index}--stress" for index in range(101)]
    for total in (1, 3, 8, 128):
        partitions = {
            index: {case for case in cases if case_partition(case, index, total)}
            for index in range(1, total + 1)
        }
        assert Counter(case for group in partitions.values() for case in group) == Counter(cases)
        assert partitions == {
            index: {case for case in reversed(cases) if case_partition(case, index, total)}
            for index in range(1, total + 1)
        }


@pytest.mark.parametrize(("index", "total"), [(0, 1), (2, 1), (1, 0), (1, -1)])
def test_invalid_shards_are_rejected(index: int, total: int) -> None:
    with pytest.raises(ValueError, match="Shard"):
        case_partition("case", index, total)


@pytest.fixture
def selected_pilot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    languages = ("python", "typescript", "rust", "solidity", "fstar", "tlaplus", "graphql", "sql")
    projects, scopes = [], {}
    for index in range(16):
        name = f"project-{index}"
        source = tmp_path / name
        source.mkdir()
        (source / "ordinary.txt").write_text("first\nordinary declaration\nthird\n")
        (source / "stress.txt").write_text("stress declaration\n")
        projects.append(
            {
                "id": name,
                "language": languages[index // 2],
                "revision": f"{index:040x}",
                "roots": {"sources": str(source)},
            }
        )
        scopes[name] = (
            ("ordinary.txt", (2, 2), "Ordinary source rationale"),
            ("stress.txt", None, "Stress source rationale"),
        )
    monkeypatch.setattr(pilot, "SCOPES", scopes)
    return projects, pilot.select_cases(projects)


def test_selection_freezes_balanced_source_scopes_and_provenance(
    selected_pilot: tuple[list[dict[str, Any]], list[dict[str, Any]]],
) -> None:
    projects, cases = selected_pilot
    assert len(cases) == 32
    assert len({case["id"] for case in cases}) == 32
    assert Counter(case["language"] for case in cases) == dict.fromkeys(
        ("python", "typescript", "rust", "solidity", "fstar", "tlaplus", "graphql", "sql"), 4
    )
    assert Counter(case["stratum"] for case in cases) == {"ordinary": 16, "stress": 16}
    assert Counter(case["profile"] for case in cases) == {
        "runtime": 16,
        "formal": 8,
        "schema": 4,
        "sql": 4,
    }
    assert cases == pilot.select_cases(projects)
    for case in cases:
        assert case["input_hash"] == digest({k: v for k, v in case.items() if k != "input_hash"})
        project = next(item for item in projects if item["id"] == case["project"])
        assert case["source_revision"] == project["revision"]
        assert "no whole-project negative claims" in case["scope_policy"]
        assert case["context"] == []
        if case["stratum"] == "ordinary":
            assert case["primary"] == [
                {
                    "path": "ordinary.txt",
                    "start_line": 2,
                    "end_line": 2,
                    "sha256": hashlib.sha256(b"ordinary declaration").hexdigest(),
                }
            ]


def test_selection_rejects_an_incomplete_pilot(
    selected_pilot: tuple[list[dict[str, Any]], list[dict[str, Any]]],
) -> None:
    projects, _ = selected_pilot
    with pytest.raises(ValueError, match="exactly 32"):
        pilot.select_cases(projects[:-1])


def test_source_bundle_keeps_original_line_numbers_and_detects_tampering(
    selected_pilot: tuple[list[dict[str, Any]], list[dict[str, Any]]],
) -> None:
    projects, cases = selected_pilot
    source = Path(projects[0]["roots"]["sources"])
    case = {**cases[0], "context": cases[1]["primary"]}
    bundle = pilot.source_bundle(source, case)
    assert "ordinary.txt:2-2" in bundle
    assert "2: ordinary declaration" in bundle
    assert "1: stress declaration" in bundle
    assert "first" not in bundle
    (source / "stress.txt").write_text("tampered context\n")
    with pytest.raises(ValueError, match="Source scope changed: stress.txt"):
        pilot.source_bundle(source, case)
    (source / "stress.txt").write_text("stress declaration\n")
    (source / "ordinary.txt").write_text("first\ntampered primary\nthird\n")
    with pytest.raises(ValueError, match="Source scope changed: ordinary.txt"):
        pilot.source_bundle(source, case)
