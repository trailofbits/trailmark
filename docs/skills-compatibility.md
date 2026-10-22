# v0.7 integration and skills compatibility

Check the installed Trailmark version (`trailmark --version` or
`trailmark.__version__`) separately from the skills/plugin package version.
Feature gates should be based on tested Trailmark behavior. The
[Trailmark plugin](https://github.com/trailofbits/skills/tree/main/plugins/trailmark)
is maintained in a separate repository; these notes are the handoff for its
compatibility tests, not a claim that those external gates have been updated.

## Graph and diff consumers

- Graph JSON includes `schema_version` and `trailmark_version`. Inspect both;
  the export schema does not promise identical parser behavior across releases.
- Graphs and summaries expose `source_file_counts`. Zero declarations can still
  mean successfully inspected files. Missing roots and zero matching files fail
  unless the caller explicitly allows empty input.
- Graph JSON and summaries also include `parse_diagnostics`. Use
  `QueryEngine.parse_diagnostics()` or strict parsing for grammar/coverage checks.
  No diagnostics does not establish feature completeness.
- Native structural diffs include `source_coverage.before` and `.after` and
  signature/type/attribute changes. Confidence changes appear as an edge removal
  and addition. Strict validators must accept the complete current output;
  dropping unfamiliar fields would hide security-relevant differences.
- Rebuild both snapshots with the same Trailmark version and language selection.
  Namespace, nested callable, overload, Erlang arity, Ruby singleton,
  Objective-C class-method, and Rego clause IDs can differ from v0.6. Position
  suffixes move when source declarations move. See [parser coverage](parser-coverage.md).
- Test all three confidence values and query depth semantics against the installed
  version. An uncertain proxy is a retained site, not a proven target. Empty
  self-path lists must not classify a directly callable entrypoint as unreachable;
  include the entrypoint itself in the reachability set.

## Finding and state workflows

Bind Solidity storage declarations as `state_variable` nodes and inspect
`state_read_by`, `state_written_by`, `readers_of`, and `writers_of` alongside
call paths. Storage nodes are not function call targets. Query relationships
retain occurrence locations; do not turn repeated edges into independent claims.

SARIF imports resolve indexed artifacts and chained URI bases before matching
source files. Keep relocation rules explicit and inspect unmatched results.
Repeated finding imports and JSON-only CLI output have regression coverage;
verify those behaviors before enabling the corresponding workflow gate.

## Suggested compatibility checks in the skills repository

Run the shipped scripts and pending workflow changes against installed v0.6 and
v0.7 artifacts. Exercise signature-only diffs, confidence changes, storage types,
state readers/writers, an entrypoint querying itself, missing/empty roots,
non-default languages, malformed syntax, and same-basename SARIF artifacts.
Validate JSON stdout with a JSON parser and repeat a finding import to verify
its documented idempotence. Include the new callable identities and unsupported
syntax controls in strict schema tests. Record version-specific expectations;
update gates only after those tests pass.

## Release preflight

The publishing workflow runs `tests/release_preflight.py` before building and
again against the built wheel. The release tag, pyproject version, runtime
version, changelog heading and wheel metadata must agree. This does not publish
or tag anything during ordinary development.
