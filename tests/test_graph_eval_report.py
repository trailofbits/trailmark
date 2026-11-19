"""Reports preserve validator limits and keep model classifications unverified."""

from __future__ import annotations

import pytest

from tests.graph_eval.report import generate_report
from tests.graph_eval.schema import CATEGORIES


@pytest.mark.parametrize("category", CATEGORIES)
def test_every_model_category_remains_unverified_pending_independent_review(category: str) -> None:
    report = generate_report(
        {"cases": [{"id": "case", "language": "python"}]},
        [
            {
                "case_id": "case",
                "status": "adjudicated",
                "measurement_status": "ok",
                "adjudication": {
                    "schema_version": 1,
                    "case_id": "case",
                    "notes": [],
                    "decisions": [
                        {
                            "claim_id": "assertion:call",
                            "category": category,
                            "evidence": "not checked",
                            "expected": "not checked",
                            "observed": "not checked",
                            "reproduction": "not available",
                        }
                    ],
                },
                "replay": {"graph_identical": True},
            }
        ],
    )
    assert "All model adjudicator classifications are unverified" in report
    assert "pending independent source review and executed-reproduction review" in report
    assert "## Unverified model decision categories" in report
    assert f"- {category}: 1" in report
    assert "## Adjudicated findings" not in report
    if category in {"confirmed_defect", "documented_limitation", "capability_need"}:
        assert f"Model category (unverified): **{category}**" in report
        assert "Proposed reproduction (unverified): not available" in report
        assert "it does not validate the model's diagnosis" in report


def test_report_exposes_validator_outcomes_scopes_and_structured_diagnostics() -> None:
    report = generate_report(
        {"cases": [{"id": "case", "language": "python"}]},
        [
            {
                "case_id": "case",
                "validation": {
                    "status": "completed",
                    "files": [
                        {
                            "path": "valid.py",
                            "validator": "python.ast",
                            "version": "3.15.2",
                            "status": "accepted",
                            "scope": "Syntax only",
                            "diagnostics": [],
                        },
                        {
                            "path": "broken.py",
                            "validator": "python.ast",
                            "version": "3.15.2",
                            "status": "rejected",
                            "scope": "Syntax only",
                            "diagnostics": [{"line": 7, "column": 2, "message": "invalid syntax"}],
                        },
                        {
                            "path": "schema.sql",
                            "validator": "pglast",
                            "status": "unavailable",
                            "scope": "Outer statements only",
                            "reason": "Install locked dependency",
                        },
                        {
                            "path": "module.rs",
                            "validator": None,
                            "status": "not_configured",
                            "scope": "No independent validator configured",
                        },
                    ],
                },
            }
        ],
    )
    assert "## Independent validation evidence" in report
    assert "| valid.py | python.ast | 3.15.2 | accepted | Syntax only |" in report
    assert "| broken.py | python.ast | 3.15.2 | rejected |" in report
    assert '"line": 7' in report and "invalid syntax" in report
    assert "unavailable | Outer statements only | Install locked dependency" in report
    assert "not_configured | No independent validator configured" in report
    assert "Acceptance does not establish graph correctness" in report


def test_missing_or_empty_validation_is_explicit_instead_of_a_pass() -> None:
    report = generate_report(
        {"cases": [{"id": "missing"}, {"id": "empty"}]},
        [
            {"case_id": "missing"},
            {
                "case_id": "empty",
                "validation": {
                    "status": "completed",
                    "files": [],
                    "counts": {"unavailable": 2},
                },
            },
        ],
    )
    assert "not_recorded" in report
    assert "Independent validation evidence was not recorded" in report
    assert "Recorded counts: {'unavailable': 2}" in report
    assert "accepted" not in report
