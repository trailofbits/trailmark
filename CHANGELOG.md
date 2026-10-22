# Changelog

## 0.7.0 (unreleased)

- Correct declaration ownership, lexical binding, call-site extraction, signatures,
  and entrypoint metadata across the 39 language frontends. See
  [parser coverage](docs/parser-coverage.md) for syntax limits and ID changes,
  including Erlang arity, Ruby singleton methods, Objective-C class methods,
  namespaces, local callables and Rego clauses.
- Expose structured parse diagnostics separately from inspected source counts.
  Python `strict=True` and CLI `--strict` reject diagnostics, including explicitly
  skipped Verus and unsupported Rego syntax. A clean tree is not proof of coverage.
- Resolve SARIF run artifact indices and chained URI bases before finding matching.
- Recover bounded C source spans around block macros with explicit diagnostics,
  retain declaration-macro identities and aliases, and infer callbacks through
  unambiguous file-local constant designated tables.
- Bundle a pinned Move grammar to distinguish explicit generic applications from
  comparisons and retain qualified call symbols.
- Check release tag, package/runtime versions, and built wheel metadata before
  publishing. Document the v0.7 skills compatibility validation checklist.

- Bash frontend: shell functions, script command sites, branches, literal source
  dependencies, and conservative local call inference.

- Add a Zig frontend for `.zig` files, with named containers, function
  signatures, direct calls, imports, and exported API entrypoints. Calls
  requiring comptime evaluation or receiver inference stay uncertain.
- Detect parameter types/defaults, return and exception types, generic
  parameters, declaration attributes, and node kind/origin/name changes in
  structural diffs. Edge differences include confidence; confidence changes
  appear as removals and additions. Diffs include both snapshots' source-file
  coverage. Consumers validating the diff schema must accept these fields.
- Reject missing/non-directory analysis roots and zero matching source files.
  Python directory factories accept `allow_empty=True`, and source-reading CLI
  commands accept `--allow-empty` for intentional empty inputs. Graphs record
  inspected source-file counts per language; summaries expose those counts and
  selected languages, including files with no declarations.
- Add top-level `trailmark_version` and `schema_version` (initially `1`) to
  graph JSON exports. The schema version describes the export format; parser
  behavior still depends on the Trailmark version and selected languages.
- Add `trailmark analyze --summary --json` to print `QueryEngine.summary()`
  directly, without importing CLI internals. Existing text summaries remain
  available; `--json` cannot be combined with `--complexity`.
- Model Solidity persistent state. Contract storage declarations become
  `state_variable` nodes (for example `token:Token.balances`) with `reads` and
  `writes` edges from functions, constructors, and modifiers, one per
  occurrence. Query them with `state_read_by()`, `state_written_by()`,
  `readers_of()`, and `writers_of()` (#84).
- Add an Aiken frontend for `.ak` files, including validator contracts and
  all six purpose handlers plus the fallback, helper functions, custom types,
  imports, and direct/pipeline calls. Validator handlers are untrusted API
  entrypoints; higher-order dispatch remains uncertain.
- Add `trailmark analyze --resolution`, `QueryEngine.call_resolution()`, and
  `QueryEngine.unresolved_calls(name=None)` for counting and inspecting
  unresolved call sites. Document Rust call-resolution limits (#85).
- Add a PowerShell frontend for scripts/modules, functions, filters, classes,
  script blocks, typed parameters, dependencies and conservative command calls.
- Keep overloads in Solidity, C++, Java, C#, Kotlin, and Swift as separate
  nodes. Colliding callable IDs gain an `@line_column` suffix; a type that
  shares a callable's ID keeps the base ID. Calls that need overload resolution
  stay unresolved and `uncertain` (#86).
- Add a Perl 5 frontend for packages, subroutine bodies, signatures/prototypes,
  anonymous subs, build-script calls, dependencies and conservative package binding.
- Give same-stem files distinct module IDs when they would collide, using
  escaped extensions (`app\.py`, `app\.rs`, `pkg.__init__\.py`). Solidity
  contract-level structs and enums are now `module:Contract.Name`, and Java
  nested classes (`module:Outer.Inner`) and enum-body methods are extracted (#86).
- Make linking more conservative. Bare cross-file calls bind only to a unique
  free function, with `inferred` confidence. Unqualified Solidity and C++ calls
  prefer the caller's own container, and Solidity calls can bind through
  resolved inheritance. `inherits` and `implements` edges link only to unique
  same-language type declarations, and `type_uses` edges target only type
  declarations (#86).
- Add an R frontend for named/anonymous functions, closures, default/variadic
  parameters, branches, call sites and literal package/source dependencies.
- Parse each `.h` file once, with the Objective-C grammar when both C and
  Objective-C are selected, and link calls across the C, C++, and Objective-C
  frontends in polyglot parses (#86).
- Add a VB.NET frontend (`vb`) with typed methods, containers, overloads,
  lambdas, accessor bodies, conservative calls and explicit CLI Main detection.
- Match file extensions case-insensitively. Explicit language lists drop empty
  and duplicate entries, and an empty list raises `ValueError`. Node-ID
  collisions between languages raise `ValueError` instead of merging unrelated
  nodes (#86).
- Add a Julia frontend for `.jl` files: modules, types, long/short functions,
  closures, signatures, branches, imports, and conservative local calls.
  Multiple-dispatch candidates remain separate and unresolved; opaque macros
  warn instead of inventing expansions.
- `paths_between()` gains a keyword-only `max_depth=20`. `max_depth` now counts
  edges in `paths_between()`, `entrypoint_paths_to()`, and
  `connect_subgraphs()`; the latter two previously stopped one edge short.
  Non-positive depths raise `ValueError`, self-paths return `[]`, parallel call
  sites no longer duplicate paths, and `reachable_from()`/`ancestors_of()` are
  sorted by ID (#86).
- Name lookup, including `trailmark diagram --focus`, tries an exact node ID,
  then concrete declarations, then proxies, matching by name, `:name` suffix,
  and `.name` suffix in that order (#86).
- `augment_sarif()` and `augment_weaudit()` take `replace_existing=True`.
  Repeated `--sarif`/`--weaudit` flags keep every file's findings instead of
  only the last. Imports validate their input, leave the graph unchanged on
  failure, and attach findings only to source nodes. `augment --json` writes
  import summaries to stderr so stdout is valid JSON (#86).
- Binary imports reject colliding node IDs and conflicting architecture or hash
  metadata for the same artifact, leaving the graph unchanged (#86).
- `trailmark diagram --depth` counts edges and defaults to 2 for `call-graph`
  and 20 for `data-flow`. Data-flow diagrams show the actual (weakest parallel)
  edge confidence instead of always `certain` (#86).
- `.trailmark/entrypoints.toml` `file_glob` rules match slash-bounded path
  suffixes relative to the configuration root (#86).
- Fix Solidity state tracking for nested `push`/`pop`, struct members,
  initializer scoping, and call-option labels; avoid recursion-limit failures
  on very deep Solidity and C# expressions; keep qualified Sway, Rego, and Tact
  calls as `uncertain` proxies instead of binding them to same-named local
  declarations (#86).
- Distinct unresolved targets that escape to the same proxy ID gain a hash
  suffix instead of merging, and colliding external proxy identities raise
  `ValueError` (#86).
- Remove the `trailmark.cli.git_worktree` re-export; import it from
  `trailmark.analysis.diff` (#86).
- Reorganize the documentation: a shorter README, and reference guides under
  `docs/` for the CLI, Python API, graph model, entrypoints, links, findings
  and preanalysis, call resolution, languages, and installation.
- Add a Lua frontend for `.lua` files, with local/global functions, table
  methods, anonymous functions, chunk execution, and literal `require`
  dependencies. Respect lexical scope, declaration order, and reassignment;
  table dispatch and callbacks remain uncertain.
- Associate entrypoint decorators and export markers with their declarations
  instead of nearby source lines. Detect legacy PHP route comments and Swift
  `@main` methods without tagging neighbouring helpers. Circom main components
  are untrusted entrypoints and participate in taint propagation.
- Skip malformed entrypoint override fields without crashing or broadening
  rules. Keep functions inside C++ `extern "C"` blocks in the graph.
- Honor explicit `analyze --complexity 0`; reject negative thresholds. Class
  diagrams honor `--direction` and warn when more than 100 classes are rendered.
- Upgrade pytest-gremlins to 1.11.4 and revalidate the mutation runner against
  its pytest and import handling. Reject skipped instrumentation. Keep
  `tree-sitter` on the 0.25 series because 0.26.0 causes native crashes.
- Test Python 3.15 in CI alongside 3.12–3.14. Run wheel and source-distribution
  smoke tests on Python 3.13 and 3.15 on Linux and macOS.

Many node IDs change in this release: overloaded callables, colliding
same-stem modules, Solidity contract-level types, and Java nested classes. A
`trailmark diff` across the upgrade shows these as removals and additions.
Cross-file calls that were `certain` are now `inferred`, and some ambiguous or
method targets that were linked before are now unresolved proxies. Polyglot
parses that silently merged colliding IDs now raise `ValueError`. Solidity
state edges cover only direct access to storage declared in the same contract.

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
