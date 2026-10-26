# Entrypoints

An entrypoint is a function where outside data enters the program, such as an
HTTP handler, a CLI `main`, or a public contract function. Trailmark tags each
entrypoint with three values:

| Field | Values |
| --- | --- |
| `kind` | `user_input`, `api`, `database`, `file_system`, `third_party` |
| `trust_level` | `untrusted_external`, `semi_trusted_external`, `trusted_internal` |
| `asset_value` | `high`, `medium`, `low` |

Entrypoints feed `attack_surface()`, `entrypoint_paths_to()`, the `data-flow`
diagram, `trailmark diff`, and the preanalysis passes. `QueryEngine.from_directory()`
runs detection automatically; `parse_directory()` and `QueryEngine.from_graph()`
do not.

```bash
trailmark entrypoints --language auto path/to/project
```

## Detection layers

Detection runs four layers. When two layers tag the same function, the later
one wins.

1. **Any function or method named `main`**, in any language. Tagged
   `user_input`, `trusted_internal`, `low`.
2. **Framework-aware patterns**: decorators, attributes, visibility, and
   parser metadata (see the table below).
3. **`pyproject.toml` `[project.scripts]` targets.** Tagged `user_input`,
   `untrusted_external`, `medium`.
4. **`.trailmark/entrypoints.toml`**, which you write. It always wins.

## Framework coverage

| Language | Detected |
| --- | --- |
| Python | Flask, FastAPI, aiohttp, Click, Typer, Celery |
| JavaScript / TypeScript | NestJS, Next.js (App Router and Pages API), AWS Lambda |
| Java | Spring MVC / WebFlux, JAX-RS, Kafka listeners, servlets |
| Kotlin | Spring MVC / WebFlux annotations, Android component lifecycle methods (`onCreate`, `onReceive`, `onBind`, ...) |
| C# | ASP.NET Core, Azure Functions |
| PHP | Symfony `#[Route]` attributes and legacy annotations |
| Ruby | Rails controller actions, Sidekiq worker `perform` methods |
| Lua | generic named `main` only; use overrides for chunk execution and host callbacks |
| Go | `http.HandleFunc` / `http.Handle` registrations, gin/chi/echo-style `<router>.GET/POST/...` registrations |
| Rust | actix-web, rocket, FFI exports (`#[no_mangle]`, `pub extern "C"`), async-main attributes |
| Zig | `export fn` declarations; `main` uses the generic CLI rule |
| C / C++ | `extern "C"` linkage, `__attribute__((visibility("default")))`, `__declspec(dllexport)` |
| Swift | Explicit `static` or `class` `main` methods declared inside `@main` types |
| Objective-C | `UIApplicationDelegate` lifecycle selectors such as `application:openURL:options:` |
| Dart | `@pragma('vm:entry-point')` |
| Haskell | top-level `main` |
| Erlang | functions listed in `-export([...])` |
| Solidity | `public` and `external` functions, `fallback`, and `receive` (see below) |
| Aiken | validator `mint`, `spend`, `withdraw`, `publish`, `vote`, `propose`, and explicit `else` handlers |
| Cairo / StarkNet | `#[external]`, `#[view]`, `#[l1_handler]`, `#[constructor]` |
| Circom | `component main`, represented as a synthetic `main` function tagged `user_input`, `untrusted_external`, `high` |
| Miden Assembly | `begin` program blocks and `pub proc <name>` procedures |
| Move | `public` and `entry` functions |
| Tact | contract `init`, `receive`, `external`, and `bounced` handlers |
| Func | `recv_internal`, `recv_external`, `run_ticktock`, and functions declared with `method_id` |
| Sway | `pub fn` functions and ABI methods |
| Proto | service RPCs |
| Thrift | service functions |
| GraphQL | root operation fields |
| Rego | `allow`, `deny`, and `violation` rules |

SQL has no entrypoint detection.

Decorator and attribute detectors use the source syntax tree to associate
markers with their declarations. Attributes on neighbouring functions,
enclosing types, or nested declarations do not tag the function being checked.
Swift `@main` applies to the owning type's explicit `static` or `class` `main`
method. Entry methods supplied by an extension or inherited from SwiftUI,
and Vapor route registration, are not detected.
Use the override file for handlers that are not covered by these patterns.

[entrypoint-patterns.md](entrypoint-patterns.md) is a reference for detector
authors: markers, suggested classifications, and grep-ready patterns, including
frameworks that are not detected yet (Express, Koa, Fastify, Laravel,
Cobra, axum, warp, clap, and others).

## Endpoint registrations

`attack_surface()` remains a generic node/tag view. Use `endpoint_inventory()`
or `trailmark endpoints` for registration identity. The inventory has
`schema_version: 1`, a complete `registrations` list and registration
`diagnostics`. HTTP routes, GraphQL operations, CLI commands and task handlers
are separate `kind` values; count these kinds separately. `template`
records describe executable PHP templates and layout references. `entrypoint`
records retain other existing source entrypoints from every supported language,
with unknown transport until framework-specific registration evidence is available.
This preserves FFI exports, contract methods, lifecycle callbacks and other
non-HTTP entrypoints without interpreting them as HTTP routes.

Each `EndpointRegistration` includes a deterministic `id`, `owning_root`,
`framework`, service `namespace`, `evidence`, `bindings` and `confidence`.
HTTP identity uses `path`, `method` (one method, otherwise null) and `methods`
(the lossless list). GraphQL identity uses `operation_type` and
`operation_name`; `declaration_nodes` identifies schema declarations, while
`bindings` identifies implementations. `transport_registrations` retains
explicit transport associations. An empty association does not prove execution
is impossible. Subscriptions do not inherit HTTP query/mutation transports.

One handler may have several registration IDs. A registration may have several
bindings, each with its own symbol, node ID, source range and status:
`resolved`, `unresolved`, `out_of_root`, `unsupported` or `ambiguous`.
Bindings use exact qualified identities or supplied file/definition evidence,
never terminal-name matching alone. Native decorator-line evidence can differ
from a function's definition line; both ranges must remain attributable.

Unknown metadata is null. `enabled` remains null for static declarations:
source presence does not establish deployment. Explicit native registration
snapshots can establish activation. `declared_enabled` separately describes
source module configuration and does not prove runtime activation. ACL
references in `acl` are authorization metadata, not authentication labels.

Evidence distinguishes `static`, `native` and `manual` sources. Native inputs
are deliberately supplied snapshots; ordinary scanning never starts or imports
an application. `import_native_registrations()` accepts Django URL resolver
exports (`routes`), Symfony `debug:router` JSON, Magento canonical HTTP registry
rows and Medusa loader snapshots (`routes`). Supplied container-to-source prefix
maps are explicit inputs. Compiled JavaScript paths are not silently mapped to
TypeScript implementations. Native rows and static declarations remain separate
records, distinguishing observed runtime registrations from source declarations.

Supported static patterns and limits:

- Python HTTP decorators require lexical Flask/FastAPI/aiohttp/Sanic receiver
  evidence, including import aliases, assignment aliases and simple application
  factories. `mock.patch`, unrelated `get`/`patch` objects and parameter shadows
  do not establish HTTP registrations. Ambiguous receivers produce diagnostics.
  Conditional/dynamic factories and cross-file registrar exports remain limited.
- Django supports literal `urlpatterns`, nested `include()` prefixes and
  namespaces, shared handlers and `as_view()` class/dispatch bindings. Dynamic
  application factories, settings-driven URL generation and inherited dispatch
  may require a native snapshot. Regex patterns remain Django patterns, and
  function-view method restrictions remain unknown when not supplied.
- Symfony supports selected YAML/XML route imports, prefixes, explicit routes
  and class/service controller bindings, including literal `set(id, Class::class)`
  aliases from typed PHP container configurators. API Platform explicit operation paths
  are retained; dynamically generated resource operations and unresolved
  providers/processors remain diagnostics or unresolved bindings.
- Magento supports `module.xml`, source module enablement configuration,
  `routes.xml`, `webapi.xml`, global DI preferences, controller HTTP marker
  interfaces and ACL references. Secret admin prefixes, rewrites, area-specific
  DI overrides, module ordering conflicts and inherited controllers require
  additional evidence. REST declarations retain their registered `/V1/...`
  paths; a native snapshot supplies deployment prefixes such as `/rest/default`.
- Medusa file routes retain HTTP verb/path bindings and Medusa provenance.
  Generic `route.ts` files without framework evidence retain unknown framework
  identity. Static declarations do not distinguish active routes from loader
  exclusions or instrumentation fixtures by absence from source alone.
  Next.js labels require an actual Next import or a matching app/pages route
  under a package that declares Next as a dependency.
- PHP `.phtml` templates are discovered in selected theme and module roots,
  including Magento `app/design` and `view/*/templates`. Template execution
  bodies retain calls, including database calls, with source locations. Magento
  layout references retain candidate bindings; competing module/theme overrides
  are ambiguous until selection is established. Template presence never marks
  an HTTP endpoint or proves rendering, deployment or authentication. Explicit
  dependency theme roots use the same source policy as application code.
- GraphQL discovers `.graphql`, `.gql` and `.graphqls`, merges operation
  fragments and preserves Vendure Shop/Admin namespaces. Resolver links require
  Nest module/provider/decorator wiring, registered Graphene schema field
  ownership or Magento `@resolver` directives. Conflicting implementations stay
  ambiguous. Explicit Vendure plugin extensions can reference literal
  `graphql-tag` schemas and resolver arrays in selected roots. Dynamic schema
  interpolation and inherited/dynamic resolver wiring may remain unresolved.

Resolved non-template implementations found by automatic adapters or native
imports also appear in the generic attack surface.
Schema declarations alone prove neither resolver execution nor authentication.

### Solidity

Solidity detection uses parser metadata instead of matching signatures:

- Interfaces are excluded.
- `public` and `external` functions are entrypoints, including `view` and
  `pure` functions and bodyless functions declared in abstract contracts.
  `attack_surface()` returns their `solidity_visibility` and
  `solidity_mutability` attributes, so you can separate read-only exposure.
- `fallback` and `receive` are entrypoints regardless of visibility.
- When a derived contract overrides a function, the base implementation stays
  an entrypoint and gains a `solidity_overridden_by` attribute rather than being
  removed. Filter on it to hide overridden bases.

`attack_surface()` includes any parser attributes attached to an entrypoint's
node, not just Solidity's.

## `.trailmark/entrypoints.toml`

Use this file for anything the heuristics miss or misclassify. Each
`[[entrypoint]]` table either names one node or describes a rule that matches
many functions.

```toml
# One node, by node ID (or "module.path:function" for Python)
[[entrypoint]]
node = "my_module:handle_request"
kind = "api"
trust = "untrusted_external"
asset_value = "high"
description = "HTTP POST /auth"

# Every function in PHP files under public_html/
[[entrypoint]]
file_glob = "public_html/**/*.php"
kind = "user_input"
trust = "untrusted_external"
asset_value = "high"
description = "Web-exposed PHP script"

# Every function with a parameter of this declared type
[[entrypoint]]
param_type = "ServerRequestInterface"
kind = "api"
description = "PSR-7 HTTP handler"

# Every function whose name matches a regular expression
[[entrypoint]]
name_regex = "^handle_"
kind = "api"

# Conditions in one table are combined with AND
[[entrypoint]]
file_glob = "public/*.py"
name_regex = "^handle_"
kind = "api"
```

| Key | Meaning | Default |
| --- | --- | --- |
| `node` | A node ID. For Python files, a `module.path:function` reference as in `[project.scripts]` also works | |
| `file_glob` | Glob over source paths; `**` matches any number of directories, `*` and `?` stay within one | |
| `param_type` | Exact name of a declared parameter type | |
| `name_regex` | Python regular expression searched in the function name | |
| `kind` | Entrypoint kind | `user_input` |
| `trust` | Trust level | `untrusted_external` |
| `asset_value` | Asset value | `medium` |
| `description` | Free text shown in reports | none |

Rules (`file_glob`, `param_type`, `name_regex`) match only functions and
methods. File globs match slash-bounded suffixes of paths relative to the
configuration root, so `handlers/*.py` and `*.py` both match
`src/handlers/api.py`. Directory names above the configuration root never
affect a match. When two entries tag the same node, the later entry
wins, so put broad rules first and specific corrections after them.

**Where the file is read from.** Trailmark walks up from the analyzed directory
and uses the first directory that contains either `pyproject.toml` or
`.trailmark/entrypoints.toml` as the configuration root. In a repository with
several `pyproject.toml` files, put the override file next to the one closest to
the directory you analyze.

**Invalid entries are skipped.** Malformed TOML, unknown `kind`, `trust`, or
`asset_value` values, and invalid regular expressions cause the entry (or, for
malformed TOML, the whole file) to be skipped silently. A field with the wrong
TOML type also skips the whole entry, so an invalid condition cannot broaden a
rule. Explicit `node` references that match nothing are skipped with a warning.
Run `trailmark entrypoints` after editing the file to confirm the result.

VB.NET: explicit Module/Shared Main methods with the supported CLI signature
are detected case-insensitively; instance Main and generated framework startup
methods require overrides. See [VB.NET](languages.md#vbnet).

An explicit override `node` key that matches no declaration emits a warning.
Rebuild overrides after parser identity changes; see [skills compatibility](skills-compatibility.md).
