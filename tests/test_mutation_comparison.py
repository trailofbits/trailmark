"""Mutation pool comparisons require identical complete, conclusive inventories."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
from pytest_gremlins.instrumentation.transformer import transform_source

from tests.compare_mutations import compare
from tests.mutation_inventory import inventory
from tests.run_mutations import RUNNER, VERSION

pytestmark = pytest.mark.mutation_infrastructure


@pytest.fixture
def reports(tmp_path: Path) -> tuple[Path, Path, Path]:
    source = "def checked(value):\n    return value > 0\n"
    target = tmp_path / "target.py"
    target.write_text(source)
    gremlins, _ = transform_source(source, str(target))
    catalog = inventory(gremlins, tmp_path)
    for group in ("full", "fast"):
        directory = tmp_path / group
        directory.mkdir()
        run = {
            "inputs": "same frozen inputs",
            "sources": {"target.py": hashlib.sha256(source.encode()).hexdigest()},
            "targets": "target.py",
            "tests": ["test_target.py"],
            "runner": RUNNER,
            "pytest_gremlins": VERSION,
            "test_order": "collection",
            "workers": 2,
            "test_selection": "full" if group == "full" else "coverage",
            "test_group": group,
            "shard": [1, 1],
            "baseline_validation": "local",
            "kill_confirmation": "baseline-orders" if group == "full" else "failure-prefix",
        }
        rows = [
            {
                "gremlin_id": row["gremlin_id"],
                "status": "zapped",
                "selected_tests": ["test_target.py::test_boundary"],
            }
            for row in catalog
        ]
        (directory / "run.json").write_text(json.dumps(run))
        (directory / "inventory.json").write_text(json.dumps(catalog))
        (directory / "gremlins.json").write_text(
            json.dumps({"results": rows, "summary": {"total": len(rows), "zapped": len(rows)}})
        )
    return tmp_path, tmp_path / "full", tmp_path / "fast"


def test_comparison_records_lost_kills(reports: tuple[Path, Path, Path]) -> None:
    root, full, fast = reports
    assert compare(full, fast, root=root)["lost_kills"] == 0
    path = fast / "gremlins.json"
    report = json.loads(path.read_text())
    report["results"][0]["status"] = "survived"
    report["summary"] = {
        "total": len(report["results"]),
        **Counter(r["status"] for r in report["results"]),
    }
    path.write_text(json.dumps(report))
    result = compare(full, fast, root=root)
    assert result["lost_kills"] == 1
    assert result["differences"][0]["candidate"] == "survived"


@pytest.mark.parametrize(
    "corruption", ["missing", "duplicate", "inconclusive", "inputs", "source", "shard", "reference"]
)
def test_comparison_rejects_invalid_evidence(
    reports: tuple[Path, Path, Path], corruption: str
) -> None:
    root, full, fast = reports
    path = fast / "gremlins.json"
    report = json.loads(path.read_text())
    run_path = fast / "run.json"
    run = json.loads(run_path.read_text())
    if corruption == "missing":
        report["results"].pop()
    elif corruption == "duplicate":
        report["results"].append(report["results"][0])
    elif corruption == "inconclusive":
        report["results"][0]["status"] = "timeout"
    elif corruption == "inputs":
        run["inputs"] = "different inputs"
    elif corruption == "source":
        (root / "target.py").write_text("def checked(value): return False\n")
    elif corruption == "reference":
        full_run = json.loads((full / "run.json").read_text())
        full_run["test_selection"] = "coverage"
        (full / "run.json").write_text(json.dumps(full_run))
    else:
        run["shard"] = [1, 2]
    path.write_text(json.dumps(report))
    run_path.write_text(json.dumps(run))
    with pytest.raises(ValueError):
        compare(full, fast, root=root)


def test_coverage_reference_requires_opt_in_and_the_full_pool(
    reports: tuple[Path, Path, Path],
) -> None:
    root, full, fast = reports
    path = full / "run.json"
    run = json.loads(path.read_text())
    run.update(test_selection="coverage", kill_confirmation="failure-prefix")
    path.write_text(json.dumps(run))
    with pytest.raises(ValueError, match="without coverage filtering"):
        compare(full, fast, root=root)
    assert compare(full, fast, root=root, require_full_reference=False)["lost_kills"] == 0
    run["test_group"] = "fast"
    path.write_text(json.dumps(run))
    with pytest.raises(ValueError, match="full test pool"):
        compare(full, fast, root=root, require_full_reference=False)


@pytest.mark.parametrize("selection", ["full", "coverage"])
def test_real_pools_keep_separate_reports_and_record_actual_witnesses(
    tmp_path: Path, selection: str
) -> None:
    (tmp_path / ".gitignore").write_text(
        Path(__file__).parents[1].joinpath(".gitignore").read_text()
    )
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)  # noqa: S603, S607
    (tmp_path / "target.py").write_text("def checked(value):\n    return value > 0\n")
    (tmp_path / "test_target.py").write_text(
        "import pytest\nfrom target import checked\n"
        "@pytest.mark.mutation_fast\ndef test_positive():\n    assert checked(10)\n"
        "def test_boundary():\n    assert checked(0) is False\n    assert checked(1) is True\n"
    )
    runner = Path(__file__).with_name("run_mutations.py")
    for group in ("full", "fast"):
        result = subprocess.run(  # noqa: S603
            [
                sys.executable,
                str(runner),
                "--targets=target.py",
                *(["--test-group=full"] if group == "full" else []),
                f"--test-selection={selection}",
                "--test-order=collection",
                "--workers=2",
                f"--report-dir=coverage/{group}",
                "test_target.py",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        directory = tmp_path / "coverage" / group
        assert json.loads((directory / "run.json").read_text())["test_group"] == group
        report = json.loads((directory / "gremlins.json").read_text())
        assert all(
            row["killing_test"].startswith("test_target.py::")
            for row in report["results"]
            if row["status"] == "zapped"
        )
        if group == "fast":
            assert all(
                row["selected_tests"] == ["test_target.py::test_positive"]
                for row in report["results"]
            )
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(Path(__file__).parents[1]), env.get("PYTHONPATH")))
    )
    compared = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "tests.compare_mutations",
            "coverage/full",
            "coverage/fast",
            *(["--allow-coverage-reference"] if selection == "coverage" else []),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    comparison = json.loads(compared.stdout)
    assert compared.returncode == 1, compared.stdout + compared.stderr
    assert comparison["lost_kills"] > 0
