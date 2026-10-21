# Cross-language and external links

Some relationships don't appear in source syntax: a backend calling a contract
over JSON-RPC, Python loading a Rust library through FFI, a service invoking a
webhook, or a process spawning another. Polyglot parsing merges each
language's graph but can't infer these edges. Declare them in
`.trailmark/links.toml`. Links with the default kind, `calls`, are followed by
reachability and path queries like any other call; links of other kinds are
recorded but not traversed by those queries.

```toml
[[link]]
source = "backend:submit"
target = "contract:Verifier.verify"
kind = "calls"                 # default: calls
confidence = "certain"         # default: inferred
description = "JSON-RPC eth_call"

[[link]]
source = "backend:notify"
target = "payments-webhook"
target_external = true         # required: no node named payments-webhook exists
```

| Key | Required | Meaning |
| --- | --- | --- |
| `source` | yes | Node ID, or a name or `:`/`.` suffix that matches exactly one node |
| `target` | yes | Same as `source` |
| `kind` | no | Any [edge kind](graph-model.md#edge-kinds); default `calls` |
| `confidence` | no | `certain`, `inferred`, or `uncertain`; default `inferred` |
| `description` | no | Stored on the edge as an attribute |
| `source_external` | no | `true` allows `source` to match nothing and creates a proxy for it |
| `target_external` | no | `true` allows `target` to match nothing and creates a proxy for it |

With `source_external = true` or `target_external = true`, an endpoint that
matches nothing becomes a `proxy.external:<name>` node. If it does match a node,
the link uses that node.

## Validation

This file is strict. Trailmark raises `ValueError` and does not build a graph
for any of these:

- malformed TOML, or a top-level key other than `link`
- an unknown key in a `[[link]]` table
- a missing or empty `source` or `target`
- an endpoint that matches no node and is not marked external
- an endpoint that matches more than one node; unlike query lookup, links do
  not rank matches, so use a full node ID
- an invalid `kind` or `confidence` value

## Where the file is read from

Trailmark reads `.trailmark/links.toml` from the root of the directory being
parsed, for both single-language and polyglot directory parses. It does not
search parent directories. `parse_file()` does not apply links.

The file format is a stable public interface.
