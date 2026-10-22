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

Each finding below links to its regression suite and minimized source inputs.
The named tests in the linked suite assert the language’s combined cases.

| Finding | Regression suite | Fixtures |
| --- | --- | --- |
| PY-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | Inline cases in suite |
| PY-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | Inline cases in suite |
| PY-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | Inline cases in suite |
| R-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [r](r/) |
| R-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [r](r/) |
| R-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [r](r/) |
| JS-001 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [javascript](javascript/) |
| JS-002 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [javascript](javascript/) |
| JS-003 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [javascript](javascript/) |
| TS-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [typescript](typescript/) |
| TS-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [typescript](typescript/) |
| TS-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [typescript](typescript/) |
| PHP-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [php](php/) |
| PHP-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [php](php/) |
| PHP-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [php](php/) |
| PERL-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [perl](perl/) |
| PERL-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [perl](perl/) |
| PERL-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [perl](perl/) |
| RUBY-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [ruby](ruby/) |
| RUBY-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [ruby](ruby/) |
| RUBY-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [ruby](ruby/) |
| LUA-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [lua](lua/) |
| LUA-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [lua](lua/) |
| C-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [c](c/) |
| C-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [c](c/) |
| C-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [c](c/) |
| BASH-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [bash](bash/) |
| BASH-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [bash](bash/) |
| BASH-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [bash](bash/) |
| CPP-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [cpp](cpp/) |
| CPP-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [cpp](cpp/) |
| CPP-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [cpp](cpp/) |
| CS-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [c_sharp](c_sharp/) |
| CS-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [c_sharp](c_sharp/) |
| CS-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [c_sharp](c_sharp/) |
| POWERSHELL-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [powershell](powershell/) |
| POWERSHELL-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [powershell](powershell/) |
| POWERSHELL-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [powershell](powershell/) |
| JAVA-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [java](java/) |
| JAVA-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [java](java/) |
| JAVA-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [java](java/) |
| VB-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [vb](vb/) |
| VB-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [vb](vb/) |
| VB-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [vb](vb/) |
| GO-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [go](go/) |
| GO-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [go](go/) |
| GO-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [go](go/) |
| RUST-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [rust](rust/) |
| RUST-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [rust](rust/) |
| RUST-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [rust](rust/) |
| ZIG-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [zig](zig/) |
| ZIG-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [zig](zig/) |
| ZIG-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [zig](zig/) |
| SOL-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [solidity](solidity/) |
| SOL-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [solidity](solidity/) |
| SOL-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [solidity](solidity/) |
| AIKEN-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [aiken](aiken/) |
| AIKEN-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [aiken](aiken/) |
| AIKEN-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [aiken](aiken/) |
| CAIRO-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [cairo](cairo/) |
| CAIRO-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [cairo](cairo/) |
| CAIRO-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [cairo](cairo/) |
| CIRCOM-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [circom](circom/) |
| CIRCOM-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [circom](circom/) |
| CIRCOM-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [circom](circom/) |
| HASKELL-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [haskell](haskell/) |
| HASKELL-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [haskell](haskell/) |
| HASKELL-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [haskell](haskell/) |
| JULIA-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [julia](julia/) |
| JULIA-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [julia](julia/) |
| JULIA-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [julia](julia/) |
| ERLANG-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [erlang](erlang/) |
| ERLANG-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [erlang](erlang/) |
| ERLANG-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [erlang](erlang/) |
| MASM-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [masm](masm/) |
| MASM-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [masm](masm/) |
| MASM-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [masm](masm/) |
| SWIFT-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [swift](swift/) |
| SWIFT-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [swift](swift/) |
| SWIFT-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [swift](swift/) |
| OBJC-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [objc](objc/) |
| OBJC-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [objc](objc/) |
| OBJC-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [objc](objc/) |
| KOTLIN-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [kotlin](kotlin/) |
| KOTLIN-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [kotlin](kotlin/) |
| KOTLIN-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [kotlin](kotlin/) |
| DART-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [dart](dart/) |
| DART-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [dart](dart/) |
| DART-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [dart](dart/) |
| MOVE-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [move](move/) |
| MOVE-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [move](move/) |
| MOVE-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [move](move/) |
| TACT-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [tact](tact/) |
| TACT-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [tact](tact/) |
| TACT-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [tact](tact/) |
| FUNC-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [func](func/) |
| FUNC-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [func](func/) |
| FUNC-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [func](func/) |
| SWAY-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [sway](sway/) |
| SWAY-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [sway](sway/) |
| REGO-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [rego](rego/) |
| REGO-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [rego](rego/) |
| REGO-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [rego](rego/) |
| PROTO-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [proto](proto/) |
| PROTO-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [proto](proto/) |
| PROTO-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [proto](proto/) |
| THRIFT-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [thrift](thrift/) |
| THRIFT-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [thrift](thrift/) |
| THRIFT-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [thrift](thrift/) |
| GRAPHQL-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [graphql](graphql/) |
| GRAPHQL-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [graphql](graphql/) |
| GRAPHQL-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [graphql](graphql/) |
| SQL-01 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [sql](sql/) |
| SQL-02 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [sql](sql/) |
| SQL-03 | [test_audit_regressions.py](../../../tests/test_audit_regressions.py) | [sql](sql/) |
