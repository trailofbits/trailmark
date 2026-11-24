"""Shared baselines must cover every order and match the campaign's inputs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.mutation_controls import baseline_receipt, input_fingerprint, validate_baselines

pytestmark = pytest.mark.mutation_infrastructure


@pytest.fixture
def receipts(tmp_path: Path) -> tuple[Path, dict, list[tuple[str, ...]]]:
    context = {"inputs": "checked input fingerprint", "test_order": "component"}
    orders = [("first", "second"), ("second", "first"), ("third", "first")]
    directory = tmp_path / "receipts"
    directory.mkdir()
    for index in range(1, 3):
        (directory / f"baseline-{index}-of-2.json").write_text(
            json.dumps(baseline_receipt(context, orders, (index, 2)))
        )
    return directory, context, orders


def test_shared_baselines_accept_complete_disjoint_orders(receipts: tuple) -> None:
    validate_baselines(*receipts)


@pytest.mark.parametrize(
    "corruption",
    ["missing", "duplicate", "unchecked order", "stale inputs", "wrong order", "invalid partition"],
)
def test_shared_baselines_reject_incomplete_or_stale_checks(
    receipts: tuple, corruption: str
) -> None:
    directory, context, orders = receipts
    path = directory / "baseline-2-of-2.json"
    receipt = json.loads(path.read_text())
    if corruption == "missing":
        path.unlink()
    else:
        if corruption == "duplicate":
            receipt = json.loads((directory / "baseline-1-of-2.json").read_text())
        elif corruption == "unchecked order":
            receipt["checked"] = []
        elif corruption == "stale inputs":
            receipt["context"]["inputs"] = "different inputs"
        elif corruption == "wrong order":
            receipt["orders"].reverse()
        else:
            receipt["shard"] = [2, 3]
        path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="baselines"):
        validate_baselines(directory, context, orders)


def test_shared_baselines_reject_changed_test_collection(receipts: tuple) -> None:
    directory, context, orders = receipts
    with pytest.raises(ValueError, match="baselines"):
        validate_baselines(directory, context, [*orders, ("new_test",)])


def test_input_identity_includes_helpers_fixtures_and_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = tmp_path / "helper.py"
    helper.write_text("VALUE = 1\n")
    fixture = tmp_path / "resource.txt"
    fixture.write_text("original fixture")
    before = input_fingerprint(tmp_path)
    helper.write_text("VALUE = 2\n")
    assert input_fingerprint(tmp_path) != before
    helper.write_text("VALUE = 1\n")
    fixture.write_text("changed fixture")
    assert input_fingerprint(tmp_path) != before
    fixture.write_text("original fixture")
    monkeypatch.setenv("TRAILMARK_REQUIRE_BINARY_TOOLS", "changed")
    assert input_fingerprint(tmp_path) != before


def test_input_identity_ignores_report_and_bytecode_outputs(tmp_path: Path) -> None:
    (tmp_path / "helper.py").write_text("VALUE = 1\n")
    before = input_fingerprint(tmp_path)
    for name in ("coverage", "__pycache__", ".pytest_cache"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "output").write_text("generated output")
    assert input_fingerprint(tmp_path) == before


def test_input_identity_includes_native_tool_contents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = tmp_path / "objdump"
    tool.write_text("first tool build")
    monkeypatch.setenv("TRAILMARK_GNU_OBJDUMP", str(tool))
    # Keep the executable outside the scanned project so only the tool identity changes.
    project = tmp_path / "project"
    project.mkdir()
    before = input_fingerprint(project)
    tool.write_text("second tool build")
    assert input_fingerprint(project) != before
