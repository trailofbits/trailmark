"""KAT snapshot tests for trailmark.diagram emitters.

Each test parses the shared Python taxonomy fixture and asserts the exact
Mermaid output produced by one diagram emitter. Snapshots can be regenerated
with TRAILMARK_UPDATE_SNAPSHOTS=1.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from trailmark.diagram import (
    emit_call_graph,
    emit_class_hierarchy,
    emit_complexity,
    emit_containment,
    emit_data_flow,
    emit_module_deps,
)
from trailmark.parse import parse_file
from trailmark.query.api import QueryEngine

FIXTURE = Path(__file__).parent / "fixtures" / "kat" / "python" / "taxonomy.py"
SNAPSHOT_DIR = Path(__file__).parent / "fixtures" / "kat" / "diagram"
UPDATE_ENV = "TRAILMARK_UPDATE_SNAPSHOTS"


def _normalize(text: str) -> str:
    """Strip absolute paths that may leak into diagram output as labels."""
    here = str(FIXTURE.resolve())
    return text.replace(here, "taxonomy.py")


def _engine() -> QueryEngine:
    graph = parse_file(str(FIXTURE), language="python")
    return QueryEngine.from_graph(graph)


def _assert_or_update(name: str, actual: str) -> None:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = SNAPSHOT_DIR / f"{name}.expected.mmd"
    actual_norm = _normalize(actual)
    if not actual_norm.endswith("\n"):
        actual_norm = actual_norm + "\n"

    if os.environ.get(UPDATE_ENV) == "1":
        snapshot.write_text(actual_norm)
        return

    if not snapshot.exists():
        msg = f"Missing snapshot: {snapshot}. Set {UPDATE_ENV}=1 to create."
        raise AssertionError(msg)

    expected = snapshot.read_text()
    if actual_norm != expected:
        msg = (
            f"Mermaid snapshot mismatch for {name}.\n"
            f"Run with {UPDATE_ENV}=1 to regenerate {snapshot.name}."
        )
        raise AssertionError(msg)


@pytest.mark.parametrize("direction", ["LR", "TD"])
def test_call_graph_snapshot(direction: str) -> None:
    out = emit_call_graph(_engine(), focus=None, depth=2, direction=direction)
    _assert_or_update(f"call_graph_{direction}", out)


def test_call_graph_focused_snapshot() -> None:
    out = emit_call_graph(_engine(), focus="bark", depth=1, direction="LR")
    _assert_or_update("call_graph_focus_bark", out)


def test_class_hierarchy_snapshot() -> None:
    out = emit_class_hierarchy(_engine(), direction="TB")
    _assert_or_update("class_hierarchy", out)


def test_module_deps_snapshot() -> None:
    out = emit_module_deps(_engine(), direction="LR")
    _assert_or_update("module_deps", out)


def test_containment_snapshot() -> None:
    out = emit_containment(_engine(), direction="LR")
    _assert_or_update("containment", out)


def test_complexity_snapshot() -> None:
    out = emit_complexity(_engine(), threshold=2, direction="LR")
    _assert_or_update("complexity_t2", out)


def test_data_flow_snapshot() -> None:
    out = emit_data_flow(_engine(), focus="branchy", depth=2, direction="LR")
    _assert_or_update("data_flow_branchy", out)
