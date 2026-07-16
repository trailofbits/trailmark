# Contributing to Trailmark

## Setup

Requires Python >= 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-groups
```

## Running Checks

Run all three before submitting changes:

```bash
# Lint and format
uv run ruff check --fix src/ tests/
uv run ruff format src/ tests/

# Type check
uv run ty check

# Tests
pytest -q
```

## Mutation Testing

Trailmark uses [mutmut](https://mutmut.readthedocs.io/) to verify test
suite quality. Mutmut generates source code mutations and confirms that
tests catch each one.

```bash
uv run mutmut run
uv run mutmut results
```

### macOS: Fork Safety

mutmut uses `fork()` to isolate mutation runs. On macOS, this conflicts
with the Objective-C runtime and with native extensions like rustworkx
(a Rust/C extension). You **must** set this environment variable:

```bash
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
```

Without it, mutmut will segfault on every mutant. This is not needed on
Linux (CI runs on Ubuntu without issue).

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

- **No exceptions for missing nodes.** All methods return `False` or `[]`
  when a node is not found.
- **QueryEngine returns dicts**, GraphStore returns model objects.
- **Helper functions** like `_unit_to_dict()`, `_edge_to_dict()`,
  `_annotation_to_dict()` live at module level alongside their class.
- **Frozen dataclasses** for immutable data (`CodeUnit`, `CodeEdge`,
  `Annotation`). `CodeGraph` is mutable (not frozen).
- **No relative imports.** Use absolute imports from `trailmark.*`.

## Adding a New Language Parser

1. Create `src/trailmark/parsers/<lang>/parser.py` implementing the
   `BaseParser` protocol from `src/trailmark/parsers/base.py`.
2. Register the parser in `_PARSER_MAP` and its file extensions in
   `_LANGUAGE_EXTENSIONS`, both in `src/trailmark/parse.py`.
3. Add tests in `tests/test_<lang>_parser.py`.
4. Update the language table in `README.md`.
