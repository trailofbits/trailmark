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

import trailmark
from trailmark.cli import build_parser
from trailmark.parse import supported_languages
from trailmark.parsers.javascript.parser import _EXTENSIONS as JS_EXTENSIONS
from trailmark.query.api import QueryEngine


def _find_repo_root() -> Path:
    """Locate the repo root by walking up until we find README.md + pyproject.toml.

    Supports running documentation checks from isolated source checkouts.
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
    def test_read_only_grammar_cache_support_is_documented(
        self,
        readme_text: str,
        pyproject_data: dict[str, object],
    ) -> None:
        project = cast("dict[str, object]", pyproject_data["project"])
        dependencies = cast("list[str]", project["dependencies"])
        assert "tree-sitter-language-pack>=1.22.1,<2.0" in dependencies
        assert "`tree-sitter-language-pack` 1.22.1 or newer" in readme_text
        assert "Root-owned grammar caches are supported" in readme_text
        assert "not group- or other-writable" in readme_text

    def test_bundled_grammar_build_requirements_are_documented(self, readme_text: str) -> None:
        assert "C compiler (`cc`)" in readme_text
        assert "Python development headers" in readme_text
        assert "write access" in readme_text
        assert "continues parsing ordinary Rust" in readme_text

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


class TestDevelopment:
    def test_mutation_workflow_scope_and_test_order_are_documented(self, readme_text: str) -> None:
        assert "tests.run_mutations --reviewed --workers=4" in readme_text
        assert "tests.run_mutations --workers=4" in readme_text
        assert "tests.mutation_gate" in readme_text
        assert "pushes to main" in readme_text and "manual trigger" in readme_text
        assert "component unit tests run first" in readme_text
        assert "Shared-helper mutants retain tests from" in readme_text
        assert "`--workers=4` already enables parallel" in readme_text


class TestPackageMetadata:
    def test_dunder_version_matches_pyproject(
        self,
        pyproject_data: dict[str, object],
    ) -> None:
        """`trailmark.__version__` should match the packaged version."""
        project_raw = pyproject_data.get("project")
        assert isinstance(project_raw, dict), "pyproject.toml has no [project] table"
        project = cast("dict[str, object]", project_raw)
        version = project.get("version")
        assert isinstance(version, str), "project.version is missing or not a string"
        assert trailmark.__version__ == version


class TestUsageSection:
    def test_augment_json_output_streams_are_documented(self, readme_text: str) -> None:
        assert "`trailmark augment --json`" in readme_text
        assert "JSON to stdout" in readme_text
        assert "import summaries to stderr" in readme_text

    @pytest.fixture(scope="class")
    @classmethod
    def cli_subcommands(cls) -> set[str]:
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
        registered = {_normalize(key) for key in supported_languages()}
        for lang in readme_languages:
            assert _normalize(lang) in registered, (
                f"README lists `{lang}` but no parser is registered"
            )

    def test_every_registered_parser_is_documented(self, readme_text: str) -> None:
        """Every registered parser should appear in the README table."""
        readme_languages = {_normalize(x) for x in _extract_languages_from_readme(readme_text)}
        for key in supported_languages():
            assert _normalize(key) in readme_languages, (
                f"Parser `{key}` is registered but not documented in README"
            )

    def test_javascript_extensions_match_parser(self, readme_text: str) -> None:
        """README's JavaScript extension list should match the parser."""
        extensions = _extract_extensions_for_language(readme_text, "JavaScript")
        assert set(extensions) == set(JS_EXTENSIONS)

    def test_verus_support_is_documented(self, readme_text: str) -> None:
        """README should advertise the Rust parser's Verus support."""
        assert "`verus!` blocks" in readme_text
        assert "brace-delimited blocks" in readme_text
        assert "file.inner:function" in readme_text
        assert "vstd::prelude::verus!" in readme_text
        assert "file:outer.inner" in readme_text
        assert "file:outer.Guard.drop" in readme_text
        assert "file:outer.local.helper" in readme_text
        assert "Bare calls search function and module" in readme_text
        assert "`Type::method`" in readme_text
        assert "including `self.method()`, remain unresolved" in readme_text
        assert "ordinary Rust as well as" in readme_text
        assert "removed and added" in readme_text
        assert "use super::*" in readme_text
        assert "opaque macros or function bodies" in readme_text
        assert "syntax errors produce a warning" in readme_text
        assert "compiled source contents, Python ABI" in readme_text
        assert "Tree-sitter CLI 0.27.1" in readme_text
        assert "run `npm ci` with npm install scripts enabled" in readme_text
        assert "generated parser and headers are gitignored" in readme_text
        assert "installing published packages needs neither" in readme_text


class TestPersistentState:
    def test_solidity_state_example(self, readme_text: str, tmp_path: Path) -> None:
        assert "state_variable" in readme_text
        assert "An absent edge is not proof" in readme_text
        assert "storage aliases" in readme_text
        assert "Other\nlanguage parsers do not yet emit state-access edges" in readme_text
        (tmp_path / "token.sol").write_text(
            "contract Token { mapping(address => uint) balances;"
            "function transfer(address to, uint amount) public { balances[to] += amount; } }"
        )
        engine = QueryEngine.from_directory(str(tmp_path), language="solidity")
        for query in (engine.state_read_by, engine.state_written_by):
            assert [node["id"] for node in query("Token.transfer")] == ["token:Token.balances"]
        for query in (engine.readers_of, engine.writers_of):
            assert [node["id"] for node in query("Token.balances")] == ["token:Token.transfer"]
        assert engine.callees_of("Token.transfer") == []


class TestQueryEngineAPI:
    @pytest.mark.parametrize("method", ["augment_sarif", "augment_weaudit"])
    def test_import_replacement_contract_is_documented(self, method: str, readme_text: str) -> None:
        parameter = inspect.signature(getattr(QueryEngine, method)).parameters["replace_existing"]
        assert parameter.default is True
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert "`replace_existing=False`" in readme_text
        assert "preserves the graph's previous state" in readme_text

    def test_path_and_lookup_contracts_are_documented(self, readme_text: str) -> None:
        assert "Path queries return distinct node-ID sequences" in readme_text
        assert "exact node ID first" in readme_text
        assert "before applying those tiers to" in readme_text
        assert "`:name` suffix, then `.name` suffix" in readme_text
        assert "maximum edge count (default 20)" in readme_text
        assert "max_depth=20" in readme_text
        depth = inspect.signature(QueryEngine.paths_between).parameters["max_depth"]
        assert depth.kind is inspect.Parameter.KEYWORD_ONLY
        assert depth.default == 20

    def test_graph_ownership_and_export_limits_are_documented(self, readme_text: str) -> None:
        assert "retains the supplied mutable graph" in readme_text
        assert "`rebuild_index()` after direct structural edits" in readme_text
        assert (
            "JSON export omits annotations, entrypoint tags, and call-site locations" in readme_text
        )

    EXPECTED_METHODS = (
        "callers_of",
        "callees_of",
        "state_read_by",
        "state_written_by",
        "readers_of",
        "writers_of",
        "ancestors_of",
        "reachable_from",
        "paths_between",
        "connect_subgraphs",
        "entrypoint_paths_to",
        "attack_surface",
        "complexity_hotspots",
        "functions_that_raise",
        "generic_parameters",
        "type_references",
        "annotate",
        "annotations_of",
        "nodes_with_annotation",
        "clear_annotations",
        "diff_against",
        "preanalysis",
        "augment_sarif",
        "augment_weaudit",
        "augment_binary",
        "findings",
        "subgraph",
        "subgraph_edges",
        "subgraph_names",
        "unresolved_calls",
        "call_resolution",
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


class TestCallResolutionDocs:
    def test_documented_diagnostics_distinguish_sites_from_targets(
        self, readme_text: str, tmp_path: Path
    ) -> None:
        assert "--resolution" in readme_text
        assert "receiver types" in readme_text
        assert "outside its counts" in readme_text
        assert "Zero unresolved calls is not proof" in readme_text
        (tmp_path / "app.rs").write_text("fn handle() { value.unwrap(); value.unwrap(); }")
        engine = QueryEngine.from_directory(str(tmp_path), language="rust")
        report = engine.call_resolution()
        assert report["unresolved_call_sites"] == 2
        assert report["unresolved_targets"] == 1
        assert len(engine.unresolved_calls("handle")) == 2


class TestConfigurationDocs:
    def test_links_toml_uses_per_endpoint_external_flags(self, readme_text: str) -> None:
        """README documents per-endpoint external link flags, not entry-level external."""
        assert "target_external = true" in readme_text
        assert "source_external = true" in readme_text
        assert re.search(r"(?<!source_)(?<!target_)external = true", readme_text) is None


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


class TestParseOnlyAPI:
    def test_readme_mentions_parse_only_api(self, readme_text: str) -> None:
        assert "from trailmark.parse import parse_directory, parse_file" in readme_text
        assert 'graph = parse_file("path/to/file.py")' in readme_text

    def test_declaration_and_directory_contracts_are_documented(self, readme_text: str) -> None:
        assert "@line_column" in readme_text
        assert "Each physical header is parsed once" in readme_text
        assert "Registered file-extension matching is case-insensitive" in readme_text
        assert "pkg.__init__\\.py" in readme_text
        assert "do not traverse directory symlinks" in readme_text

    def test_readme_documents_cross_file_call_resolution(self, readme_text: str) -> None:
        assert "Directory parsing resolves bare cross-file calls" in readme_text
        assert "ambiguous cross-file calls" in readme_text


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


def _extract_extensions_for_language(readme_text: str, language: str) -> list[str]:
    """Return the parsed extension list from a README Supported Languages row."""
    section = re.search(
        r"### Supported Languages\s*\n(.*?)(?=\n###|\n## )",
        readme_text,
        re.DOTALL,
    )
    assert section, "Could not find '### Supported Languages' section in README"
    for line in section.group(1).splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 2 or cells[0] != language:
            continue
        return [item.strip().strip("`") for item in cells[1].split(",") if item.strip()]
    raise AssertionError(f"Could not find `{language}` row in Supported Languages table")
