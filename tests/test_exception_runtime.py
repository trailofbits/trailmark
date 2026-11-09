"""Optional, bounded runtime characterization; never a language-wide guarantee."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from trailmark.models import ExceptionEventKind
from trailmark.parse import parse_file

FIXTURE = Path(__file__).parent / "fixtures/exceptions/php_runtime.php"


def test_php_runtime_exposes_implicit_failure_paths_absent_from_direct_throw_metadata() -> None:
    php = shutil.which("php")
    if php is None:
        pytest.skip("PHP 7.4+ is required for the optional interruption characterization")
    version = subprocess.run(  # noqa: S603
        [php, "-n", "-r", "echo PHP_VERSION_ID;"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    if int(version.stdout) < 70400:
        pytest.skip("Throwing from __toString and __unserialize require PHP 7.4+")
    result = subprocess.run(  # noqa: S603
        [php, "-n", str(FIXTURE)],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    observed = json.loads(result.stdout)
    assert observed["state"] == "changed"
    assert observed["events"] == [
        "convert",
        "finally",
        "error-handler",
        "warning-callback",
        "converted-warning",
        "unserialize",
        "initialization-failed",
        "LogicException:RuntimeException",
    ]
    graph = parse_file(str(FIXTURE), "php", strict=True)
    convert = next(unit for unit in graph.nodes.values() if unit.name == "convert")
    # Coercion really throws, but the source model promises only lexical evidence.
    assert convert.exception_types == ()
    assert [event.kind for event in convert.exception_events] == [
        ExceptionEventKind.HANDLER,
        ExceptionEventKind.CLEANUP,
    ]
    assert graph.exception_support()[0].implicit_execution.value == "unsupported"
