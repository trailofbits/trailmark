"""Regression tests for package dependency metadata."""

from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.requirements import Requirement


def test_tree_sitter_excludes_point_lifetime_regression() -> None:
    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    requirements = [Requirement(value) for value in pyproject["project"]["dependencies"]]
    tree_sitter = next(
        requirement for requirement in requirements if requirement.name == "tree-sitter"
    )
    assert "0.25.2" in tree_sitter.specifier
    assert "0.26.0" not in tree_sitter.specifier


def test_tree_sitter_language_pack_requires_read_only_cache_support() -> None:
    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    dependencies = pyproject["project"]["dependencies"]
    dependency = next(dep for dep in dependencies if dep.startswith("tree-sitter-language-pack"))

    assert ">=1.22.1" in dependency
    assert "<2.0" in dependency
