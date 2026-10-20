# CLI reference

Every subcommand that reads source code takes `--language` (`-l`). The default
is `python`. Pass a key from the
[language table](../README.md#supported-languages), a comma-separated list such
as `python,rust`, or `auto` to detect and merge every supported language found
in the tree.

- [`trailmark analyze`](#trailmark-analyze)
- [`trailmark entrypoints`](#trailmark-entrypoints)
- [`trailmark diff`](#trailmark-diff)
- [`trailmark diagram`](#trailmark-diagram)
- [`trailmark augment`](#trailmark-augment)
- [`trailmark version`](#trailmark-version)

## `trailmark analyze`

```bash
trailmark analyze [--language LANG] [--summary | --complexity N | --resolution] PATH
```

Parses `PATH` and prints the graph. The output flags are mutually exclusive.

| Flag | Output |
| --- | --- |
| *(none)* | Full graph as JSON (the same as `QueryEngine.to_json()`) |
| `--summary`, `-s` | Node counts (functions, classes, proxies), call-edge count, dependencies, and entrypoint count |
| `--complexity N`, `-c N` | One line per function with cyclomatic complexity &ge; `N`: ID, complexity, and `file:line` |
| `--resolution` | Call-resolution report as JSON; see [Call resolution](call-resolution.md) |

The JSON graph contains nodes, edges, subgraphs, and a summary. It does not
contain annotations, entrypoint tags, or per-call-site locations; see
[JSON export](python-api.md#json-export) for what is omitted and how to get it.

```bash
trailmark analyze --summary path/to/project
trailmark analyze --language auto path/to/project > graph.json
trailmark analyze --complexity 10 path/to/project
trailmark analyze --language rust --resolution path/to/project
```

## `trailmark entrypoints`

```bash
trailmark entrypoints [--language LANG] [--json] PATH
```

Lists detected entrypoints with their kind, trust level, asset value, and
description. `--json` prints the result of `QueryEngine.attack_surface()`,
which also includes parser attributes such as Solidity visibility and
mutability. When nothing is detected, the command suggests declaring
entrypoints in `.trailmark/entrypoints.toml`. See [Entrypoints](entrypoints.md).

```bash
trailmark entrypoints path/to/project
trailmark entrypoints --language solidity --json contracts/
```

## `trailmark diff`

```bash
trailmark diff [--language LANG] [--repo REPO] [--json] BEFORE AFTER
```

Compares two snapshots of a codebase. Each side is a directory if one exists at
that path; otherwise it is treated as a git ref (branch, tag, or commit) in
`--repo` (default: the current directory). Refs are checked out into temporary
`git worktree` directories, which are removed afterwards; your working tree is
not touched.

The text report shows up to 20 each of the added, removed, and modified nodes
(for example, changed complexity, parameters, or line span), the number of
added and removed edges, and every added, removed, or modified entrypoint.
`--json` emits the complete structure returned by `QueryEngine.diff_against()`,
including each changed edge.

```bash
trailmark diff before/ after/
trailmark diff --repo . main HEAD
trailmark diff --language auto --json v0.6.0 HEAD
```

Some node IDs include a source position (for example, overloads and repeated
Rust local names; see [Node IDs](graph-model.md#node-ids)). Editing code above
such a declaration changes its ID, so the diff reports it as removed and added.

## `trailmark diagram`

```bash
trailmark diagram --target PATH --type TYPE [--language LANG] [--focus NAME]
                  [--depth N] [--direction TB|LR] [--threshold N]
```

Prints a [Mermaid](https://mermaid.js.org/) diagram. `--target` (`-t`) and
`--type` (`-T`) are required.

| `--type` | Shows |
| --- | --- |
| `call-graph` | Call edges. With `--focus`, only callers and callees within `--depth` calls of the focus node, in either direction (default depth 2). |
| `class-hierarchy` | Inheritance and implementation relationships |
| `module-deps` | `imports` edges. Only the Func, Move, Proto, Rego, Sway, Tact, and Thrift parsers emit them; other languages produce an empty diagram. |
| `containment` | Classes and their members |
| `complexity` | Functions with cyclomatic complexity &ge; `--threshold` (default 10) and the calls between them, colored low (under 5), medium (5&ndash;10), or high (over 10) |
| `data-flow` | Call paths of at most `--depth` edges (default 20) from detected entrypoints to `--focus`. Without `--focus`, the targets are up to ten non-entrypoint functions with complexity &ge; 10, and the diagram is empty if there are none. |

`--focus` (`-f`) accepts the same names as the Python API, and `--depth` (`-d`)
is measured in edges. `--direction` sets top-to-bottom (`TB`, the default) or
left-to-right (`LR`) layout. Edge styles show confidence; `data-flow` uses the
weakest confidence when one function calls another from several sites.

Diagrams with more than 100 nodes print a warning to stderr. Use `--focus` to
scope large graphs.

```bash
trailmark diagram -t path/to/project -T call-graph -f parse_file --depth 3
trailmark diagram -t path/to/project -T complexity --threshold 5 --direction LR
trailmark diagram -t contracts/ -l solidity -T data-flow -f Vault.withdraw
```

`python -m trailmark.diagram` accepts the same options.

## `trailmark augment`

```bash
trailmark augment [--language LANG] [--sarif FILE]... [--weaudit FILE]... [--json] PATH
```

Parses `PATH`, imports findings, and prints how many findings matched graph
nodes. `--sarif` and `--weaudit` are repeatable, and every file's findings are
kept. Matching rules, created subgraphs, and the Python equivalents are in
[Findings and preanalysis](augmenting.md).

`trailmark augment --json` writes the graph topology and selected node metadata
as JSON to stdout and import summaries to stderr, so stdout can be redirected
to a file. The JSON does not include finding details; use the Python API's
`findings()` and `annotations_of()` for those.

```bash
trailmark augment --sarif results.sarif path/to/project
trailmark augment --sarif semgrep.sarif --sarif codeql.sarif --weaudit audit.json path/to/project
trailmark augment --sarif results.sarif --json path/to/project > graph.json
```

## `trailmark version`

```bash
trailmark version
trailmark --version     # or -V
```

Prints `trailmark X.Y.Z`.

## Version notes

`trailmark diagram` was added in 0.4.0, and `trailmark analyze --resolution` in
0.7.0. Every other subcommand and flag has been available since 0.2.2.
