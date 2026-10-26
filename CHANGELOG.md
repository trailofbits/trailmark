# Changelog

## Unreleased

- Add typed endpoint registrations, explicit native registry imports, Django,
  Symfony/API Platform and Magento source adapters, and GraphQL resolver and
  transport associations. Static presence retains unknown deployment state.
- Add named source roots, configurable source selection and file-level policy
  diagnostics. Generated `var/cache` output is excluded by default.
- Retain existing entrypoints across all 39 frontends in the registration
  inventory. Parse PHP `.phtml` theme templates and top-level script execution,
  including database calls, and preserve Magento layout/template candidates.
- Add source-ranged execution evidence and bounded authentication-path queries;
  trust tags and ordinary calls do not establish authentication.
- Resolve Python HTTP decorator receivers to avoid `mock.patch` false positives,
  identify Medusa file routes, discover `.graphqls`, and recover valid PHP
  `TRUE`/`FALSE` class constant declarations without suppressing other errors.

## 0.7.0 (2026-10-23)

This release adds nine language frontends, Solidity storage-access graphs, and
source-coverage and call-resolution diagnostics. It also corrects declaration
identities, lexical binding, call extraction, and analysis performance across
the 39 supported languages.

### Added

- **Nine language frontends:** Aiken, Bash, Julia, Lua, Perl 5, PowerShell, R,
  VB.NET, and Zig. These include Aiken validator handlers, script and chunk
  execution, anonymous functions, signatures, imports, and conservative call
  graphs where supported. See [language notes](docs/languages.md) for each
  frontend's scope and limitations.
- **Solidity persistent state:** `state_variable` nodes and occurrence-level
  `reads` and `writes` edges for functions, constructors, and modifiers. Query
  them with `state_read_by()`, `state_written_by()`, `readers_of()`, and
  `writers_of()`.
- **Call-resolution diagnostics:** `trailmark analyze --resolution`,
  `QueryEngine.call_resolution()`, and `unresolved_calls(name=None)` report
  unresolved call sites and their targets.
- **Source-file coverage:** graphs, summaries, and structural diffs report
  inspected file counts per language, including files with no declarations.
  Missing/non-directory roots and zero matching files now fail; use Python's
  `allow_empty=True` or the CLI's `--allow-empty` for intentional empty inputs.
- **Structured parse diagnostics:** inspect grammar errors, recovered syntax,
  unsupported constructs, and excessive nesting separately from source counts.
  Python `strict=True` and CLI `--strict` reject these diagnostics. Empty and
  comment-only source files remain valid; a clean parse does not prove coverage.
- **Export provenance and JSON summaries:** graph exports include
  `trailmark_version` and `schema_version` (initially `1`).
  `trailmark analyze --summary --json` prints the same summary as the Python API.

### Changed

- Structural diffs detect parameter types/defaults, return and exception types,
  generic parameters, declaration attributes, and node kind/origin/name changes.
  Edge comparisons include confidence; confidence changes appear as removals
  and additions. Diffs include `source_coverage.before` and `.after`.
- Cross-file linking binds unique free functions with `inferred` confidence.
  Ambiguous overloads and unresolved receivers retain `uncertain` proxies.
  Solidity calls can follow resolved inheritance; C, C++, and Objective-C calls
  can link across frontends in polyglot parses. Python candidates include all
  module-level functions and classes. Type edges target type declarations.
- Nested callable bodies retain their own owners and possible callback paths
  through uncertain `deferred` call edges. Calls through returned values or
  computed callees retain distinct indirect sites instead of inventing targets.
- Repeated declarations and overloads retain separate nodes. Python's final
  definition keeps the plain ID, earlier definitions gain position suffixes,
  and `@overload` stubs are omitted. Name lookup prefers exact IDs and source
  declarations before proxies, consistently across queries and diagrams.
- Literal quoted C headers are cached within each parse and read only inside
  the analysis root. `.h` files are parsed once, using Objective-C when both C
  and Objective-C are selected.

### Fixed

- Recover C function bodies independently through brace-neutral preprocessor
  conditionals and bounded block-macro recovery. Preserve valid call confidence
  despite unrelated syntax errors, link included static definitions, and keep
  same-file calls past swallowed prototypes. Ordinary `void`/parameter-macro
  declarations keep plain IDs; genuine declaration wrappers retain their
  verified expansions or symbolic identities and diagnostics.
- Correct lexical shadowing, declaration order, captured parameters, local
  classes, namespaces, overload descendants, and object-method ownership across
  frontends. This includes Python lambdas and repeated bodies, JS/TS named
  function expressions and IIFEs, TypeScript parameter decorators and merged
  namespaces, PHP namespace calls, Ruby block parameters, and C# delegate bodies.
- Preserve Go closures, Haskell do-bind calls, Dart constructor initializers,
  Java constructor links, Swift nested-type extensions, and Move `Self::` and
  qualified calls. The bundled Move grammar supports explicit generics and
  common Aptos syntax. Unsupported or skipped syntax remains diagnosed.
- Bound recursive extraction and macro expansion. Index declarations, receivers,
  header directives, and linker metadata instead of rescanning for every call;
  deduplicate parse diagnostics in linear time. These changes remove the
  quadratic and exponential slowdowns found in parser regression cases.
- Match SARIF indexed artifacts and bounded URI-base chains, including undefined
  bases used by Semgrep. Decode paths once, preserve path characters, and reject
  traversal above defined bases. Finding importers validate inputs before
  mutation and accept `replace_existing=True`. Repeated CLI import flags retain
  every file; `augment --json` sends import summaries to stderr.
- Count path depths in edges consistently. `paths_between()` accepts keyword-only
  `max_depth=20`; non-positive depths fail, self-paths return `[]`, and parallel
  call sites do not duplicate paths. Reachability lists are sorted by ID.
- Correct entrypoint decorator/export ownership, Solidity state access, and
  entrypoint override matching. Unmatched explicit override keys warn. Preserve
  distinct unresolved proxies and reject conflicting binary-import metadata
  without changing the graph. Diagrams honor depth, direction, thresholds, and
  actual edge confidence.

### Packaging and development

- Test Python 3.12–3.15 in CI, with installed wheel and sdist checks on Python
  3.13 and 3.15 on Linux and macOS. Python 3.12 remains the minimum version.
- Generate bundled Circom, Move, and Verus parsers during builds rather than
  tracking generated sources in Git. Wheels and sdists include the generated
  sources and grammar notices; installing them does not require Node.js.
- Validate the release tag, package/runtime versions, dated changelog entry,
  wheel metadata, and root sdist `PKG-INFO` before publishing. Keep the
  `tree-sitter` dependency below 0.26 to avoid native crashes; update the pinned
  pytest-gremlins runner to 1.11.4 and reject skipped instrumentation.
- Reorganize the reference guides under `docs/`, including
  [parser coverage](docs/parser-coverage.md) and
  [skills compatibility](docs/skills-compatibility.md).

### Upgrading from 0.6

- **Rebuild saved graphs and ID-keyed configuration.** IDs change for overloads,
  colliding same-stem modules, nested and anonymous callables, namespaced types,
  C declaration wrappers, and several language-specific declarations. Position
  suffixes move with the source. See [node IDs](docs/graph-model.md#node-ids) and
  [parser coverage](docs/parser-coverage.md). A diff across parser versions can
  report these as removals and additions; rebuild both snapshots with the same
  Trailmark version and language selection for code-change comparisons.
- **Update strict JSON validators.** Accept export provenance, parse diagnostics,
  source coverage, and the expanded structural-diff fields. Export schema
  version `1` does not promise identical parser behavior across releases.
- **Review reachability assumptions.** Some formerly certain cross-file edges
  are now inferred, ambiguous targets remain proxies, and deferred callback
  edges add possible paths. An empty self-path does not make an entrypoint
  unreachable. Solidity state edges cover direct access to storage declared in
  the same contract; missing edges do not establish dead code or safe data flow.
- **Handle explicit input failures.** Empty selections require `allow_empty`;
  unresolved node-ID collisions between languages raise `ValueError`.
- Import `git_worktree` from `trailmark.analysis.diff`; its old
  `trailmark.cli` re-export is removed. See the
  [skills compatibility guide](docs/skills-compatibility.md) before widening
  external workflow version gates.

## 0.6.0

- Extract ordinary, `spec`, and `proof` functions and calls from brace-delimited
  `verus!` blocks, including qualified invocations, with source locations and
  diagnostics for unsupported or invalid blocks (#76).
- Preserve Rust function-local functions, types, impls, traits, and inline
  modules as separate graph nodes. Resolve calls and relative imports within
  each file using lexical and block scope, including duplicate local names (#76).
- Generate the vendored Verus grammar during builds and include generated
  sources in wheels and source distributions. Share content-addressed, atomic
  native grammar loading across Verus, Circom, and Miden Assembly (#76).
- Escape double quotes in Mermaid node labels so names and branch conditions
  containing quotes produce valid diagrams (#78).
- Build Nix packages from `uv.lock`, add a development shell and package checks,
  and precompile bundled grammars for use in the read-only Nix store (#80).
- Require `tree-sitter-language-pack` 1.22.1 or newer and use its supported
  root-owned grammar cache in Nix (#79, #81).
- Refresh Python dependencies, Tree-sitter CLI, Nix inputs, and GitHub Actions;
  lock the type checker and enable Dependabot updates for uv and npm. Keep the
  Python `tree-sitter` bindings on 0.25.2 to avoid native crashes in 0.26.0 (#81).
- Replace mutmut with a pinned pytest-gremlins runner, validated baselines,
  reviewed mutation gates for Rust/Verus changes, and full campaigns on main.

Rust scope resolution changes also apply outside Verus blocks. Repeated local
names use byte-offset suffixes in their node IDs; edits before those declarations
can therefore appear as removals and additions in graph diffs. Arbitrary macro
expansion, bodyless signatures, calls in Verus contract clauses, implicit
destructor calls, and receiver type inference remain out of scope.

## 0.5.0

- Pin `tree-sitter` to the compatible 0.25 series, with large-file native-crash
  regressions for Go, Rust, Solidity, and TypeScript (#61, #62).
- Detect Solidity entrypoints from parser metadata, exclude interfaces, retain
  overridden base implementations with `solidity_overridden_by`, and expose
  visibility/mutability (#57).
- Resolve straightforward constructed TypeScript interface receivers and
  document call-reachability versus taint limitations (#30).
- Add PostgreSQL-oriented SQL schema, table, view, function, procedure, and
  dependency extraction (#59).
- Add stable `.trailmark/links.toml` configuration for cross-language, FFI, RPC,
  and external/binary graph links with per-endpoint external flags (#58).
- Materialize repository links, unresolved-call proxies, and `TYPE_USES` edges
  for single-language directory parses as well as polyglot parses.
- Support C# file-scoped namespaces (#63).
- Document grammar caching and TLS-inspection/offline installation (#39).
- Add wheel/sdist installed-package smoke tests across all supported languages.
- Add a Nix flake package (#56).

New `NodeKind` members are additive. `.trailmark/links.toml` is a new stable
configuration interface. Dynamic dispatch, full SQL query semantics, and true
interprocedural taint analysis remain out of scope for this release.

## 0.4.0

- Add Move, Tact, FunC, Sway, Rego, Protocol Buffers, Thrift, and GraphQL
  parsers, each with entrypoint detection: Move `public`/`entry` functions,
  Tact receivers and initializers, FunC `recv_internal`/`recv_external`/`get_*`,
  Sway `pub fn` and ABI methods, Rego `allow`/`deny`/`violation` rules, and
  service RPCs, service functions, and root operation fields (#53).
- Add the `trailmark diagram` subcommand for Mermaid call graphs, class
  hierarchies, module dependencies, containment, complexity, and data-flow
  diagrams. `python -m trailmark.diagram` still works (#52).
- Represent unresolved call targets as `proxy.unresolved:<raw-symbol>` nodes
  instead of dropping those edges, and add `type_uses` edges from functions to
  the types named in their signatures (#49).
- Add `NodeOrigin` (`source`, `proxy`, `binary`, `synthetic`), key/value
  `attributes` on nodes and edges, and generic type parameters for C++, C#,
  Dart, Go, Java, Kotlin, Python, Rust, Swift, and TypeScript (#49).
- Add `augment_binary()` to import an externally produced binary call graph as
  a `binary:<artifact>` subgraph, with `corresponds_to` edges to matching source
  nodes (#49).
- Add `connect_subgraphs()`, `subgraph_edges()`, `generic_parameters()`,
  `type_references()`, and `GraphStore.rebuild_index()`. Report a `proxies`
  count in `summary()` and `trailmark analyze --summary` (#49).
- Require `tree-sitter-language-pack>=1.8,<2.0` (#47).
- Add known-answer snapshot tests for every parser, diagram type, and CLI
  output (#48).

New `NodeKind` (`proxy`) and `EdgeKind` (`type_uses`, `corresponds_to`,
`resolves_to`, `specializes`) members are additive; no built-in pass emits
`resolves_to` or `specializes`. Graph JSON may include `origin`, `attributes`,
and `type_parameters`, omitted at their defaults. Proxy nodes now appear in
traversal results and node counts. Trailmark does not disassemble binaries,
and binary import has no CLI flag.

## 0.3.1

- Cap `tree-sitter-language-pack` below 1.7 after the 1.8 series broke
  parsing (#41).
- Keep Erlang remote calls module-qualified (`module:function`) and extract
  calls nested in call arguments (#41).

## 0.3.0

- Add the public `trailmark.parse` module with `parse_file()`,
  `parse_directory()`, `detect_languages()`, and `supported_languages()`, also
  exported from `trailmark`. `trailmark.query.api.detect_languages` remains as
  a deprecated alias (#23).
- Make language auto-detection follow the parser's directory-skipping rules
  (#23).
- Restrict `reachable_from()`, `ancestors_of()`, `paths_between()`,
  `entrypoint_paths_to()`, and blast-radius preanalysis to `calls` edges;
  containment, inheritance, and import edges no longer count as reachability
  (#32).
- Link bare cross-file calls after directory parsing instead of dropping them
  (#34).
- Recover C and C++ functions inside preprocessor conditional blocks and
  tree-sitter error nodes (#35).
- Walk directories in sorted order for deterministic output (#38).
- Exclude `tree-sitter-language-pack` 1.6.3, which lacks a CPython 3.12 macOS
  arm64 wheel (#33).

Reachability and path results can shrink for graphs that previously reached
nodes through containment or import edges, and directory parses gain
cross-module call edges.
