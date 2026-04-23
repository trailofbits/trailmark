# Agents

Development context for AI agents working on this codebase.

## Project Overview

Trailmark parses source code into directed graphs of functions, classes,
calls, and semantic metadata for security analysis. It supports more than
twenty language frontends via tree-sitter-language-pack plus bundled
custom grammars, and uses rustworkx for graph traversal. Treat
`README.md` as the authoritative public feature list.

## Architecture

```
CodeGraph (data) -> GraphStore (indexed storage) -> QueryEngine (facade)
```

- **CodeGraph** holds raw nodes, edges, annotations, entrypoints. Mutable.
- **GraphStore** wraps CodeGraph in a rustworkx PyDiGraph. Validates
  node existence. Returns model objects.
- **QueryEngine** resolves names to node IDs, delegates to GraphStore,
  and returns JSON-serializable data structures for user-facing APIs.

## Key Conventions

- Query/read methods should return `False`, `[]`, or empty sets for
  missing nodes where practical. Factory methods and invalid-input paths
  may raise `ValueError` or exit via the CLI.
- QueryEngine returns JSON-serializable data structures; GraphStore
  returns model objects and node IDs.
- Node IDs follow `module:function`, `module:Class`, `module:Class.method`.
- Edge confidence: `certain`, `inferred`, `uncertain`.
- Annotation sources: `"llm"`, `"docstring"`, `"manual"`.
- Frozen dataclasses for immutable data. `CodeGraph` is mutable.
- No relative (`..`) imports. Use `from trailmark.*`.
- Update `README.md` and `tests/test_readme.py` when documented behavior
  changes.

## File Layout

```
src/trailmark/
  models/          # Data classes: CodeUnit, CodeEdge, Annotation, CodeGraph
    graph.py       # CodeGraph with add_annotation, clear_annotations, merge
    nodes.py       # CodeUnit, Parameter, TypeRef, BranchInfo
    edges.py       # CodeEdge, EdgeKind, EdgeConfidence
    annotations.py # Annotation, AnnotationKind, EntrypointTag
  parsers/         # Language-specific tree-sitter parsers
    base.py        # BaseParser protocol
    _common.py     # Shared parser utilities
    python/        # One subpackage per language
    javascript/
    ...
  analysis/        # Higher-level passes: entrypoints, diff, augment, preanalysis
  storage/
    graph_store.py # GraphStore: rustworkx-backed indexed storage
  query/
    api.py         # QueryEngine: high-level facade
  cli.py           # CLI entry point
tests/             # pytest test suite
```

## Running Checks

```bash
uv sync --all-groups
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv tool install ty && ty check
uv run pytest -q tests/
```

## Mutation Testing

```bash
uv run mutmut run
uv run mutmut results
```

### macOS Fork Safety

mutmut uses `fork()` which segfaults with rustworkx on macOS. Set:

```bash
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
```

This is not needed on Linux/CI (Ubuntu).

## Adding Features

- Follow the three-layer pattern: add to CodeGraph first, then GraphStore
  (with validation), then QueryEngine (with name resolution and dict
  conversion).
- Analysis passes live under `trailmark.analysis` and should usually
  persist results as annotations or named subgraphs rather than
  bypassing the graph model.
- Add tests at each relevant layer: `test_models.py`, `test_storage.py`,
  `test_query.py`, plus focused parser/analysis/doc regression tests.
- Update `README.md` and the matching regression tests if adding or
  changing user-facing API.
