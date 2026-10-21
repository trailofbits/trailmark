# Findings, annotations, and preanalysis

Trailmark can attach information from other tools, and from you, to graph
nodes. Once attached, it can be queried alongside the call graph: for example,
"which static-analysis findings are reachable from an untrusted entrypoint?"

- [SARIF and weAudit findings](#sarif-and-weaudit-findings)
- [Binary call graphs](#binary-call-graphs)
- [Annotations](#annotations)
- [Preanalysis](#preanalysis)

## SARIF and weAudit findings

```bash
trailmark augment --sarif semgrep.sarif --weaudit audit.json path/to/project
```

```python
engine.augment_sarif("semgrep.sarif")
engine.augment_weaudit("audit.json")
engine.findings()                        # nodes with finding or audit_note annotations
engine.subgraph("sarif:error")

engine.preanalysis()                     # creates the entrypoints:* subgraphs
engine.connect_subgraphs("entrypoints:untrusted_external", "sarif:error")
```

[weAudit](https://github.com/trailofbits/vscode-weaudit) is Trail of Bits'
VS Code extension for marking findings and notes during code review.

**Matching.** Each finding's file path and line range is matched against
source nodes. A finding is attached to every source node whose span overlaps
it, including enclosing classes and modules, not just the innermost function.
Proxy and binary nodes are never matched. Relative and absolute analysis roots
match the same findings. Findings that overlap no node are counted as
unmatched.

| Input | Annotation | `source` | Subgraphs created |
| --- | --- | --- | --- |
| SARIF 2.1.0 result | `finding`, described as `[LEVEL] ruleId: message (tool)` | `sarif:<tool>` | `sarif:<level>` (default level `warning`), `sarif:<tool>` |
| weAudit finding | `finding` | `weaudit:<author>` | `weaudit:findings`, `weaudit:<severity>` |
| weAudit note | `audit_note` | `weaudit:<author>` | `weaudit:notes`, `weaudit:<severity>` |

**Repeated imports.** By default, each SARIF import replaces findings from
earlier SARIF imports, and likewise for weAudit. Pass `replace_existing=False`
to accumulate findings across files; importing the same finding twice this way
keeps both copies. The CLI accumulates automatically, so repeated `--sarif` and
`--weaudit` flags keep every file's findings.

**Failures.** Both importers validate the JSON fields they read. An import that
fails before completion preserves the graph's previous state.

## Binary call graphs

Trailmark does not disassemble binaries. It can import a call graph produced
by another tool (a disassembler script, for example) and connect it to the
source graph:

```python
engine.augment_binary("build/app.callgraph.json")
engine.augment_binary("libfoo.json", connect_sources=False)  # skip source matching
```

The input is a JSON object with an `artifact` object and `functions` and
`calls` lists. Unknown fields are ignored.

```json
{
  "artifact": {"key": "app", "architecture": "x86_64", "hash": "abc123", "path": "build/app"},
  "functions": [
    {"name": "main", "address": "0x1000", "size": 32, "section": ".text",
     "source": {"symbol": "main"}},
    {"name": "helper", "address": "0x1040", "size": 16}
  ],
  "calls": [
    {"source": "main", "target": "helper", "confidence": "certain"},
    {"source": "helper", "target": "puts", "confidence": "uncertain"}
  ]
}
```

| Field | Meaning |
| --- | --- |
| `artifact.key` (or `name`) | Namespace for node IDs; defaults to the file name without its last extension (`app.callgraph` for `app.callgraph.json`) |
| `artifact.architecture` (or `arch`), `artifact.hash` (or `sha256`), `artifact.path` | Recorded on each imported node |
| `functions[].name` (or `symbol`) | Function name. Unique names give IDs like `bin.app:main` |
| `functions[].address` or `rva` | Used for the ID (`bin.app:sub_1040`) when the name is missing or repeated |
| `functions[].source` | Optional link to source: `node_id`, `symbol`/`name`, or `file`/`path` plus `line`. Without it, the function's own name is matched against source nodes. |
| `calls[].source`, `calls[].target` | A function's name, address, or `id`, or an object with one of `id`, `symbol`, `name`, `address`, `rva` |
| `calls[].confidence` | `certain`, `inferred`, or `uncertain` |

Imported functions have origin `binary`. Each import creates a
`binary:<artifact key>` subgraph. Call targets that are not in `functions`, such
as `puts` above, become `proxy.external:` nodes. With `connect_sources=True`
(the default), each imported function that matches a source node, through its
`source` field or its name, gets a `corresponds_to` edge from that node, with
`inferred` confidence.

Binary imports raise `ValueError`, and leave the graph unchanged, when:

- two functions would get the same node ID
- an artifact key is reused with a different known architecture or hash
  (omitting these fields does not erase values already recorded)
- a record has the wrong shape

## Annotations

Annotations are free-text notes attached to a node, each with a kind and a
source.

```python
from trailmark.models.annotations import AnnotationKind

engine.annotate("Vault.withdraw", AnnotationKind.INVARIANT,
                "Total shares never exceed total assets", source="manual")
engine.annotations_of("Vault.withdraw")
engine.nodes_with_annotation(AnnotationKind.INVARIANT)
engine.clear_annotations("Vault.withdraw", AnnotationKind.INVARIANT)
```

Kinds: `assumption`, `precondition`, `postcondition`, `invariant`,
`blast_radius`, `privilege_boundary`, `taint_propagation`, `finding`, and
`audit_note`. The `source` string is free-form. By convention it is `manual`,
`llm`, or `docstring` for annotations you add; Trailmark itself uses
`preanalysis`, `sarif:<tool>`, and `weaudit:<author>`.

Annotations are not included in `to_json()` output.

## Preanalysis

```python
summary = engine.preanalysis()
```

Preanalysis runs four passes and stores the results as annotations (with source
`preanalysis`) and named subgraphs. Running it again replaces the previous
results.

| Pass | What it computes | Annotation | Subgraphs |
| --- | --- | --- | --- |
| Blast radius | For every node, how many nodes it can transitively call and be called by | `blast_radius` | `high_blast_radius` (10 or more downstream nodes) |
| Entrypoints | Entrypoints, grouped by trust level, and everything they can reach | | `entrypoints`, `entrypoints:<trust_level>`, `entrypoint_reachable` |
| Privilege boundaries | Call edges where both ends are reachable from entrypoints, but from different sets of trust levels | `privilege_boundary` | `privilege_boundary` |
| Taint | Every node reachable from an entrypoint that is not `trusted_internal` | `taint_propagation`, naming the entrypoints | `tainted` |

All four passes follow `calls` edges only. "Taint" here means call-graph
reachability from an untrusted or semi-trusted entrypoint, not data flow: a
tainted function can be called on a path that starts at an untrusted
entrypoint, but Trailmark has not shown that attacker-controlled data reaches
it. Treat it as a way to prioritize review, not as a finding. Unresolved calls
end each pass at a proxy node; see [Call resolution](call-resolution.md).
