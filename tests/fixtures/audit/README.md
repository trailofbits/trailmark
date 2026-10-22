# Pre-release audit fixtures

These minimized source cases came from the Trailmark v0.7 frontend audit of
commit `66b03026363466fd17f63ed07bd193ba1e18b4fa`. They are source inputs,
including intentional grammar-limit controls; do not execute them as programs.
Behavioral assertions live in `tests/test_audit_regressions.py`, supplemented by
the existing parser, entrypoint, identity, and directory-linking suites. Parser
known-answer snapshots are under `tests/fixtures/kat`.

The audit covered 115 frontend findings across 39 languages and four shared-core
items. Grammar limitations are recorded in `docs/parser-coverage.md`; the
pre-release acceptance for those findings was explicit diagnostics or documented
coverage limits. In particular, C header annotation propagation, Aiken and Zig
grammar expansion, Tact trait bodies, and unsupported modern Rego forms remain
limited. This work does not claim complete language or compiler compatibility.
External skills gates require testing in the skills repository; the handoff is
`docs/skills-compatibility.md`. Release preflight does not create a tag or publish.

| Language | Audit findings |
| --- | --- |
| python | PY-01, PY-02, PY-03 |
| r | R-01, R-02, R-03 |
| javascript | JS-001, JS-002, JS-003 |
| typescript | TS-01, TS-02, TS-03 |
| php | PHP-01, PHP-02, PHP-03 |
| perl | PERL-01, PERL-02, PERL-03 |
| ruby | RUBY-01, RUBY-02, RUBY-03 |
| lua | LUA-01, LUA-02 |
| c | C-01, C-02, C-03 |
| bash | BASH-01, BASH-02, BASH-03 |
| cpp | CPP-01, CPP-02, CPP-03 |
| c_sharp | CS-01, CS-02, CS-03 |
| powershell | POWERSHELL-01, POWERSHELL-02, POWERSHELL-03 |
| java | JAVA-01, JAVA-02, JAVA-03 |
| vb | VB-01, VB-02, VB-03 |
| go | GO-01, GO-02, GO-03 |
| rust | RUST-01, RUST-02, RUST-03 |
| zig | ZIG-01, ZIG-02, ZIG-03 |
| solidity | SOL-01, SOL-02, SOL-03 |
| aiken | AIKEN-01, AIKEN-02, AIKEN-03 |
| cairo | CAIRO-01, CAIRO-02, CAIRO-03 |
| circom | CIRCOM-01, CIRCOM-02, CIRCOM-03 |
| haskell | HASKELL-01, HASKELL-02, HASKELL-03 |
| julia | JULIA-01, JULIA-02, JULIA-03 |
| erlang | ERLANG-01, ERLANG-02, ERLANG-03 |
| masm | MASM-01, MASM-02, MASM-03 |
| swift | SWIFT-01, SWIFT-02, SWIFT-03 |
| objc | OBJC-01, OBJC-02, OBJC-03 |
| kotlin | KOTLIN-01, KOTLIN-02, KOTLIN-03 |
| dart | DART-01, DART-02, DART-03 |
| move | MOVE-01, MOVE-02, MOVE-03 |
| tact | TACT-01, TACT-02, TACT-03 |
| func | FUNC-01, FUNC-02, FUNC-03 |
| sway | SWAY-01, SWAY-02 |
| rego | REGO-01, REGO-02, REGO-03 |
| proto | PROTO-01, PROTO-02, PROTO-03 |
| thrift | THRIFT-01, THRIFT-02, THRIFT-03 |
| graphql | GRAPHQL-01, GRAPHQL-02, GRAPHQL-03 |
| sql | SQL-01, SQL-02, SQL-03 |

Core regressions cover indexed/base-relative SARIF locations, structured syntax
and unsupported-syntax diagnostics, strict parsing, valid empty inputs, and
release metadata validation. Run these with `uv run pytest -q tests/`; see
`CONTRIBUTING.md` for lint, type, mutation, and installed-distribution checks.
