# Call resolution

Trailmark resolves calls by name and scope, not by type checking. Calls it
cannot resolve are kept as edges to `proxy.unresolved:` nodes (see
[Proxy nodes](graph-model.md#proxy-nodes)). This page covers how to measure
those gaps and what they mean for reachability results.

## The report

```bash
trailmark analyze --language rust --resolution path/to/project
```

For this file:

```rust
struct Engine;
impl Engine {
    fn execute(&self) {}
}

fn make() -> Result<Engine, ()> { Ok(Engine) }

fn handle(engine: &Engine, value: Option<u8>) {
    value.unwrap();
    value.unwrap();
    engine.execute();
    helper();
}

fn helper() {}
```

the report is:

```json
{
  "call_sites": 5,
  "resolved_call_sites": 1,
  "unresolved_call_sites": 4,
  "unresolved_targets": 3,
  "affected_callers": 2,
  "unresolved_by_callee": [
    {"callee": "unwrap", "call_sites": 2, "targets": 1},
    {"callee": "Ok", "call_sites": 1, "targets": 1},
    {"callee": "execute", "call_sites": 1, "targets": 1}
  ]
}
```

| Field | Meaning |
| --- | --- |
| `call_sites` | Every `calls` edge: extracted call expressions, plus any `calls` links from `.trailmark/links.toml` and imported binary calls. Repeated calls count separately. |
| `resolved_call_sites` | Calls whose target is not an unresolved proxy: a declaration (here, `helper()`), or an external proxy from a declared link or binary import |
| `unresolved_call_sites` | Calls that point to a `proxy.unresolved:` node |
| `unresolved_targets` | Distinct unresolved proxy nodes |
| `affected_callers` | Functions with at least one unresolved call |
| `unresolved_by_callee` | Unresolved calls grouped by the final name in the call expression, sorted by call-site count (descending), then name |

`engine.execute()` is unresolved even though `engine` is declared `&Engine`,
because Trailmark does not use parameter annotations to infer receiver types.
The `unwrap` group is a syntactic label: it does not identify which `unwrap`
implementation is called. Calls to proxies that were explicitly declared
external in `.trailmark/links.toml` are not counted as unresolved.

## Inspecting call sites

```python
engine = QueryEngine.from_directory("./project", language="rust")
engine.call_resolution()            # the report above
engine.unresolved_calls()           # every unresolved call site
engine.unresolved_calls("handle")   # only calls made directly by handle
```

Each row has the caller (`source`), the proxy ID (`target`), the raw call text
(`raw_target`), the final name (`callee`), the edge `confidence`, and the call
site's `location` (or `None` if unavailable). A proxy node's own location is
only its first occurrence; use each row's `location` when a call repeats. A
name that matches no function returns `[]`.

The report and `unresolved_calls()` work for every supported language.

## What unresolved calls mean for results

A proxy marks where Trailmark stopped following a path. Reachability queries,
entrypoint paths, and preanalysis cannot continue past it to the real
implementation, so effects of that implementation are missing from their
results. In the README example, `handle_request` does not appear in
`ancestors_of("Auth._check_sig")` for this reason.

To close a gap you have reviewed:

- declare the missing edge in [`.trailmark/links.toml`](links.md), or
- import an externally produced call graph with
  [`augment_binary()`](augmenting.md#binary-call-graphs) when that is the
  better source.

Hiding common proxy names, such as `unwrap`, does not restore the missing paths.

## What the report does not measure

The report counts the calls Trailmark extracted, not overall program coverage.
Code inside macros that are not expanded and implicit calls such as destructors
produce no call sites, so they are outside its counts. Zero unresolved calls is
not proof that the graph contains every executable path. A large number of
proxies does not mean calls were dropped; each one is a call that was kept but
not resolved.

## Rust limits

Rust code commonly produces many proxies. Common patterns:

| Pattern | Behavior |
| --- | --- |
| Standard methods: `.unwrap()`, `.expect()`, `.clone()`, `.len()` | Calls are kept; receiver types and dependency implementations are not inferred |
| Iterator chains: `.iter().map(...).collect()` | Each call is kept; intermediate result types are not propagated |
| Trait conversions: `.into()`, `.try_into()`, `TokenId::try_from()` | Trait implementations and generated methods may stay unresolved |
| External crates: `UdpSocket::bind()`, `prost_build::Config::new()` | Dependency source is not loaded |
| Generated builders: `Action::builder().timestamp(...)` | Calls are kept; the macro that generates the builder is not expanded |
| Local value receivers: `engine.execute()`, `self.side.converse()` | Receiver types are not inferred, even from explicit parameter annotations |
| Enum constructors: `ActionKind::Deposit(1)`, `Ok(...)`, `Err(...)` | Parenthesized constructors are calls and can stay proxies; a bare variant value is not a call |

Declared return types are recorded but do not resolve later calls on the
returned value. Given `make() -> Result<Engine, ()>`, the chain
`make().unwrap().execute()` records three calls, to `make`, `unwrap`, and
`execute`, but does not link `execute` to `Engine.execute`.

See [Rust and Verus](languages.md#rust-and-verus) for the scoping rules that do
resolve.
