# Changelog

## 0.5.0

- Pin `tree-sitter` to the compatible 0.25 series, with large-file native-crash
  regressions for Go, Rust, Solidity, and TypeScript (#61, #62).
- Detect Solidity entrypoints from parser metadata, exclude interfaces, suppress
  overridden base implementations, and expose visibility/mutability (#57).
- Resolve straightforward constructed TypeScript interface receivers and
  document call-reachability versus taint limitations (#30).
- Add PostgreSQL-oriented SQL schema, table, view, function, procedure, and
  dependency extraction (#59).
- Add stable `.trailmark/links.toml` configuration for cross-language, FFI, RPC,
  and external/binary graph links (#58).
- Support C# file-scoped namespaces (#63).
- Document grammar caching and TLS-inspection/offline installation (#39).
- Add wheel/sdist installed-package smoke tests across all supported languages.

New `NodeKind` members are additive. `.trailmark/links.toml` is a new stable
configuration interface. Dynamic dispatch, full SQL query semantics, and true
interprocedural taint analysis remain out of scope for this release.
