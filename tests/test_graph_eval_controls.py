"""Run scientific controls against the installed frozen Trailmark interpreter."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.graph_eval import controls


def test_real_parser_metamorphic_and_corruption_controls(tmp_path: Path) -> None:
    report = controls.run_controls(tmp_path, Path(sys.executable))
    assert report["status"] == "ok"
    assert report["model_jobs"] == 0
    assert len(report["controls"]) == 12
    assert all(row["status"] == "passed" for row in report["controls"])
    assert json.loads((tmp_path / "controls.json").read_text()) == report
    for name in ("baseline", "alpha_rename", "definition_order", "unreachable_helper"):
        measured = json.loads((tmp_path / name / "measurement" / "measurement.json").read_text())
        comparison = json.loads((tmp_path / name / "comparison.json").read_text())
        assert measured["status"] == "ok" and measured["deterministic"] is True
        assert comparison["metrics"]["call_sites"]["retained"] == 1
        assert comparison["metrics"]["targets"]["resolved"] == 1


def test_measurement_failure_does_not_report_successful_controls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failed_measurement(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"status": "crashed", "error": "Deliberate worker failure"}

    monkeypatch.setattr(controls, "measure_project", failed_measurement)
    report = controls.run_controls(tmp_path, Path(sys.executable))
    assert report["status"] == "failed"
    assert [row["status"] for row in report["controls"]] == ["failed"] * 4 + ["not_run"]
    assert report["controls"][-1]["reason"] == "Real-parser baseline failed"
