# Python API

Trailmark has two entry points:

- `trailmark.parse` returns a raw `CodeGraph` and nothing else.
- `trailmark.query.api.QueryEngine` parses, indexes, detects entrypoints, and
  answers queries. Most users want this one.

## Parsing only

```python
from trailmark.parse import parse_directory, parse_file

graph = parse_file("path/to/file.py")                      # language from the extension
graph = parse_file("path/to/header.h", language="objc")    # or explicit
graph = parse_directory("path/to/project", language="auto")
```

The same functions are exported from the top-level `trailmark` package, along
with:

- `detect_languages(path)` returns the languages that have at least one file
  under `path`, using the same directory-skipping rules as parsing.
- `supported_languages()` returns every accepted `language` key.

`parse_directory()` applies cross-file call linking, type linking, and
`.trailmark/links.toml`, but not entrypoint detection. To get entrypoints, build
a `QueryEngine`.

`parse_directory()` and `QueryEngine.from_directory()` reject missing roots and
paths that are not directories with `ValueError`. They also reject a directory
where no source files were inspected for the selected languages. The default
language is Python; use `language="auto"` or an explicit language list for other
projects. Pass the keyword-only `allow_empty=True` to permit an existing directory
with zero matching files, including an empty `auto` parse. A source file with no
declarations still counts as inspected and does not require this option.

`CodeGraph.source_file_counts` records inspected files per selected language,
including zero counts in explicit language lists. A shared header belongs to
one parser and counts once. `GraphStore.source_file_counts()` returns a copy.
`QueryEngine.summary()` and the exported JSON summary include `languages`,
`source_files` (the total), and `source_file_counts`. Manually assembled graphs
have no coverage metadata unless the caller supplies it. These counts describe
the selected files, not parser completeness or coverage of unselected languages.

## Building a `QueryEngine`

```python
from trailmark.query.api import QueryEngine

engine = QueryEngine.from_directory("path/to/project")                    # Python
engine = QueryEngine.from_directory("path/to/project", language="auto")
engine = QueryEngine.from_directory("path/to/project", language="python,rust")

engine = QueryEngine.from_graph(graph)   # wrap a CodeGraph you already have
```

`QueryEngine.from_graph(graph)` retains the supplied mutable graph and
normalizes its proxy nodes in place. Make any direct node and edge edits before
constructing the engine, then use the engine's methods for later changes.
`from_graph()` does not run entrypoint detection. If you manage a `GraphStore`
yourself, call `rebuild_index()` after direct structural edits.

## Referring to nodes

Every method that takes a `name` accepts a full node ID (`auth:Auth.verify`), a
qualified name (`Auth.verify`), or a bare name (`verify`). Lookup tries an exact
node ID first. It then searches concrete declarations by exact name, `:name`
suffix, then `.name` suffix, before applying those tiers to proxy nodes.

When several nodes match at the same tier, the first one parsed wins; there is
no ambiguity error. Use a full node ID when a name is common, or to query a
proxy node directly. Diagrams use the same ranking.

A name that matches nothing returns `[]` (or `False` for the annotation
mutators) rather than raising.

## Method reference

Results are plain dicts and lists that serialize to JSON. Node results include
the node's `id`, `name`, `kind`, `location`, and parser metadata. Transitive
and state-access queries return nodes ordered by ID. Collections in results,
such as `summary()` dependencies and subgraph members, are copies; changing
them does not change the graph.

### Calls and reachability

| Method | Returns |
| --- | --- |
| `callers_of(name)` | Nodes with a direct call edge to `name` |
| `callees_of(name)` | Nodes `name` calls directly |
| `ancestors_of(name)` | Every node that can transitively call `name` (upward slice) |
| `reachable_from(name)` | Every node `name` can transitively call |
| `paths_between(src, dst, *, max_depth=20)` | Call paths from `src` to `dst`, as lists of node IDs |
| `entrypoint_paths_to(name, max_depth=20)` | Call paths from any detected entrypoint to `name` |

Transitive queries follow `calls` edges only. Imports, containment, state
access, and links declared with a kind other than `calls` do not extend a slice.

Path queries return distinct node-ID sequences. `max_depth` is the maximum edge
count (default 20) and must be positive; zero or a negative value raises
`ValueError`. A path from a node to itself returns `[]`.

### Attack surface and complexity

| Method | Returns |
| --- | --- |
| `attack_surface()` | Each entrypoint's node ID, kind, trust level, asset value, description, and parser attributes such as Solidity visibility |
| `complexity_hotspots(threshold=10)` | Functions with cyclomatic complexity &ge; `threshold` |
| `functions_that_raise(exception_name)` | Functions whose parser-detected exception types include `exception_name` |
| `summary()` | Total nodes; function, class, and proxy counts; call-edge count; dependencies; entrypoint count; selected languages and inspected source-file counts |
| `parse_diagnostics()` | Grammar errors and explicitly unsupported constructs, with language, grammar, source span, kind and message |

### Types

| Method | Returns |
| --- | --- |
| `generic_parameters(name)` | Generic type parameters declared by `name` |
| `type_references(name)` | Type references in `name`'s parameters, return type, raised exceptions, and generic bounds |

### Persistent state (Solidity)

| Method | Returns |
| --- | --- |
| `state_read_by(name)` | State variables `name` reads directly |
| `state_written_by(name)` | State variables `name` writes directly |
| `readers_of(name)` | Functions, constructors, and modifiers that read state variable `name` |
| `writers_of(name)` | Functions, constructors, and modifiers that write state variable `name` |

Each returns distinct nodes ordered by ID. See
[Solidity persistent state](languages.md#solidity-persistent-state) for what
counts as a read or write.

### Annotations and findings

| Method | Returns |
| --- | --- |
| `annotate(name, kind, description, source="manual")` | `True` if the annotation was added |
| `annotations_of(name, kind=None)` | Annotations on `name`, optionally filtered by `AnnotationKind` |
| `clear_annotations(name, kind=None)` | `True` if the node exists, whether or not anything was removed |
| `nodes_with_annotation(kind)` | Nodes carrying an annotation of `kind` |
| `findings(kind=None)` | Nodes with `finding` or `audit_note` annotations (or only `kind`), each with its matching annotations |
| `preanalysis()` | A summary of each pass; results are stored as annotations and subgraphs |
| `augment_sarif(sarif_path, *, replace_existing=True)` | Match counts for a SARIF import |
| `augment_weaudit(weaudit_path, *, replace_existing=True)` | Match counts for a weAudit import |
| `augment_binary(graph_path, *, connect_sources=True)` | Counts of imported binary nodes, edges, and source correspondences |

```python
from trailmark.models.annotations import AnnotationKind

engine.annotate(
    "handle_request",
    AnnotationKind.ASSUMPTION,
    "Caller has already authenticated the session token",
    source="llm",
)
engine.annotations_of("handle_request")
engine.nodes_with_annotation(AnnotationKind.FINDING)
```

Annotation kinds are `assumption`, `precondition`, `postcondition`,
`invariant`, `blast_radius`, `privilege_boundary`, `taint_propagation`,
`finding`, and `audit_note`. The import and preanalysis methods are described in
[Findings and preanalysis](augmenting.md).

### Subgraphs

Named subgraphs are sets of node IDs. Preanalysis and finding imports create
them, for example `tainted`, `entrypoints:untrusted_external`, and
`sarif:error`.

| Method | Returns |
| --- | --- |
| `subgraph_names()` | Every subgraph name on the graph |
| `subgraph(name)` | The nodes in a subgraph |
| `subgraph_edges(name, *, edge_kinds=None)` | Edges whose endpoints are both in the subgraph, optionally filtered by kind |
| `connect_subgraphs(source, target, *, max_depth=20, edge_kinds=("calls",))` | Paths from any node in one subgraph to any node in another |

If the two subgraphs share a node, `connect_subgraphs()` can return that node as
a one-node path.

### Call resolution

| Method | Returns |
| --- | --- |
| `unresolved_calls(name=None)` | Each unresolved call site, optionally only those made directly by `name` |
| `call_resolution()` | Call-site counts and unresolved calls grouped by callee name |

See [Call resolution](call-resolution.md).

### Diff and export

| Method | Returns |
| --- | --- |
| `diff_against(other)` | Structural diff from `other` (the earlier snapshot) to this engine's graph |
| `to_json(indent=2)` | The graph as a JSON string |

```python
before = QueryEngine.from_directory("before/")
after = QueryEngine.from_directory("after/")
diff = after.diff_against(before)
# Keys: source_coverage {before, after}, summary_delta, nodes {added, removed, modified},
#       edges {added, removed}, entrypoints {added, removed, modified}
```

`source_coverage` contains the two graphs' per-language `source_file_counts`,
even when there are no changes. Modified nodes contain an `id` and `changes`;
each changed field has `before` and `after` values. Comparison covers:

- `name`, `kind`, `origin`, `cyclomatic_complexity`, and `line_span`;
- `parameters` (the ordered parameter names), `parameter_types`, and
  `parameter_defaults` (parallel ordered lists);
- `return_type`, `exception_types`, `type_parameters`, and `attributes`.

Type references are objects with `name`, `module`, and recursive `generic_args`,
or `null` for an absent type. Type parameters contain `name`, `constraints`,
`default`, and `variance`. Attributes are objects, so their order is ignored.
Each added or removed edge contains `source`, `target`, `kind`, and `confidence`.
A confidence change appears as removal of the old edge and addition of the new
one. Parallel edges with identical endpoints, kind, and confidence are collapsed
in the edge lists; the summary delta still reflects total edge counts.

This is a comparison of extracted structure, not semantic equivalence. It ignores
source bodies, docstrings, branch conditions, absolute positions (except line
span), edge attributes and locations, annotations, and subgraphs. Build both
snapshots with the same Trailmark version and language selection before
interpreting the result.

## JSON export

`to_json()` and `trailmark analyze`'s default JSON output contain nodes, edges, subgraphs, and a
summary. Since v0.7, graph exports also contain top-level `trailmark_version`
(the exporting package's `trailmark.__version__`) and `schema_version`
(currently `1`). `schema_version` changes for incompatible export-format
changes; additive fields may appear within a schema version. It does not
version parser behavior, so compare exports generated with the same Trailmark
version and language selection even when their schema versions match.
Unversioned exports from older releases have no declared schema version;
rebuild both snapshots with one tested version before interpreting a diff.
These fields identify the exporter, not the source revision or all settings
used to construct a graph. Trailmark does not import graph exports or
automatically validate their compatibility.

The JSON export omits annotations, entrypoint tags, and call-site
locations, so it does not preserve all analysis state. While the engine is
available, use `findings()`, `annotations_of()`, `attack_surface()`, and
`unresolved_calls()` for those details.

## Compatibility

New `NodeKind`, `EdgeKind`, and `AnnotationKind` members may be added in minor
releases. For example, `NodeKind.SCHEMA`, `TABLE`, `VIEW`, and `PROCEDURE` were
added in 0.5.0. Code that matches enum values exhaustively should handle
unknown members.

`trailmark.query.api.detect_languages` is a deprecated alias for
`trailmark.parse.detect_languages`.

### Version notes

Methods not listed here have been available since 0.2.2. Clients that must work
with older releases can check `trailmark.__version__` or probe with `hasattr()`.

| Since | Added |
| --- | --- |
| 0.3.0 | `trailmark.parse` (`parse_file`, `parse_directory`, `detect_languages`, `supported_languages`) |
| 0.4.0 | `connect_subgraphs`, `subgraph_edges`, `generic_parameters`, `type_references`, `augment_binary`, proxy nodes |
| 0.7.0 | `state_read_by`, `state_written_by`, `readers_of`, `writers_of`, `unresolved_calls`, `call_resolution`; `replace_existing` on the finding importers; keyword-only `max_depth` on `paths_between`; directory `allow_empty`, source-file coverage in summaries and diffs, and the expanded structural diff fields above |

## Source analysis diagnostics

`CodeGraph.parse_diagnostics` contains immutable `ParseDiagnostic` records.
`GraphStore.parse_diagnostics()` returns a copy; `QueryEngine.parse_diagnostics()`
returns JSON-serializable dictionaries. The exported summary includes the same
`parse_diagnostics` array. Each record identifies its language, grammar, source
location, diagnostic kind and message. Directory merges preserve diagnostics
from every inspected file independently of `source_file_counts`.

Pass `strict=True` to `parse_file`, `parse_directory` or
`QueryEngine.from_directory` to reject graphs with diagnostics. Valid empty
files remain valid. An empty diagnostic list means no problem was recorded;
it does not establish complete language coverage or compiler validation.
