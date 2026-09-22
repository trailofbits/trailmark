"""Run pytest-gremlins with complete pytest semantics and baseline controls.

Run as ``uv run python -m tests.run_mutations --targets=src/trailmark/...``.
Remove the version-specific adapter after upstream issues #486 and #415 and
package-import handling are fixed, retaining the behavioral control tests.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path
from unittest.mock import patch

import pytest
from pytest_gremlins import plugin

VERSION = "1.9.0"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets", default="src/trailmark")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("tests", nargs="*", default=["tests"])
    args = parser.parse_args()
    if version("pytest-gremlins") != VERSION:
        parser.error(
            f"Revalidate the mutation runner adapter before changing pytest-gremlins {VERSION}"
        )
    if os.environ.get("TRAILMARK_UPDATE_SNAPSHOTS") == "1":
        parser.error("Snapshot updates are disabled in mutation testing")
    report_dir = Path("coverage/gremlins")
    report_dir.mkdir(parents=True, exist_ok=True)
    for name in ("run.json", "gremlins.json"):
        (report_dir / name).unlink(missing_ok=True)
    baseline = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", "-q", *args.tests], check=False
    )
    if baseline.returncode:
        return baseline.returncode
    source_paths = []
    for target in args.targets.split(","):
        path = Path(target)
        source_paths.extend(path.rglob("*.py") if path.is_dir() else [path])
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths}
    bootstrap = Path(__file__).with_name("mutation_bootstrap.py").read_text()
    write_sources = plugin._write_instrumented_sources

    def write_and_check(instrumented_asts: dict[str, ast.Module], rootdir: Path) -> Path:
        directory = write_sources(instrumented_asts, rootdir)
        env = os.environ.copy()
        env.pop("ACTIVE_GREMLIN", None)
        env["PYTEST_GREMLINS_SOURCES_FILE"] = str(directory / "sources.json")
        env["GREMLIN_ROOTDIR"] = str(Path.cwd())
        control = subprocess.run(  # noqa: S603
            [sys.executable, str(directory / "gremlin_bootstrap.py"), *args.tests],
            env=env,
            check=False,
            timeout=180,
        )
        if control.returncode:
            raise pytest.UsageError(
                "Instrumented baseline failed; mutation results would be invalid"
            )
        return directory

    with (
        patch.object(plugin, "_get_bootstrap_script", return_value=bootstrap),
        patch.object(plugin, "_get_lightweight_runner_script", return_value=bootstrap),
        patch.object(plugin, "_write_instrumented_sources", side_effect=write_and_check),
    ):
        result = pytest.main(
            [
                "-q",
                *args.tests,
                "-m",
                "not mutation_infrastructure",
                "--gremlins",
                "--gremlin-executor=subprocess",
                "--gremlin-no-coverage-filter",
                f"--gremlin-workers={args.workers}",
                f"--gremlin-targets={args.targets}",
                "--gremlin-report=json,html",
                f"--gremlins-html-dir={report_dir}",
            ]
        )

    if result == 0:
        (report_dir / "run.json").write_text(
            json.dumps(
                {
                    "pytest_gremlins": VERSION,
                    "runner": "trailmark-full-pytest-v1",
                    "sources": hashes,
                    "tests": args.tests,
                },
                indent=2,
            )
            + "\n"
        )
    return result


if __name__ == "__main__":
    raise SystemExit(main())
