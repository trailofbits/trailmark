# Contributing to Trailmark

## Setup

Requires Python >= 3.12, [uv](https://docs.astral.sh/uv/), Node.js/npm,
a C compiler (`cc`), and Python development headers. CI tests Python 3.12–3.14.

```bash
npm ci
uv sync --locked --all-groups
```

`npm ci` installs the pinned Tree-sitter CLI used to generate the bundled Verus
grammar. Its install script must be enabled; if your npm configuration disables
scripts, run `npm rebuild tree-sitter-cli --ignore-scripts=false` before syncing.
See [docs/installation.md](docs/installation.md#from-a-git-checkout) for how
generated grammar sources are handled.

## Running Checks

Run these checks before submitting changes:

```bash
# Lint and check formatting
uv run ruff check hatch_build.py src/ tests/
uv run ruff format --check hatch_build.py src/ tests/

# Type check
uv run --locked ty check

# Tests
uv run pytest -q tests/

# Check source-distribution guide selection and local-note exclusions
uv run --no-project --with hatchling python tests/sdist_smoke.py
```

## Mutation Testing

Trailmark uses a pinned pytest-gremlins runner to verify test suite quality.
Always use the repository runner rather than invoking the plugin directly.

```bash
# Mutate the files under review and gate the results
uv run python -m tests.run_mutations --reviewed --workers=4
uv run python -m tests.mutation_gate

# Full repository campaign
uv run python -m tests.run_mutations --workers=4
```

`--workers=4` already enables parallel execution. Reports are written to
`coverage/gremlins/`.

**Scope and gate.** Pull requests mutate the Rust/Verus files named in
`tests/mutation_baseline.json` and gate new and changed functions there. Full
repository campaigns run on pushes to main and through the mutation workflow's
manual trigger. Unchanged legacy functions are identified by source hashes and
enter the gate automatically when edited. The gate rejects unreviewed
survivors, missing tests or results, errors, and timeouts. An exception
requires a reason and a fingerprint of the function and mutation; changing
that function requires reviewing its exceptions again.

**How the runner works.** The runner pins pytest-gremlins 1.11.4 and uses its
pytest subprocesses and import handling, including fixtures, parametrized tests,
package metadata, and instrumentation in spawned child processes. Trailmark
checks the clean baseline and each distinct instrumented test ordering before
mutating, skips the redundant coverage pre-scan, preserves exact parameter IDs,
and rejects skipped instrumentation. The adapter uses private plugin APIs; its
control tests must pass before upgrading the plugin.

**Test ordering.** Each mutant keeps the full selected test suite: component
unit tests run first, followed by the remaining tests, stopping at the first
failure. Shared-helper mutants retain tests from every language. Tests stay in
their existing files; `tests/mutation_order.py` defines the ordering, and
unknown components keep collection order. Use `--test-order=collection` to
compare against pytest's original order.

**Focusing a campaign.** Pass `--targets` with comma-separated source paths,
followed by the relevant test files.

Snapshot updates (`TRAILMARK_UPDATE_SNAPSHOTS=1`) are disabled during CI and
mutation runs; do not enable them there.

## Architecture

Trailmark has a parse layer followed by a three-layer analysis stack:

0. **Parse API** (`src/trailmark/parse.py`) -- public entry point for
   parsing files or directories into a raw `CodeGraph`. Lazily imports
   the appropriate language parser. Used directly for parse-only
   workflows and internally by `QueryEngine`.
1. **CodeGraph** (`src/trailmark/models/graph.py`) -- mutable data
   container holding nodes, edges, annotations, and entrypoints.
2. **GraphStore** (`src/trailmark/storage/graph_store.py`) -- wraps
   CodeGraph in a rustworkx `PyDiGraph` with bidirectional ID/index
   mappings. Validates node existence before mutations.
3. **QueryEngine** (`src/trailmark/query/api.py`) -- high-level facade
   that resolves names, delegates to GraphStore, and returns plain dicts.

### Conventions

- **Missing-node queries return empty values** where practical. Factories and
  invalid-input paths may raise `ValueError` or exit via the CLI.
- **QueryEngine returns dicts**, GraphStore returns model objects.
- **Helper functions** like `_unit_to_dict()`, `_edge_to_dict()`,
  `_annotation_to_dict()` live at module level alongside their class.
- **Frozen dataclasses** for immutable data (`CodeUnit`, `CodeEdge`,
  `Annotation`). `CodeGraph` is mutable (not frozen).
- **No relative imports.** Use absolute imports from `trailmark.*`.

## Adding a New Language Parser

1. Create `src/trailmark/parsers/<lang>/parser.py` implementing the
   `LanguageParser` protocol from `src/trailmark/parsers/base.py`.
2. Register the parser in `_PARSER_MAP` and its file extensions in
   `_LANGUAGE_EXTENSIONS`, both in `src/trailmark/parse.py`.
3. Add tests in `tests/test_<lang>_parser.py`.
4. Add a row to the language table in `README.md`, including the
   `--language` key.
5. If the language has entrypoint conventions, add a detector in
   `src/trailmark/analysis/entrypoints.py` and a row to the framework table in
   `docs/entrypoints.md`. Note behavior that needs explanation in
   `docs/languages.md`.

## Documentation

`README.md` is the landing page and the PyPI description. Reference material
lives in `docs/`:

| File | Covers |
| --- | --- |
| `docs/cli.md` | Subcommands, flags, output formats |
| `docs/python-api.md` | `QueryEngine` and `trailmark.parse` |
| `docs/graph-model.md` | Node/edge kinds, IDs, proxies, directory parsing, linking |
| `docs/entrypoints.md` | Detection layers and `.trailmark/entrypoints.toml` |
| `docs/links.md` | `.trailmark/links.toml` |
| `docs/augmenting.md` | SARIF, weAudit, binary imports, annotations, preanalysis |
| `docs/call-resolution.md` | Resolution diagnostics and limits |
| `docs/languages.md` | Per-language behavior |
| `docs/installation.md` | Grammars, offline installs, Nix, checkouts |
| `docs/entrypoint-patterns.md` | Patterns for writing new entrypoint detectors |

`tests/test_docs.py` keeps these files in sync with the code. It checks that
every CLI subcommand, public `QueryEngine` method, language, and enum value is
documented, runs the README example, verifies relative links and anchors, and
pins specific statements of behavior to the guide that documents them. When
behavior changes, update the guide and the matching test in the same change.

Links in `README.md` use absolute `https://github.com/trailofbits/trailmark/blob/main/...`
URLs so they work on PyPI; links between files in `docs/` are relative.
