# CLI reference

Every subcommand that reads source code takes `--language` (`-l`). The default
is `python`. Pass a key from the
[language table](../README.md#supported-languages), a comma-separated list such
as `python,rust`, or `auto` to detect and merge every supported language found
in the tree.

Source-reading commands reject missing or non-directory roots and directories
with zero matching source files. Use `--allow-empty` for an intentional empty
input, such as a diff against a snapshot before its first source file was added.
The option still requires an existing directory. Empty or comment-only source
files are valid inputs. Inspect the selected languages and source-file counts
in the analysis summary; selecting one language does not cover the others.

- [`trailmark analyze`](#trailmark-analyze)
- [`trailmark entrypoints`](#trailmark-entrypoints)
- [`trailmark diff`](#trailmark-diff)
- [`trailmark diagram`](#trailmark-diagram)
- [`trailmark augment`](#trailmark-augment)
- [`trailmark binary`](#trailmark-binary)
- [`trailmark version`](#trailmark-version)

## `trailmark analyze`

```bash
trailmark analyze [--language LANG] [--allow-empty] [--json] [--summary | --complexity N | --resolution] PATH
```

Parses `PATH` and prints the graph. `--summary`, `--complexity`, and `--resolution`
are mutually exclusive.

| Flag | Output |
| --- | --- |
| *(none)* | Full graph as JSON (the same as `QueryEngine.to_json()`) |
| `--summary`, `-s` | Node counts (functions, classes, proxies), call-edge count, dependencies, entrypoint count, selected languages, and inspected source-file count |
| `--complexity N`, `-c N` | One line per function with cyclomatic complexity &ge; `N`: ID, complexity, and `file:line` |
| `--resolution` | Call-resolution report as JSON; see [Call resolution](call-resolution.md) |
| `--json` | With `--summary`, the same object as `QueryEngine.summary()` as JSON (v0.7+); graph and resolution output already use JSON |

`--summary --json` writes only the summary object to stdout, including
`languages`, `source_files`, and `source_file_counts`. It does not include the
graph's nodes, edges, or export provenance. `--json` cannot be combined with
`--complexity`.

`--complexity N` requires a nonnegative integer. An explicit `--complexity 0`
lists every function with a recorded complexity; omitting the output flags
prints JSON. Negative thresholds are rejected.

The JSON graph contains nodes, edges, subgraphs, a summary, and the
`trailmark_version` and `schema_version` export provenance fields (v0.7+).
It does not contain annotations, entrypoint tags, or per-call-site locations; see
[JSON export](python-api.md#json-export) for what is omitted and how to get it.

```bash
trailmark analyze --summary path/to/project
trailmark analyze --language auto --summary --json path/to/project > summary.json
trailmark analyze --language auto path/to/project > graph.json
trailmark analyze --complexity 10 path/to/project
trailmark analyze --language rust --resolution path/to/project
```

## `trailmark entrypoints`

`--evidence` emits all entrypoint observations as JSON; `--candidates` emits
observations outside the reported attack surface. The flags are mutually
exclusive. Bindings, source spans and unresolved facts are retained; see
[Entrypoint evidence](entrypoints.md#evidence-and-candidates).

```bash
trailmark entrypoints [--language LANG] [--allow-empty] [--json] PATH
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

Both `analyze` and `entrypoints` accept repeatable `--source-root NAME=PATH`,
`--include GLOB`, and `--exclude GLOB` options. The positional directory is the
`root` source root; additional roots retain their supplied names. Globs are
relative to each root, includes override exclusions, and canonical files are
parsed once. A deliberately selected dependency root bypasses its parents'
exclusions. Full graph JSON includes the effective source policy and decisions.

```bash
trailmark entrypoints --language auto --candidates \
  --source-root plugin=project/vendor/selected \
  --exclude 'generated/**' project
```

## `trailmark diff`

```bash
trailmark diff [--language LANG] [--repo REPO] [--allow-empty] [--json] BEFORE AFTER
```

Compares two snapshots of a codebase. Each side is a directory if one exists at
that path; otherwise it is treated as a git ref (branch, tag, or commit) in
`--repo` (default: the current directory). Refs are checked out into temporary
`git worktree` directories, which are removed afterwards; your working tree is
not touched.

The text report shows up to 20 each of the added, removed, and modified nodes
(for example, changed complexity, parameter/return types, declaration attributes,
or line span), the number of
added and removed edges, and every added, removed, or modified entrypoint.
`--json` emits the complete structure returned by `QueryEngine.diff_against()`,
including each changed edge's confidence and both snapshots' per-language
`source_coverage`. Confidence changes appear as removed and added edges. See the
[diff comparison scope](python-api.md#diff-and-export) for the full field list
and limits. Both sides must inspect source files unless `--allow-empty` is set.

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
                  [--depth N] [--direction TB|LR] [--threshold N] [--allow-empty]
```

Prints a [Mermaid](https://mermaid.js.org/) diagram. `--target` (`-t`) and
`--type` (`-T`) are required.

| `--type` | Shows |
| --- | --- |
| `call-graph` | Call edges. With `--focus`, only callers and callees within `--depth` calls of the focus node, in either direction (default depth 2). |
| `class-hierarchy` | Inheritance and implementation relationships |
| `module-deps` | Import relationships recorded as `imports` edges. See [import edges](graph-model.md#import-edges) for the frontends that emit them; dependency names alone do not create diagram edges. |
| `containment` | Classes and their members |
| `complexity` | Functions with cyclomatic complexity &ge; `--threshold` (default 10) and the calls between them, colored low (under 5), medium (5&ndash;10), or high (over 10) |
| `data-flow` | Call paths of at most `--depth` edges (default 20) from detected entrypoints to `--focus`. Without `--focus`, the targets are up to ten non-entrypoint functions with complexity &ge; 10, and the diagram is empty if there are none. |

`--focus` (`-f`) accepts the same names as the Python API, and `--depth` (`-d`)
is measured in edges. `--direction` sets top-to-bottom (`TB`, the default) or
left-to-right (`LR`) layout. Edge styles show confidence; `data-flow` uses the
weakest confidence when one function calls another from several sites.

Diagrams with more than 100 rendered nodes print a warning to stderr. For class
diagrams, each class counts as one node; its listed members do not add nodes.
Use `--focus` to scope large call graphs or select a data-flow target.

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

Repeatable `--binary ARTIFACT` additionally ingests linked native images. It
accepts the binary tool/range options below. `--binary-strict` rejects incomplete
binary imports; `--strict` continues to control source parsing. An explicit
`--debug-file` requires exactly one `--binary` image. Incomplete evidence produces
exit status 3 after printing the graph; invalid input produces status 2.

## `trailmark binary`

```bash
trailmark binary ARTIFACT [--source-root PATH] [--language auto] [--disassemble] [--json]
trailmark binary build/app --source-root . --function verify --disassemble --json
```

Inspect a linked ELF, Mach-O, or PE image, optionally connecting it to a source
graph from `--source-root`. This command is unreleased and requires
`trailmark[binary]`. Its source `--language`/`-l` default is `auto`; other
source-reading commands keep their existing defaults. `--allow-empty` permits
an empty source root. `--strict` rejects incomplete source or binary evidence.
`--function NAME` selects source/binary ranges for `--disassemble`; it does not
restrict ingestion. `--max-instructions N` bounds the total returned instructions
(default 256). Unknown function selections are errors. `--json` emits an object
containing `graph`, `report`, and optional `disassembly`. Exit status is 0 for
complete requested evidence, 3 for retained gaps or truncated disassembly, and
2 for invalid input. The inspected artifact is never executed.

These options are shared with `augment --binary`:

| Option | Meaning |
| --- | --- |
| `--backend auto\|llvm\|gnu` | Disassembler family; auto prefers compatible LLVM |
| `--objdump PATH` | Explicit host disassembler; family detected by capabilities |
| `--symbolizer PATH` | Explicit LLVM symbolizer or GNU addr2line |
| `--debug-file PATH` | Identity-checked local ELF debug companion, dSYM, or PDB |
| `--mapping-file PATH` | SHA-256-bound explicit source-node/range JSON |
| `--path-map OLD=NEW` | Repeatable source-path prefix remapping |
| `--slice INDEX` | One exact zero-based universal slice; default all supported slices |
| `--range START:END` | Repeatable half-open link-time virtual-address selection |
| `--rva-range START:END` | Repeatable RVA selection |
| `--file-range START:END` | Repeatable whole-container executable file-offset selection |
| `--mode arm\|thumb` | Explicit instruction mode; requires an ARM range selection |
| `--tool-timeout SECONDS` | Per invocation, default 30 |
| `--import-timeout SECONDS` | Per import, default 300 |
| `--max-tool-output-bytes N` | Combined output per invocation, default 67108864 |
| `--max-scan-instructions N` | Import scan budget, default 1000000 |

Addresses accept decimal or a base prefix such as `0x1040`. Selections must not
overlap. See [compiled binary evidence](binaries.md) for targets, tool requirements,
exact slice identities, explicit mappings, inference, and coverage limitations.

## `trailmark version`

```bash
trailmark version
trailmark --version     # or -V
```

Prints `trailmark X.Y.Z`.

## Version notes

`trailmark diagram` was added in 0.4.0, and `trailmark analyze --resolution` in
0.7.0. Entrypoint `--evidence` and `--candidates`, along with source-selection
`--source-root`, `--include`, and `--exclude` options, were added in 0.7.1.

## Strict source analysis

All source-analysis commands accept `--strict` to reject grammar errors and
explicitly unsupported constructs. The default retains partial graphs and
reports `parse_diagnostics` in the summary and JSON graph export. Source-file
counts measure inspection, independently of successful feature extraction.
An empty diagnostic list does not establish complete language coverage.
