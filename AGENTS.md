# Agents

Development context for AI agents working on this codebase.

## Project Overview

Trailmark parses source code into directed graphs of functions, classes,
calls, and semantic metadata for security analysis. It supports more than
twenty language frontends via tree-sitter-language-pack plus bundled
custom grammars, and uses rustworkx for graph traversal. Treat
`README.md` as the authoritative public feature list.

## Agent Skills

The [Trailmark plugin](https://github.com/trailofbits/skills/tree/main/plugins/trailmark)
in `trailofbits/skills` provides agent workflows for using Trailmark. When a
task calls for graph analysis, select the skills that match it:

| Task | Skills |
| --- | --- |
| Build and query code graphs | [trailmark](https://github.com/trailofbits/skills/blob/main/plugins/trailmark/skills/trailmark/SKILL.md) |
| Gather bounded source context | [slicing-code-context](https://github.com/trailofbits/skills/blob/main/plugins/trailmark/skills/slicing-code-context/SKILL.md) |
| Review structural changes | [graph-evolution](https://github.com/trailofbits/skills/blob/main/plugins/trailmark/skills/graph-evolution/SKILL.md), [trailmark-review-gate](https://github.com/trailofbits/skills/blob/main/plugins/trailmark/skills/trailmark-review-gate/SKILL.md) |
| Triage findings and related variants | [trailmark-finding-triage](https://github.com/trailofbits/skills/blob/main/plugins/trailmark/skills/trailmark-finding-triage/SKILL.md), [trailmark-variant-neighborhood](https://github.com/trailofbits/skills/blob/main/plugins/trailmark/skills/trailmark-variant-neighborhood/SKILL.md) |
| Generate diagrams | [diagramming-code](https://github.com/trailofbits/skills/blob/main/plugins/trailmark/skills/diagramming-code/SKILL.md) |
| Triage mutation survivors | [genotoxic](https://github.com/trailofbits/skills/blob/main/plugins/trailmark/skills/genotoxic/SKILL.md) |

The plugin README covers installation and compatibility. Check the installed
Trailmark version and follow the selected skill's feature gates.

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
- Annotation sources: `"llm"`, `"docstring"`, `"manual"` for user
  annotations; built-in passes use `"preanalysis"`, `"sarif:<tool>"`, and
  `"weaudit:<author>"`.
- Frozen dataclasses for immutable data. `CodeGraph` is mutable.
- No relative (`..`) imports. Use `from trailmark.*`.
- When documented behavior changes, update the guide in `docs/` that covers
  it (or `README.md` for the landing page) and the matching check in
  `tests/test_docs.py`. `CONTRIBUTING.md` maps topics to files.

## File Layout

```
src/trailmark/
  models/          # Data classes: CodeUnit, CodeEdge, Annotation, CodeGraph
    graph.py       # CodeGraph with add_annotation, clear_annotations, merge
    nodes.py       # CodeUnit, Parameter, TypeRef, BranchInfo, NodeKind, NodeOrigin
    edges.py       # CodeEdge, EdgeKind, EdgeConfidence
    annotations.py # Annotation, AnnotationKind, EntrypointTag
    lookup.py      # Name-to-node-ID ranking shared by queries and diagrams
  parsers/         # Language-specific tree-sitter parsers
    base.py        # LanguageParser protocol
    _common.py     # Shared parser utilities, directory walking, cross-file linking
    python/        # One subpackage per language
    javascript/
    ...
  tree_sitter_custom/ # Bundled Circom, Miden Assembly, and Verus grammars
  analysis/        # entrypoints, links, proxies, diff, augment, binary, preanalysis
  storage/
    graph_store.py # GraphStore: rustworkx-backed indexed storage
  query/
    api.py         # QueryEngine: high-level facade
  parse.py         # Public parse API and language registry
  diagram.py       # Mermaid diagram generation
  cli.py           # CLI entry point
docs/              # User guides; see CONTRIBUTING.md for the topic map
tests/             # pytest test suite
```

## Running Checks

```bash
npm ci
uv sync --all-groups
uv run ruff check hatch_build.py src/ tests/
uv run ruff format --check hatch_build.py src/ tests/
uv run --locked ty check
uv run pytest -q tests/
```

## Mutation Testing

```bash
uv run python -m tests.run_mutations --reviewed --workers=4
uv run python -m tests.mutation_gate
```

The runner uses real pytest subprocesses and validates clean and instrumented
baselines. It orders component unit tests first and retains all selected tests
as fallback. Use it rather than invoking the pinned plugin directly. Omit
`--reviewed` for a full repository campaign; CI runs that after merges
and on manual dispatch. `--workers` already enables parallel execution.
Do not enable `TRAILMARK_UPDATE_SNAPSHOTS` during CI or mutation testing.

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
