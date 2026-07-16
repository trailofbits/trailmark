"""Subprocess regressions for tree-sitter native crashes on larger inputs."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("language", "suffix", "source"),
    [
        ("go", ".go", "package p\n" + "".join(f"func f{i}() {{}}\n" for i in range(1800))),
        ("rust", ".rs", "".join(f"fn f{i}() {{}}\n" for i in range(1800))),
        (
            "typescript",
            ".ts",
            "".join(f"export function f{i}(): number {{ return {i}; }}\n" for i in range(900)),
        ),
        (
            "solidity",
            ".sol",
            "pragma solidity ^0.8.0; contract Large {\n"
            + "".join(
                f"function f{i}() external pure returns(uint) {{ return {i}; }}\n"
                for i in range(500)
            )
            + "}\n",
        ),
    ],
)
def test_large_file_parses_in_subprocess(
    tmp_path: Path,
    language: str,
    suffix: str,
    source: str,
) -> None:
    path = tmp_path / f"large{suffix}"
    path.write_text(source)
    script = (
        "from trailmark.parse import parse_file; "
        f"g=parse_file({str(path)!r}, {language!r}); "
        "assert g.nodes"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-X", "faulthandler", "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
