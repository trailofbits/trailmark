# Release-readiness roadmap

The October 30, 2026 audit reviewed commit
`2babb4ac08206433a46497b01e3186807a28b9b9` plus the exception-evidence work.
It recorded 343 findings: 282 new defects, one regression, 47 enhancements,
and 13 documented limitations. Its 350 work items include seven shared readiness
tasks. These are historical audit results, not a claim that the findings are
fixed or that a stable release is ready. No release version or date is assigned.

The exception feature retains syntax evidence and publishes frontend limits;
see [exceptions.md](exceptions.md). It does not recover runtime dispatch,
interprocedural propagation, implicit callbacks, or binary unwinding.

## Implementation priorities

Address regression and release controls first, then shared binding and ownership
problems before expanding inference. Work IDs refer to the archived full plan.

| Stream | Representative work | Required behavior |
| --- | --- | --- |
| Binding and confidence | WORK-004, 022, 064, 127 | Respect each language's binders, writes, scope lifetimes and Unicode identifiers; preserve unresolved calls when targets are unknown. |
| Declaration identity and ownership | WORK-001, 002, 084–090, 111, 340 | Retain nested, repeated and deferred declarations with distinct owners; remap descendants and diagram identifiers consistently. |
| Lexical and source boundaries | WORK-009, 118, 328, 331, 335, 341, 343 | Keep comments and literals inert, preserve quoted identifiers and coordinates, and apply strict source policy consistently. |
| Metadata and query correctness | WORK-026, 069, 310, 336, 338, 342 | Preserve exception ownership, frontend roles, subgraph existence, ARM mode and decorator provenance. |
| Release infrastructure | WORK-339, 346–349 | Keep packaging checks current; review mutation survivors; validate exact locks, grammar generation and representative performance. |
| Capability and API contracts | WORK-344, 345, 350 | Publish tested language boundaries and migration rules before promising stability; scope deeper analysis separately. |

For each fix, bring its minimized reproduction and passing controls into the
normal test suite. Assert expected ownership, targets, confidence, and metadata;
review identity and JSON changes explicitly. Run the relevant parser, query,
and documentation checks with snapshot updates disabled. Close a finding only
after reviewing the corrected behavior.

## Recorded release gaps

The current feature branch addresses five reproduced remaining-code defects:
stale subgraph membership (REMAINING-001), Mermaid ID collisions
(REMAINING-005), oversized XML numeric references (REMAINING-006), future and
conditional decorator bindings (REMAINING-007), and Unicode comment delimiters
in Rust and Verus scanners (REMAINING-008). Focused regressions cover each fix.
These corrections do not close the wider language audit backlog.

- The reviewed mutation campaign had 22 unreviewed survivors out of 443 mutants;
  its gate failed. A full repository campaign was not run.
- The source-distribution smoke expected an outdated public-guide set. Keep its
  allowlist synchronized when adding guides, including exception documentation.
- Fresh exact-lock checks and broader interpreter/platform coverage remained
  open. Recorded full-suite results used the installed audit environment on
  CPython 3.15, macOS arm64; installed-package smokes covered 3.12–3.15 there.
- Stable API, CLI, JSON, identity, confidence, and diff contracts, a tested
  capability matrix, grammar provenance, and representative performance checks
  still require acceptance. Passing existing tests does not close audit findings.

## Full evidence

Raw reports, source copies, probe output, and logs are intentionally kept out of
the versioned tree. The local archive `v1-readiness-2026-10-30.tar.gz` contains
`docs/audits/v1-readiness/`, including `ROADMAP.md`, `findings/work-items.json`,
`findings/consolidated.json`, reproductions, and validation records. Its adjacent
manifest records the source commit, SHA-256, and verification of every archived
file and symbolic link against Git blobs. Preserve both files outside the repo.

The recovery archive has SHA-256
`bc5e8efdb29a558f28b0a27a124191b31a5283c4e2b86ff81d896de0a1b0aee8`.
The original local stash is `51fd1c976c84ada21e0d03cbe9c471004257107f`; a
stash is local recovery state and is not distributed with the repository.
