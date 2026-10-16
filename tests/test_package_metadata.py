"""Regression tests for package dependency metadata."""

from __future__ import annotations

import tomllib
from pathlib import Path


def test_tree_sitter_language_pack_requires_read_only_cache_support() -> None:
    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    dependencies = pyproject["project"]["dependencies"]
    dependency = next(dep for dep in dependencies if dep.startswith("tree-sitter-language-pack"))

    assert ">=1.22.1" in dependency
    assert "<2.0" in dependency
