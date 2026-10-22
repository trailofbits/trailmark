"""Regression tests that keep README.md and docs/ in sync with the code.

Three kinds of checks live here:

- Inventories compare what the code exposes (CLI flags, QueryEngine methods,
  enum values, languages) with what the guides document.
- Executable examples run snippets taken from the docs and compare the
  results with what the surrounding prose claims.
- Pinned contracts tie specific statements of behavior to the guide that
  documents them, so moving or deleting a caveat is a deliberate change.

Text comparisons collapse whitespace, so rewrapping a paragraph never breaks
a test.
"""

from __future__ import annotations

import argparse
import inspect
import json
import re
import tomllib
from enum import Enum
from functools import cache
from pathlib import Path
from typing import cast

import pytest

import trailmark
from trailmark.cli import build_parser
from trailmark.models.annotations import AnnotationKind, AssetValue, EntrypointKind, TrustLevel
from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.nodes import NodeKind, NodeOrigin
from trailmark.parse import _LANGUAGE_EXTENSIONS, parse_directory, parse_file, supported_languages
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
DOCS = REPO_ROOT / "docs"
REPO_URL_PREFIX = "https://github.com/trailofbits/trailmark/blob/main/"
LINKED_FILES = (
    "README.md",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    *sorted(f"docs/{path.name}" for path in DOCS.glob("*.md")),
)


@cache
def _raw(relative: str) -> str:
    return (REPO_ROOT / relative).read_text()


@cache
def _flat(relative: str) -> str:
    """Return a document with every whitespace run collapsed to one space."""
    return " ".join(_raw(relative).split())


def _flatten(text: str) -> str:
    return " ".join(text.split())


def _code_block_after(relative: str, marker: str, language: str) -> str:
    """Return the first fenced code block of ``language`` that follows ``marker``."""
    text = _raw(relative)
    start = text.index(marker)
    match = re.compile(rf"```{language}\n(.*?)```", re.DOTALL).search(text, start)
    assert match, f"No ```{language} block after {marker!r} in {relative}"
    return match.group(1)


@pytest.fixture(scope="module")
def pyproject_data() -> dict[str, object]:
    return tomllib.loads(PYPROJECT.read_text())


# ── Pinned contracts ──────────────────────────────────────────────────

PINNED_CONTRACTS: tuple[tuple[str, str], ...] = (
    ("docs/entrypoint-patterns.md", "`pub proc foo` declarations"),
    (
        "docs/skills-compatibility.md",
        "The release tag, pyproject version, runtime version, "
        "changelog heading and wheel metadata must agree.",
    ),
    ("docs/parser-coverage.md", "Erlang callable IDs include arity: `module:function/2`."),
    ("docs/parser-coverage.md", "singleton methods use `file:Class.self.method`."),
    ("docs/parser-coverage.md", "Trait bodies and inherited receivers are not yet extracted"),
    ("docs/parser-coverage.md", "Logical rules have potential `calls` edges to their clauses"),
    ("docs/parser-coverage.md", "Constructor patterns do not create calls."),
    ("docs/parser-coverage.md", "`parse_incomplete` and `syntax_recovered` attributes"),
    ("docs/parser-coverage.md", "`macro_wrapper`, `source_alias`, and `expansion_alias`"),
    ("docs/parser-coverage.md", "file-local `static const` objects"),
    ("docs/installation.md", "Move, and Verus ship as C sources"),
    # Installation and bundled grammars
    ("README.md", "CI tests Python 3.12 through 3.15."),
    ("CONTRIBUTING.md", "CI tests Python 3.12 through 3.15."),
    ("docs/installation.md", "CI tests Python 3.12 through 3.15."),
    ("docs/installation.md", "`tree-sitter-language-pack` 1.22.1 or newer"),
    ("docs/installation.md", "Root-owned grammar caches are supported"),
    ("docs/installation.md", "not group- or other-writable"),
    ("docs/installation.md", "C compiler (`cc`)"),
    ("docs/installation.md", "Python development headers"),
    ("docs/installation.md", "write access"),
    ("docs/installation.md", "continues parsing ordinary Rust"),
    ("docs/installation.md", "compiled source contents, Python ABI"),
    ("docs/installation.md", "Tree-sitter CLI 0.27.1"),
    ("docs/installation.md", "run `npm ci` with npm install scripts enabled"),
    ("docs/installation.md", "generated parser and headers are gitignored"),
    ("docs/installation.md", "installing published packages needs neither"),
    # Mutation testing
    ("CONTRIBUTING.md", "tests.run_mutations --reviewed --workers=4"),
    ("CONTRIBUTING.md", "tests.run_mutations --workers=4"),
    ("CONTRIBUTING.md", "tests.mutation_gate"),
    ("CONTRIBUTING.md", "pushes to main"),
    ("CONTRIBUTING.md", "manual trigger"),
    ("CONTRIBUTING.md", "component unit tests run first"),
    ("CONTRIBUTING.md", "Shared-helper mutants retain tests from"),
    ("CONTRIBUTING.md", "`--workers=4` already enables parallel"),
    # CLI output streams
    ("docs/cli.md", "`trailmark augment --json`"),
    ("docs/cli.md", "JSON to stdout"),
    ("docs/cli.md", "import summaries to stderr"),
    ("README.md", "zero matching source files"),
    ("docs/cli.md", "--allow-empty"),
    ("docs/cli.md", "Both sides must inspect source files"),
    ("docs/python-api.md", "allow_empty=True"),
    ("docs/python-api.md", "CodeGraph.source_file_counts"),
    ("docs/python-api.md", "CodeGraph.parse_diagnostics"),
    ("docs/cli.md", "--strict"),
    ("docs/augmenting.md", "originalUriBaseIds"),
    ("docs/languages.md", "Thrift IDs use source-file identity"),
    ("docs/entrypoints.md", "run_ticktock"),
    ("docs/python-api.md", "parameter_types"),
    ("docs/python-api.md", "parameter_defaults"),
    ("docs/python-api.md", "source_coverage"),
    ("docs/python-api.md", "removal of the old edge and addition of the new"),
    ("docs/python-api.md", "not semantic equivalence"),
    ("README.md", "trailmark analyze --summary --json"),
    ("docs/cli.md", "same object as `QueryEngine.summary()`"),
    ("docs/cli.md", "cannot be combined with `--complexity`"),
    ("docs/python-api.md", "`trailmark_version`"),
    ("docs/python-api.md", "`schema_version` (currently `1`)"),
    ("docs/python-api.md", "Unversioned exports from older releases"),
    ("docs/python-api.md", "does not version parser behavior"),
    ("docs/languages.md", "Sourcing or `eval` disables local inference"),
    ("docs/languages.md", "Function redirects and non-brace function bodies"),
    # Rust and Verus
    ("README.md", "`verus!` blocks"),
    ("docs/languages.md", "brace-delimited blocks"),
    ("docs/languages.md", "pub fn` alone is not an external entrypoint"),
    ("docs/languages.md", "Comptime execution, generated/anonymous types"),
    ("docs/languages.md", "file.inner:function"),
    ("docs/languages.md", "vstd::prelude::verus!"),
    ("docs/languages.md", "file:outer.inner"),
    ("docs/languages.md", "file:outer.Guard.drop"),
    ("docs/languages.md", "file:outer.local.helper"),
    ("docs/languages.md", "Bare calls search function and module"),
    ("docs/languages.md", "`Type::method`"),
    ("docs/languages.md", "including `self.method()`, remain unresolved"),
    ("docs/languages.md", "ordinary Rust as well as"),
    ("docs/languages.md", "removed and added"),
    ("docs/languages.md", "use super::*"),
    ("docs/languages.md", "opaque macros or function bodies"),
    ("docs/languages.md", "file:<chunk>"),
    ("docs/languages.md", "A shadowed `require` does not create an import"),
    ("docs/languages.md", "syntax errors produce a warning"),
    # Solidity persistent state
    ("docs/languages.md", "state_variable"),
    ("docs/languages.md", "file:payments.spend"),
    ("docs/languages.md", "Imported/aliased calls and higher-order dispatch stay uncertain"),
    ("docs/languages.md", "An absent edge is not proof"),
    ("docs/languages.md", "storage aliases"),
    ("docs/languages.md", "Other language parsers do not yet emit state-access edges"),
    # Julia dispatch and macro limits
    ("docs/call-resolution.md", "Multiple dispatch is not inferred"),
    ("docs/call-resolution.md", "Anonymous functions and `do` blocks own their calls"),
    # Query semantics
    ("docs/python-api.md", "Path queries return distinct node-ID sequences"),
    ("docs/python-api.md", "exact node ID first"),
    ("docs/python-api.md", "before applying those tiers to"),
    ("docs/python-api.md", "`:name` suffix, then `.name` suffix"),
    ("docs/python-api.md", "maximum edge count (default 20)"),
    ("docs/python-api.md", "max_depth=20"),
    ("docs/python-api.md", "first one parsed wins"),
    ("docs/python-api.md", "retains the supplied mutable graph"),
    ("docs/python-api.md", "`rebuild_index()` after direct structural edits"),
    (
        "docs/python-api.md",
        "JSON export omits annotations, entrypoint tags, and call-site locations",
    ),
    ("docs/python-api.md", "from trailmark.parse import parse_directory, parse_file"),
    ("docs/python-api.md", 'graph = parse_file("path/to/file.py")'),
    ("docs/languages.md", "Unique lexical function names resolve case-insensitively"),
    # Finding imports
    ("docs/augmenting.md", "`replace_existing=False`"),
    ("docs/augmenting.md", "preserves the graph's previous state"),
    ("docs/augmenting.md", "not data flow"),
    ("docs/languages.md", "Prototypes are retained as `perl_prototype` metadata"),
    # Call resolution
    ("docs/call-resolution.md", "--resolution"),
    ("docs/call-resolution.md", "receiver types"),
    ("docs/call-resolution.md", "outside its counts"),
    ("docs/call-resolution.md", "Zero unresolved calls is not proof"),
    ("docs/languages.md", "A dynamic `character.only=TRUE` package argument"),
    # Graph model and directory parsing
    ("docs/graph-model.md", "@line_column"),
    ("docs/graph-model.md", "Each physical header is parsed once"),
    ("docs/graph-model.md", "Registered file-extension matching is case-insensitive"),
    ("docs/graph-model.md", "pkg.__init__\\.py"),
    ("docs/graph-model.md", "do not traverse directory symlinks"),
    ("docs/graph-model.md", "Directory parsing resolves bare cross-file calls"),
    ("docs/graph-model.md", "Ambiguous cross-file calls stay unresolved"),
    ("docs/graph-model.md", "no built-in pass emits it"),
    ("docs/graph-model.md", "rather than filtering on `uncertain`"),
    ("docs/python-api.md", "changing them does not change the graph"),
    ("docs/augmenting.md", "keeps both copies"),
    ("docs/links.md", "recorded but not traversed"),
    ("docs/languages.md", "Instance Main methods are excluded"),
    ("docs/entrypoints.md", "explicit Module/Shared Main methods"),
    # Configuration files
    ("docs/links.md", "target_external = true"),
    ("docs/links.md", "source_external = true"),
    ("docs/entrypoints.md", "Invalid entries are skipped silently"),
)


@pytest.mark.parametrize(("relative", "phrase"), PINNED_CONTRACTS)
def test_pinned_contract_is_documented(relative: str, phrase: str) -> None:
    assert _flatten(phrase) in _flat(relative), f"{relative} no longer says: {phrase!r}"


def test_links_toml_uses_per_endpoint_external_flags() -> None:
    """Docs describe per-endpoint external flags, never an entry-level `external`."""
    for relative in LINKED_FILES:
        assert re.search(r"(?<!source_)(?<!target_)external = true", _raw(relative)) is None


# ── Installation and package metadata ─────────────────────────────────


def test_language_pack_floor_matches_pyproject(pyproject_data: dict[str, object]) -> None:
    project = cast("dict[str, object]", pyproject_data["project"])
    dependencies = cast("list[str]", project["dependencies"])
    assert "tree-sitter-language-pack>=1.22.1,<2.0" in dependencies


@pytest.mark.parametrize("relative", ["README.md", "docs/installation.md", "CONTRIBUTING.md"])
def test_python_version_matches_pyproject(
    relative: str,
    pyproject_data: dict[str, object],
) -> None:
    """Every stated Python version requirement must match pyproject.toml."""
    match = re.match(r">=\s*(\d+\.\d+)", _read_requires_python(pyproject_data))
    assert match, "Unexpected requires-python format"
    found = re.findall(r"Python\s*(?:&ge;|>=)\s*(\d+\.\d+)", _raw(relative))
    assert found, f"{relative} does not state a Python version requirement"
    assert set(found) == {match.group(1)}


def test_dunder_version_matches_pyproject(pyproject_data: dict[str, object]) -> None:
    project = cast("dict[str, object]", pyproject_data["project"])
    assert trailmark.__version__ == project["version"]


def test_sdist_ships_docs(pyproject_data: dict[str, object]) -> None:
    """The sdist contains these tests, so it must contain the files they read."""
    table = pyproject_data
    for key in ("tool", "hatch", "build", "targets", "sdist"):
        table = cast("dict[str, object]", table[key])
    included = cast("list[str]", table["only-include"])
    assert set(LINKED_FILES) <= set(included)
    assert "docs" not in included


# ── CLI ────────────────────────────────────────────────────────────────


def _subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:  # noqa: SLF001
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return cast("dict[str, argparse.ArgumentParser]", action.choices)
    msg = "No subparsers action found on CLI parser"
    raise AssertionError(msg)


def _find_option(parser: argparse.ArgumentParser, flag: str) -> argparse.Action:
    for action in parser._actions:  # noqa: SLF001
        if flag in action.option_strings:
            return action
    msg = f"Option `{flag}` not registered on parser"
    raise AssertionError(msg)


def test_every_subcommand_is_documented() -> None:
    for command in _subparsers(build_parser()):
        assert f"## `trailmark {command}`" in _raw("docs/cli.md"), f"`{command}` lacks a section"


def test_every_cli_option_is_documented() -> None:
    cli_doc = _raw("docs/cli.md")
    for command, parser in _subparsers(build_parser()).items():
        for action in parser._actions:  # noqa: SLF001
            for flag in action.option_strings:
                if flag in {"-h", "--help"}:
                    continue
                assert f"`{flag}" in cli_doc or f" {flag} " in cli_doc, (
                    f"`trailmark {command} {flag}` is not documented in docs/cli.md"
                )


def test_analyze_defaults_match_docs() -> None:
    analyze = _subparsers(build_parser())["analyze"]
    assert _find_option(analyze, "--language").default == "python"
    complexity = _find_option(analyze, "--complexity")
    assert complexity.default is None
    assert "-c" in complexity.option_strings
    assert "The default is `python`" in _flat("docs/cli.md")


# ── Python API ─────────────────────────────────────────────────────────


def _public_query_methods() -> list[str]:
    return [
        name
        for name, member in inspect.getmembers(QueryEngine, predicate=callable)
        if not name.startswith("_")
    ]


def test_every_public_query_method_is_documented() -> None:
    api_doc = _raw("docs/python-api.md")
    for name in _public_query_methods():
        assert f"{name}(" in api_doc, f"QueryEngine.{name}() is not documented"


def test_documented_methods_exist() -> None:
    """Every method in the python-api.md reference tables exists on QueryEngine."""
    documented = re.findall(r"^\| `([a-z_]+)\(", _raw("docs/python-api.md"), re.MULTILINE)
    assert documented, "No method rows found in docs/python-api.md"
    missing = sorted(set(documented) - set(_public_query_methods()))
    assert not missing, f"docs/python-api.md documents missing methods: {missing}"


@pytest.mark.parametrize(
    ("method", "documented_signature"),
    re.findall(r"^\| `([a-z_]+)(\([^`]*\))` \|", _raw("docs/python-api.md"), re.MULTILINE),
)
def test_documented_signatures_match(
    method: str,
    documented_signature: str,
) -> None:
    """Compare each complete row, including argument names and keyword-only markers."""
    signature = inspect.signature(getattr(QueryEngine, method))
    parameters = [
        parameter.replace(annotation=inspect.Parameter.empty)
        for name, parameter in signature.parameters.items()
        if name != "self"
    ]
    rendered = str(
        signature.replace(parameters=parameters, return_annotation=inspect.Signature.empty)
    ).replace("'", '"')
    assert documented_signature == rendered, f"{method}() row should show {method}{rendered}"


# ── Enums ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("relative", "enum"),
    [
        ("docs/graph-model.md", NodeKind),
        ("docs/graph-model.md", NodeOrigin),
        ("docs/graph-model.md", EdgeKind),
        ("docs/graph-model.md", EdgeConfidence),
        ("docs/augmenting.md", AnnotationKind),
        ("docs/python-api.md", AnnotationKind),
        ("docs/entrypoints.md", EntrypointKind),
        ("docs/entrypoints.md", TrustLevel),
        ("docs/entrypoints.md", AssetValue),
    ],
)
def test_every_enum_value_is_documented(relative: str, enum: type[Enum]) -> None:
    text = _raw(relative)
    missing = [member.value for member in enum if f"`{member.value}`" not in text]
    assert not missing, f"{relative} does not document {enum.__name__} values {missing}"


# ── Supported languages ───────────────────────────────────────────────

_HEADER_EXTENSIONS = {"c": {".h"}, "objc": {".h"}}


def _language_rows() -> list[list[str]]:
    section = re.search(r"^## Supported languages\n(.*?)(?=^#)", _raw("README.md"), re.S | re.M)
    assert section, "Could not find '## Supported languages' in README.md"
    rows = []
    for line in section.group(1).splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 4 and cells[0] not in {"Language", "---"}:
            rows.append(cells)
    assert rows, "No rows parsed from the Supported languages table"
    return rows


def test_language_keys_match_registry() -> None:
    keys = [row[2].strip("`") for row in _language_rows()]
    assert len(keys) == len(set(keys)), "Duplicate --language keys in README table"
    assert set(keys) == set(supported_languages())


def test_language_extensions_match_registry() -> None:
    for _name, extensions, key, _extracts in _language_rows():
        documented = {item.strip().strip("`") for item in extensions.split(",")}
        language = key.strip("`")
        expected = set(_LANGUAGE_EXTENSIONS[language]) | _HEADER_EXTENSIONS.get(language, set())
        assert documented == expected, f"README extensions for {language} drifted"


def test_language_overview_links_to_registry_checked_inventory() -> None:
    assert (
        "[supported languages](https://github.com/trailofbits/trailmark#supported-languages)"
        in _raw("README.md")
    )


# ── Executable examples ───────────────────────────────────────────────


def test_module_ids_depend_on_parse_scope(tmp_path: Path) -> None:
    source = tmp_path / "src" / "auth" / "token.py"
    source.parent.mkdir(parents=True)
    source.write_text("def verify(): pass\n")
    assert "token:verify" in parse_file(str(source)).nodes
    assert "src.auth.token:verify" in parse_directory(str(tmp_path)).nodes
    init = source.parent / "__init__.py"
    init.write_text("def initialize(): pass\n")
    assert "auth:initialize" in parse_file(str(init)).nodes
    guide = _flat("docs/graph-model.md")
    assert "**Directory module IDs** are root-relative dotted paths" in guide
    assert "Single-file parsing uses the file stem instead" in guide
    assert "`__init__.py` uses its parent directory's name" in guide


@pytest.mark.parametrize(
    ("language", "suffix", "source", "confidence"),
    [
        ("python", "py", "def f():\n    auth.verify()\n", EdgeConfidence.INFERRED),
        ("rego", "rego", "package p\nallow { coin.transfer(1) }\n", EdgeConfidence.UNCERTAIN),
        ("sway", "sw", "script; fn f() { coin::transfer(); }", EdgeConfidence.UNCERTAIN),
        (
            "tact",
            "tact",
            "contract C { fun f(other: C) { other.transfer(); } }",
            EdgeConfidence.UNCERTAIN,
        ),
    ],
)
def test_unresolved_confidence_varies_by_frontend(
    tmp_path: Path, language: str, suffix: str, source: str, confidence: EdgeConfidence
) -> None:
    path = tmp_path / f"sample.{suffix}"
    path.write_text(source)
    calls = parse_file(str(path), language=language).unresolved_calls()
    assert len(calls) == 1
    assert calls[0].confidence == confidence
    guide = _flat("docs/graph-model.md")
    assert "In the README's Python example" in guide
    assert "Confidence varies by frontend" in guide


def test_readme_example_graph(tmp_path: Path) -> None:
    """The README example's graph, proxy, and prose claims match real output."""
    (tmp_path / "app.py").write_text(_code_block_after("README.md", "Given `app.py`", "python"))
    engine = QueryEngine.from_directory(str(tmp_path))
    graph = json.loads(engine.to_json())
    mermaid = _code_block_after("README.md", "Trailmark produces this graph", "mermaid")
    for label in re.findall(r'\["([^"]+)"\]', mermaid):
        assert label in graph["nodes"], f"README diagram shows {label}, which is not a node"
    calls = {(e["source"], e["target"]) for e in graph["edges"] if e["kind"] == "calls"}
    assert ("app:handle_request", "proxy.unresolved:auth.verify") in calls
    assert ("app:Auth.verify", "app:Auth._check_sig") in calls
    assert [n["id"] for n in engine.ancestors_of("Auth._check_sig")] == ["app:Auth.verify"]
    unresolved = engine.unresolved_calls("handle_request")
    assert [row["target"] for row in unresolved] == ["proxy.unresolved:auth.verify"]


def test_call_resolution_example(tmp_path: Path) -> None:
    """docs/call-resolution.md shows the exact report for its example file."""
    source = _code_block_after("docs/call-resolution.md", "For this file", "rust")
    (tmp_path / "lib.rs").write_text(source)
    engine = QueryEngine.from_directory(str(tmp_path), language="rust")
    documented = json.loads(_code_block_after("docs/call-resolution.md", "the report is", "json"))
    assert engine.call_resolution() == documented


def test_solidity_state_example(tmp_path: Path) -> None:
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
    assert "token:Token.balances" in _raw("docs/languages.md")


def test_call_sites_are_distinguished_from_targets(tmp_path: Path) -> None:
    (tmp_path / "app.rs").write_text("fn handle() { value.unwrap(); value.unwrap(); }")
    engine = QueryEngine.from_directory(str(tmp_path), language="rust")
    report = engine.call_resolution()
    assert report["unresolved_call_sites"] == 2
    assert report["unresolved_targets"] == 1
    assert len(engine.unresolved_calls("handle")) == 2


# ── Links ──────────────────────────────────────────────────────────────

_FENCE = re.compile(r"^```.*?^```", re.DOTALL | re.MULTILINE)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*(?:\s#+)?$", re.MULTILINE)


def _github_slug(heading: str) -> str:
    """Approximate GitHub's heading anchor algorithm for ASCII headings."""
    text = re.sub(r"[^\w\- ]", "", heading.lower())
    return text.replace(" ", "-")


@cache
def _anchors(relative: str) -> frozenset[str]:
    seen: dict[str, int] = {}
    anchors: set[str] = set()
    for heading in _HEADING.findall(_FENCE.sub("", _raw(relative))):
        slug = _github_slug(heading)
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        anchors.add(slug if count == 0 else f"{slug}-{count}")
    return frozenset(anchors)


def _prose(relative: str) -> str:
    """Return a document without fenced or inline code, where links are literal."""
    return _INLINE_CODE.sub("", _FENCE.sub("", _raw(relative)))


def _link_target(relative: str, url: str) -> tuple[str, str] | None:
    """Map a link to (repo-relative file, anchor), or None for external links."""
    if url.startswith(REPO_URL_PREFIX):
        url = url.removeprefix(REPO_URL_PREFIX)
        base = REPO_ROOT
    elif re.match(r"[a-z]+:", url):
        return None
    else:
        base = (REPO_ROOT / relative).parent
    path, _, anchor = url.partition("#")
    target = (base / path).resolve() if path else (REPO_ROOT / relative)
    return target.relative_to(REPO_ROOT).as_posix(), anchor


@pytest.mark.parametrize("relative", LINKED_FILES)
def test_internal_links_resolve(relative: str) -> None:
    for url in _LINK.findall(_prose(relative)):
        resolved = _link_target(relative, url)
        if resolved is None:
            continue
        target, anchor = resolved
        assert (REPO_ROOT / target).exists(), f"{relative} links to missing {url}"
        if anchor and target.endswith(".md"):
            assert anchor in _anchors(target), f"{relative} links to missing anchor {url}"


def test_readme_links_work_on_pypi() -> None:
    """PyPI renders README.md without the repository, so links must be absolute."""
    for url in _LINK.findall(_prose("README.md")):
        assert re.match(r"[a-z]+:", url), f"README.md link {url} breaks on PyPI"


def _read_requires_python(pyproject_data: dict[str, object]) -> str:
    project = cast("dict[str, object]", pyproject_data["project"])
    value = project.get("requires-python")
    assert isinstance(value, str), "requires-python is missing or not a string"
    return value


@pytest.mark.parametrize(
    "marker",
    [
        "`file:factory.hidden`",
        "`binding_blocked`",
        "TSX grammar",
        "`unsafe(no_mangle)`",
        "source-offset",
        "`$argN`",
        "lexical package",
        "`_ENV`",
        "quoting constructors",
        "relation/routine namespaces",
        "`abi(embed_v0)`",
        "property tests, benchmarks",
        "destructured arguments",
        "soft casts",
        "labeled switch",
        "`procref`",
        "`pub proc`",
        "Bash 5.3",
        "`coproc`",
        "`method_id`",
        "`#declaration`",
    ],
)
def test_parser_coverage_contracts(marker: str) -> None:
    assert marker in _flat("docs/parser-coverage.md")
