"""Session safeguards shared by all snapshot suites."""

from __future__ import annotations

import os

import pytest


def pytest_sessionstart(session: pytest.Session) -> None:
    if os.environ.get("TRAILMARK_UPDATE_SNAPSHOTS") == "1" and (
        os.environ.get("CI", "").lower() not in {"", "0", "false"}
        or session.config.getoption("gremlins", default=False)
        or "ACTIVE_GREMLIN" in os.environ
        or "PYTEST_GREMLINS_SOURCES_FILE" in os.environ
    ):
        raise pytest.UsageError("Snapshot updates are disabled in CI and mutation testing")
