# Commerce registration and execution contracts — 2026-10-26

Trailmark now exports typed registrations, explicit source policies and reviewed
execution facts alongside its existing generic attack surface. The changes address
TM-01 through TM-07 with bounded framework adapters and explicit unresolved results.
Custom PHP theme bodies are included, and common contracts preserve existing
entrypoints across all 39 supported frontends. This is not a complete deployment
registry or an automatic authentication classifier.

## Evidence and measurement policy

The original private evaluation used Trailmark 0.7.0. The implementation branch
started at `bbd8e7d`; the reduced Python decorator, GraphQL-link, missing API and
PHP grammar issues were reproduced there before implementation. The retained
platform snapshots, frozen installation and baseline artifacts were read only.
Reproducers and the structural mapper wrapper were copied to a separate scratch
directory before execution. No application was started or imported by these scans.

[Aggregate measurements](commerce-smoke-2026-10-26.json) contain baseline and final
counts, relative root identities and evidence hashes. Private absolute paths,
registry payloads and platform source are omitted. Baseline counts come from the
retained `run-1/summary-api.json` and `attack-surface-full.json`; final counts come
from `tests/commerce_smoke.py`. Static results are captured **before** optional
native snapshot imports. Source handlers, registration rows, method/path pairs and
GraphQL identities are separate units throughout.

## TM-01…TM-07 completion matrix

| Item | Delivered and validated | Remaining limits |
| --- | --- | --- |
| TM-01 | Static Django URLconf/includes/namespaces/CBV bindings; Symfony YAML/XML imports and explicit API Platform operations; Magento modules/routes/REST/DI/ACL; explicit native imports. All 164 in-root Oscar native rows resolve, with 385 out of root. | Dynamic Oscar URL factories require the native snapshot. Sylius retains 604 unresolved native bindings, including generated resource/service dispatch. Magento retains 160 unresolved native bindings; area DI, module ordering and inherited controllers are not fully modeled. |
| TM-02 | Receiver evidence replaces suffix-only Python HTTP matching. All 812 Saleor false HTTP tags disappear; schema and task entrypoints remain. Aliases, unrelated receivers, shadows, factories and valid frameworks have focused tests. | Conditional/dynamic factories and cross-file registrar exports can remain unknown. Tests are not excluded to obtain the result. |
| TM-03 | `.graphqls` discovery/extension merging; namespace-specific Nest/Vendure, Graphene and Magento resolver associations; separate transport evidence; selected Vendure plugin support. All retained native operation identities are represented when the plugin root is selected. | Resolver binding is partial: Saleor 318, Vendure core 9 and Magento 5 operation bindings remain unresolved. Dynamic/inherited wiring and subscription activation are not inferred. |
| TM-04 | Ordered source roots, includes/excludes/auxiliary evidence, canonical deduplication, root ownership and per-file reasons. Sylius excludes all 8,163 formerly scanned generated cache source files. | The caller must select dependency/plugin/theme roots. Dynamic deployment composition is outside source selection. |
| TM-05 | Versioned registrations and CLI/JSON export; HTTP/GraphQL/CLI/task/template/other-entrypoint separation; multiple registrations per handler; static/native/manual provenance and explicit unknowns. All 491 Medusa core handler detections and nine CLI entries remain. | Static presence does not establish enabled state. Native compiled JS paths are not silently mapped to TypeScript. Unsupported framework metadata remains null. |
| TM-06 | Source-ranged gate, credential, rejection, success, middleware and dispatch facts; bounded query; nonempty roots; mixed-path evidence; no identity from CSRF/capability/trust tags. | Facts must be explicitly supplied and reviewed. All commerce scans have zero reviewed facts and therefore unknown authentication. No universal automatic auth classification is claimed. |
| TM-07 | Equal-length parser-input recovery for boolean-named PHP class constants. The reduced fixture and real Magento Config file each go from two diagnostics to zero; source bytes and neighboring method ranges are unchanged. Invalid syntax still produces diagnostics. | Typed/comma-separated constant variants and unrelated grammar gaps remain outside this narrow recovery. Upstream PHP grammar 0.25.1 reproduced the issue, so this is an integration fix. |

## Source and generic entrypoint counts

These generic counts are not HTTP endpoint counts. The final registration column
is automatic static discovery; native imports are shown separately below.

| Selected root | Baseline files → final files | Baseline generic entrypoints → final | Final static registration kinds |
| --- | ---: | ---: | --- |
| Medusa app | 10 → 10 | 2 → 2 | HTTP 2 |
| Medusa framework | 826 → 826 | 500 → 500 | HTTP 491; CLI 9 |
| Oscar app | 53 → 163 | 0 → 0 | HTTP 5, unresolved |
| Oscar framework | 544 → 544 | 0 → 0 | HTTP 1, unresolved factory |
| Sylius app | 8,255 → 92 | 0 → 0 | HTTP 2 |
| Sylius framework | 4,104 → 4,104 | 0 → 32 | HTTP 341 |
| Saleor | 4,405 → 4,405 | 1,381 → 716 | GraphQL 458; HTTP 7; tasks 109; CLI 2 |
| Vendure app | 5 → 5 | 0 → 0 | None in selected app source |
| Vendure core | 1,125 → 1,126 | 332 → 641 | GraphQL 320; HTTP transports 2; other entrypoints 16; template 1 |
| Magento | 28,046 → 27,976 | 0 → 1,434 | HTTP 1,719; GraphQL 108; templates/references 2,073 |

Oscar's retained app snapshot contains 150 JavaScript assets versus 40 in its
baseline capture. Replaying the old walk on the retained snapshot also selects
163 files. That difference, including two JavaScript parse diagnostics, is a
snapshot/capture mismatch, not improved discovery. Vendure's additional file is
its existing `e2e/fixtures/assets/malicious.phtml`, now recognized as PHP.

Saleor's 716 generic records comprise 458 schema declarations, 109 tasks, two CLI
entries, seven Django bindings and 140 Graphene resolver bindings. The removal
of 812 false Python HTTP tags is not obscured by the newly represented resolvers.
Magento's net file change combines generated/cache exclusions with newly selected
`.graphqls` and `.phtml` files; it is not a loss-of-coverage percentage.

## Native HTTP registration reconciliation

Native snapshots are explicit evidence inputs, not automatic detector results.
Resolved **binding rows** and unique represented **source handlers** differ when
several routes share an implementation.

| Root | Native rows | Resolved bindings | Unresolved | Out of root | Unique represented handlers |
| --- | ---: | ---: | ---: | ---: | ---: |
| Oscar framework | 549 | 164 | 0 | 385 | 151 |
| Oscar app | 549 | 0 | 0 | 549 | 0 |
| Sylius framework | 644 | 40 | 604 | 0 | 37 |
| Sylius app | 644 | 0 | 644 | 0 | 0 |
| Saleor | 10 | 9 | 0 | 1 | 9 |
| Magento | 1,385 | 1,225 | 160 | 0 | 1,150 |
| Medusa framework | 466 | 0 | 0 | 466 | 0 |
| Medusa app | 466 | 2 | 464 | 0 | 2 |

Oscar's resolved native handlers gain compatible generic entrypoint tags after
import. The unselected application/framework roots retain their own outcomes.
Sylius app plus the deliberately selected framework root inspects 4,196 source
files once, discovers 343 static registrations and resolves the same 40 native
binding rows to 37 unique handlers. Other installed dependency implementations
are still unselected; generated API Platform/provider wiring remains explicit.

Magento's bounded native census has 348 REST service rows, 1,036 canonical
controller rows and one GraphQL transport. Static REST paths and native deployment
paths have different prefixes and placeholder conventions: 123 literal method/path
intersections are reported, without presenting this as endpoint recall. Secret
admin prefixes, optional aliases, rewrites, SOAP/async expansion and runtime
module/area precedence remain outside complete deployment URL coverage.

Medusa's source core retains 489 framework-backed route declarations, two generic
file-route fixtures and nine CLI entries. Of 489 known static method/path pairs,
464 match active core loader pairs; the retained native analysis identifies the
other 25 declarations as disabled. Static records still have `enabled: null`.
The loader snapshot contains 464 core rows and two app rows; compiled core JS
paths do not resolve to selected TS sources without an explicit crosswalk.
The two custom app handlers resolve. The separate native Express census of 509
unique pairs / 1,195 instances is a different denominator.

Saleor's container/source prefix mapping is supported by the retained runtime
crosswalk; every referenced source hash is verified before applying that mapping.

## GraphQL identities, implementations and transports

| Root | Static operation records | Native identities | Identity intersection | Resolved / unresolved implementation bindings | Operations with transport evidence |
| --- | ---: | ---: | ---: | ---: | ---: |
| Saleor | 458 | 458 | 458 | 140 / 318 | 430 |
| Vendure core | 320 | 317 | 316 | 311 / 9 | 311 |
| Magento | 108 | 98 | 98 | 103 / 5 | 104 |

Vendure core preserves all 265 Admin and 51 Shop SDL fields. Four additional
literal plugin-extension records belong to core test fixtures and are kept
separate from the 316 SDL identities. The runtime-only `dashboardMetricSummary`
lies in `packages/dashboard/plugin`. Selecting that root adds its schema and
`MetricsResolver.dashboardMetricSummary` binding, with plugin ownership and a
core transport association: 321 records across 1,138 source files, including
those four fixture operations. Runtime activation is still unknown from source.

Saleor's 26 subscription fields never inherit the Query/Mutation HTTP transport.
Schema identity and a transport association do not imply that the implementation
resolved. Magento includes six test-module operations plus four unscoped fixture
operations beyond its 98 native identities. The four unscoped operations do not
inherit the Magento transport. Resolver edges use qualified/provider/schema
evidence and `RESOLVES_TO`; ordinary call traversal does not treat them as calls.

## Themes and all-language contracts

Selected custom theme roots participate in the same source policy as application
and dependency roots. PHP `.phtml` files have synthetic execution bodies; top-level
executable `.php` statements have script bodies. Calls, including direct database
calls, preserve source coordinates and nested callable ownership. Standalone SQL,
JS and TS theme files use their respective frontends. Embedded SQL remains a PHP
expression; HTML/embedded JS is not separately parsed as a JavaScript program.

Magento's final scan adds 980 `.phtml` source files and emits 2,073 template-body
and literal layout-reference records. The isolated `app/design` root includes
nine source files (five PHP, four JS), two template bodies and four layout
references, with zero parse diagnostics. References to unselected module templates
remain unresolved. Competing module/theme overrides remain ambiguous; runtime
inheritance, store selection and dynamic `setTemplate()` remain unknown.

A copied structural mapper wrapper was run against an authored custom theme
fixture using this branch's Trailmark. Its graph export retains PHP script/template
bodies, database calls and template registrations alongside JS and SQL sources.
This validates the Trailmark contract consumed by that wrapper; its display cap,
orchestration and installed-version pin are owned by the skills repositories.

`tests/test_registration_language_contracts.py` derives its matrix from the public
language registry and fails if any frontend lacks a fixture. All 39 frontends
exercise explicit dependency roots, overlapping-root deduplication, ownership,
existing entrypoint retention, registration serialization, common execution facts
and fail-closed missing/unknown queries. Explicit manual facts in those contract
tests are test inputs, never automatic commerce detector coverage. Framework
discovery depth remains language/framework-specific, as agreed for this PR.

## Parser diagnostics and validation

The targeted Magento Config file retains 24 graph nodes and its original source
hash `b22cdaf4e1fd8ecb8708702411ad7dbccf524ee4e2cba61bc37dfdcdbebfc116`.
Both it and the reduced fixture pass native PHP 8.5.12 syntax checking and now
produce zero Trailmark diagnostics. Genuinely invalid syntax remains tested.

The complete Magento scan reports 659 diagnostics: 634 GraphQL, four JavaScript
and 21 PHP. Its baseline had 25 total. Newly discovered Magento SDL exposes
existing grammar limitations, including escaped directive-string syntax;
adding templates exposes two further PHP diagnostics while the constant recovery
removes two. These diagnostics are retained, not suppressed. The new discovery
does not guarantee complete parsing of every selected file. Other final counts:
Oscar framework one, Oscar app two, Sylius app five, Saleor six; other measured
roots zero. The aggregate artifact records their language breakdowns.

Validation completed with repository lint/format/type checks, all 3,049 pytest
tests, and the source-distribution smoke check. The reviewed mutation campaign
ran 445 mutants: 432 killed and 13 existing/equivalent survivors; the gate has
zero unreviewed or inconclusive results. The existing private directory-context
mutability exception was re-reviewed and its changed module fingerprint updated.
This is the repository's reviewed mutation scope, not a full mutation campaign
for every new adapter. No remaining pre-existing check failures were observed.

To reproduce with access to the retained evaluation, choose an output directory
outside it and run each root's case independently:

```bash
uv run python -m tests.commerce_smoke "$EVIDENCE_DIR" "$RESULT_DIR" --case saleor/shop --native
```

The driver rejects output beneath the evidence directory. Omit `--native` for
source-only discovery. For combined roots, use `SourcePolicy`/`SourceRoot` as
documented in the [Python API](../python-api.md); the aggregate artifact records
the selected roots. Public regression fixtures require no private workspace.
