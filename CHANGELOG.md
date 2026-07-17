# Changelog

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
