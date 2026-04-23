"""Regression tests for README.md claims.

These tests codify the findings from a documentation audit so that future
drift between README and implementation is caught in CI rather than by
users. Each test corresponds to a concrete claim the README makes.
"""

from __future__ import annotations

import argparse
import inspect
import re
import tomllib
from pathlib import Path
from typing import cast

import pytest

from trailmark.cli import build_parser
from trailmark.query.api import _PARSER_MAP, QueryEngine


def _find_repo_root() -> Path:
    """Locate the repo root by walking up until we find README.md + pyproject.toml.

    Necessary because mutmut copies the source tree into a `mutants/` subdir
    without the non-Python files, so `parent.parent` resolves to the wrong place.
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "README.md").exists() and (candidate / "pyproject.toml").exists():
            return candidate
    msg = "Could not locate repo root (README.md + pyproject.toml)"
    raise RuntimeError(msg)


REPO_ROOT = _find_repo_root()
README = REPO_ROOT / "README.md"
PYPROJECT = REPO_ROOT / "pyproject.toml"


@pytest.fixture(scope="module")
def readme_text() -> str:
    return README.read_text()


@pytest.fixture(scope="module")
def pyproject_data() -> dict[str, object]:
    return tomllib.loads(PYPROJECT.read_text())


class TestInstallation:
    def test_python_version_matches_pyproject(
        self,
        readme_text: str,
        pyproject_data: dict[str, object],
    ) -> None:
        """README's Python version requirement must match pyproject.toml."""
        pyproject_floor = _read_requires_python(pyproject_data)
        match = re.match(r">=\s*(\d+\.\d+)", pyproject_floor)
        assert match, f"Unexpected requires-python format: {pyproject_floor}"
        expected = match.group(1)

        readme_matches = re.findall(
            r"Python\s*&?ge;?\s*(\d+\.\d+)|Python\s*>=\s*(\d+\.\d+)",
            readme_text,
        )
        versions = [v for pair in readme_matches for v in pair if v]
        assert versions, "README does not state a Python version requirement"
        for v in versions:
            assert v == expected, (
                f"README claims Python >= {v} but pyproject.toml requires {expected}"
            )


class TestUsageSection:
    @pytest.fixture(scope="class")
    def cli_subcommands(self) -> set[str]:
        subparsers_action = _find_subparsers_action(build_parser())
        return set(subparsers_action.choices.keys())

    def test_every_subcommand_is_documented(
        self,
        readme_text: str,
        cli_subcommands: set[str],
    ) -> None:
        """Every subparser the CLI registers must appear in the README."""
        for cmd in cli_subcommands:
            assert f"trailmark {cmd}" in readme_text, (
                f"CLI subcommand `{cmd}` is not documented in README.md"
            )


class TestSupportedLanguages:
    def test_every_readme_language_has_parser(self, readme_text: str) -> None:
        """Every language in the README table must have a parser registered."""
        readme_languages = _extract_languages_from_readme(readme_text)
        registered = {_normalize(key) for key in _PARSER_MAP}
        for lang in readme_languages:
            assert _normalize(lang) in registered, (
                f"README lists `{lang}` but no parser is registered in _PARSER_MAP"
            )

    def test_every_registered_parser_is_documented(self, readme_text: str) -> None:
        """Every parser in _PARSER_MAP should appear in the README table."""
        readme_languages = {_normalize(x) for x in _extract_languages_from_readme(readme_text)}
        for key in _PARSER_MAP:
            assert _normalize(key) in readme_languages, (
                f"Parser `{key}` is registered but not documented in README"
            )


class TestQueryEngineAPI:
    EXPECTED_METHODS = (
        "callers_of",
        "callees_of",
        "ancestors_of",
        "reachable_from",
        "paths_between",
        "entrypoint_paths_to",
        "attack_surface",
        "complexity_hotspots",
        "functions_that_raise",
        "annotate",
        "annotations_of",
        "nodes_with_annotation",
        "clear_annotations",
        "diff_against",
        "summary",
        "to_json",
    )

    def test_all_documented_methods_exist(self) -> None:
        """Every method documented in the README must exist on QueryEngine."""
        for method_name in self.EXPECTED_METHODS:
            assert hasattr(QueryEngine, method_name), (
                f"README documents QueryEngine.{method_name}() but it is missing"
            )
            assert callable(getattr(QueryEngine, method_name)), (
                f"QueryEngine.{method_name} exists but is not callable"
            )

    def test_documented_methods_appear_in_readme(self, readme_text: str) -> None:
        """The README methods table must actually list each expected method."""
        for method_name in self.EXPECTED_METHODS:
            assert method_name in readme_text, (
                f"Expected method `{method_name}` not found in README text"
            )

    def test_annotate_signature_matches_readme(self) -> None:
        """README: annotate(name, kind, desc, source) — positional order must bind."""
        sig = inspect.signature(QueryEngine.annotate)
        params = list(sig.parameters.keys())
        assert params[1:5] == ["name", "kind", "description", "source"], (
            f"annotate signature drifted from README: {params}"
        )

    def test_annotations_of_signature_matches_readme(self) -> None:
        """README: annotations_of(name, kind=None)."""
        sig = inspect.signature(QueryEngine.annotations_of)
        params = sig.parameters
        assert "name" in params
        assert "kind" in params
        assert params["kind"].default is None

    def test_clear_annotations_signature_matches_readme(self) -> None:
        """README: clear_annotations(name, kind=None)."""
        sig = inspect.signature(QueryEngine.clear_annotations)
        params = sig.parameters
        assert "name" in params
        assert "kind" in params
        assert params["kind"].default is None


class TestCLIDefaults:
    def test_analyze_language_default_is_python(self) -> None:
        """README: default language is Python."""
        subparsers_action = _find_subparsers_action(build_parser())
        analyze = subparsers_action.choices["analyze"]
        language_action = _find_option(analyze, "--language")
        assert language_action.default == "python"

    def test_analyze_complexity_is_int_with_default_zero(self) -> None:
        """README: --complexity takes an int threshold; code gates on > 0."""
        subparsers_action = _find_subparsers_action(build_parser())
        analyze = subparsers_action.choices["analyze"]
        action = _find_option(analyze, "--complexity")
        assert action.type is int
        assert action.default == 0

    def test_complexity_short_flag_exists(self) -> None:
        """README example uses --complexity; CLI also advertises -c."""
        subparsers_action = _find_subparsers_action(build_parser())
        analyze = subparsers_action.choices["analyze"]
        action = _find_option(analyze, "--complexity")
        assert "-c" in action.option_strings


def _read_requires_python(pyproject_data: dict[str, object]) -> str:
    project_raw = pyproject_data.get("project")
    assert isinstance(project_raw, dict), "pyproject.toml has no [project] table"
    project = cast("dict[str, object]", project_raw)
    value = project.get("requires-python")
    assert isinstance(value, str), "requires-python is missing or not a string"
    return value


def _find_subparsers_action(
    parser: argparse.ArgumentParser,
) -> argparse._SubParsersAction:  # type: ignore[type-arg]
    for action in parser._actions:  # noqa: SLF001
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return action
    msg = "No subparsers action found on CLI parser"
    raise AssertionError(msg)


def _find_option(parser: argparse.ArgumentParser, flag: str) -> argparse.Action:
    for action in parser._actions:  # noqa: SLF001
        if flag in action.option_strings:
            return action
    msg = f"Option `{flag}` not registered on parser"
    raise AssertionError(msg)


_LANGUAGE_ALIASES = {
    "c++": "cpp",
    "c#": "c_sharp",
    "miden assembly": "masm",
    "objective-c": "objc",
}


def _normalize(name: str) -> str:
    lowered = name.strip().lower()
    return _LANGUAGE_ALIASES.get(lowered, lowered)


def _extract_languages_from_readme(readme_text: str) -> list[str]:
    """Parse the first column of the 'Supported Languages' markdown table."""
    section = re.search(
        r"### Supported Languages\s*\n(.*?)(?=\n###|\n## )",
        readme_text,
        re.DOTALL,
    )
    assert section, "Could not find '### Supported Languages' section in README"
    lines = section.group(1).splitlines()
    langs: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        if set(stripped) <= set("|- "):
            continue
        first_cell = stripped.split("|")[1].strip()
        if first_cell in {"", "Language"}:
            continue
        langs.append(first_cell)
    assert langs, "No language rows parsed from Supported Languages table"
    return langs
