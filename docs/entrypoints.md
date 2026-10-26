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

## Evidence and candidates

`QueryEngine.entrypoint_evidence()` reports each discovery observation with its
source span, target binding, rule, basis, and unresolved facts. Multiple source
occurrences can reference the same handler. `entrypoint_candidates()` returns
observations whose targets are outside the reported attack surface, including
unresolved references with no target node. Both accept an optional node name.

```bash
trailmark entrypoints --language auto --evidence path/to/project
trailmark entrypoints --language auto --candidates path/to/project
```

These JSON outputs expose evidence separately from the existing entrypoint tags.
Candidate discovery never promotes a reference to an entrypoint. A `resolved`
binding identifies a source declaration; external invocation can still be
unknown. Ordinary call paths follow `calls` edges, so `references` edges do not
establish execution or authentication. The evidence model and source-selection
contract apply to every supported frontend; automatic reference extraction is
currently bounded to the forms described below.

- Python decorator lookup follows imports, aliases, enclosing bindings and
  literal path constants. Unrelated key-based decorators and provable local
  identity decorators are excluded. Existing HTTP method conventions retain
  their heuristic tags for unknown arguments and conditional bindings, with
  the uncertainty recorded in evidence. Even a matching path is a heuristic:
  registrar semantics and runtime activation remain unknown. Binding analysis
  does not model arbitrary dynamic writes or monkeypatches from other files.
- ECMAScript file-route conventions require an exported module-level callable.
  Aliases use the exported name; default and CommonJS exports retain their
  source occurrences. Call wrappers record `export_value` uncertainty.
  Their descriptions identify the convention without assigning a framework.
- Python and PHP callable arguments and collection values retain references,
  including supported aliases, imports and statically qualified members.
  Dynamic member dispatch stays unresolved. Other frontends keep their existing
  entrypoints and the common evidence API; automatic callable-value extraction
  is not yet implemented for them.
- Executable file bodies, including PHP `.phtml` templates, are candidates with
  unknown invocation context. Their direct calls remain queryable. Anchored PHP
  includes can link to inspected executable bodies through normal call edges.
- Qualified symbol and file references in GraphQL values and selected
  JSON, YAML, XML and TOML values are matched against inspected declarations.
  Keys, comments and descriptions are excluded. Multiple exact qualified matches
  stay ambiguous; terminal names alone never bind a declaration.
  Relative `./` and `../` file references use their declaring file's directory;
  suffix-only file matches retain `target_selection` uncertainty. Schema or
  configuration references do not identify the runtime method a loader invokes.
  Invalid or unsupported file syntax and unreadable/oversized files are reported
  as unresolved scan evidence.

Unmatched qualified strings are retained, so ordinary metadata such as hostnames
can also appear among unresolved candidates. Review their consumer before
treating them as code references. Data files with matched references gain a
synthetic module node for provenance, which is included in graph node counts.

The data reader accepts UTF-8 files up to 8 MiB. Unsupported literal forms,
including custom XML entities and YAML block indentation indicators, are not
resolved. It never expands custom or external XML entities,
loads objects from YAML tags, or executes application code. Candidate strings may
be ordinary data; reviewing their consumer semantics is required.

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
| JavaScript / TypeScript | NestJS, exported HTTP-method/file-route conventions, AWS Lambda |
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
frameworks that are not detected yet (Express, Koa, Fastify, Django, Laravel,
Cobra, axum, warp, clap, and others).

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
