# Changelog

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

New `NodeKind` members are additive. `.trailmark/links.toml` is a new stable
configuration interface. Dynamic dispatch, full SQL query semantics, and true
interprocedural taint analysis remain out of scope for this release.
