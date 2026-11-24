"""Recognize application exits during collection without hiding harness errors."""

from pathlib import Path

import pytest


def record_source_collection_exits(directory: Path) -> None:
    """Extend the pinned bootstrap's load recorder, retaining upstream confirmation."""
    path = directory / "gremlin_bootstrap.py"
    source = path.read_text()
    replacements = {
        "            self.suite_failed_to_load = False\n": (
            "            self.suite_failed_to_load = False\n"
            "            self.source_collection_exit = False\n"
        ),
        "        def pytest_collection(self, session):\n            outcome = yield\n": (
            "        def pytest_collection(self, session):\n"
            "            outcome = yield\n"
            "            if outcome.excinfo is not None and issubclass(\n"
            "                outcome.excinfo[0], SystemExit\n"
            "            ):\n"
            "                self.source_collection_exit = any(\n"
            "                    normalize_origin(frame.f_code.co_filename) "
            "in instrumented_sources\n"
            "                    for frame, _ in traceback.walk_tb(outcome.excinfo[2])\n"
            "                )\n"
            "                if self.source_collection_exit:\n"
            "                    self.suite_failed_to_load = True\n"
        ),
        "    load_failure_exit = exit_code in (pytest.ExitCode.USAGE_ERROR, "
        "pytest.ExitCode.INTERRUPTED)\n": (
            "    load_failure_exit = (\n"
            "        exit_code in (pytest.ExitCode.USAGE_ERROR, pytest.ExitCode.INTERRUPTED)\n"
            "        or (exit_code == pytest.ExitCode.INTERNAL_ERROR "
            "and recorder.source_collection_exit)\n"
            "    )\n"
        ),
    }
    for original, replacement in replacements.items():
        if source.count(original) != 1:
            raise pytest.UsageError("Revalidate the pinned mutation bootstrap adapter")
        source = source.replace(original, replacement)
    path.write_text(source)
