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
```

## Mutation Testing

Trailmark uses a pinned pytest-gremlins runner to verify test suite quality.
Use the repository runner, which validates clean and instrumented baselines
and runs component tests before retaining the remaining selected tests as fallback.

```bash
uv run python -m tests.run_mutations --reviewed --workers=4
uv run python -m tests.mutation_gate
```

Omit `--reviewed` for a full repository campaign. `--workers=4` already enables
parallel execution. Do not invoke the plugin directly or enable
`TRAILMARK_UPDATE_SNAPSHOTS` during CI or mutation testing. See
[the README](README.md#development) for scope, reports, and gate behavior.

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
4. Update the language table in `README.md`.
