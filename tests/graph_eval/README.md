# Source-first graph evaluation

This harness compares what independent source readers need from Trailmark with
what a frozen Trailmark build produces. It preserves predictions, measurements,
disagreements, and adjudication evidence. It does not change language parsers or
treat reader agreement as ground truth.

## Pilot and scope

The first campaign contains **32 cases**: eight languages, two pinned public
repositories per language, and an ordinary and stress scope per repository.
The languages are Python, TypeScript, Rust, Solidity, F*, TLA+, GraphQL, and SQL.
[corpus.py](corpus.py) pins repositories and revisions;
[pilot.py](pilot.py) fixes source ranges and rationales before any measurement.
These are purposeful coverage samples, not ecosystem defect-rate estimates.
The [pilot review](PILOT.md) records independently reproduced findings and the
method changes required before expanding the sample.

The frozen baseline is commit
`455f6554aa1b6c138c62a7d2a114ae4aba111af7`. Create an unchanged worktree at that
revision and install it in editable mode into a separate environment (for example,
with `UV_PROJECT_ENVIRONMENT=/absolute/tool-env uv sync --locked --no-dev`).
Measurement workers run its interpreter with `-I`, so the evaluator
checkout and target repositories cannot shadow the installed Trailmark package.
Every measurement records interpreter, package, grammar, and source hashes.
Keep the baseline unchanged during the campaign.

A primary scope is a complete source excerpt small enough to inventory. Context
is explicit. Targets outside those scopes may remain external or unknown; readers
cannot make whole-project negative claims from a partial reading. Graph generation
still covers the full selected project. Schema dependencies, proof references,
type uses, and specification relations do not imply runtime calls.

## Protocol

1. Prepare separate source-reading and measurement worktrees. Verify their source
   inventories and revisions. Freeze case IDs, ranges, content hashes, semantic
   profiles, model settings, prompts, schemas, and tool versions.
2. Run **two independent source-only readers** per case. Each fresh session
   inventories declarations and source sites, records uncertainty, and supplies
   exactly three bounded navigation questions. Readers see source and neutral
   task metadata; they do not see Trailmark, measured graphs, other readers, or
   previous results.
3. Validate and freeze both reader artifacts before exposing measurements. A
   partial inventory remains partial. Retries retain their attempt evidence.
4. Measure explicit-language raw parsing and the default public `QueryEngine`
   separately. Run automatic discovery as secondary evidence. Retain diagnostics,
   unresolved calls, confidence, proxy origins, ownership, relation kinds and
   attributes, and every available source span.
5. Match declarations by path, source location, name, and owner; compare uses per
   source site. Ambiguity remains explicit. A proxy can preserve a call site while
   preventing useful navigation; it differs from a dropped call.
6. Give discrepancies, source, validator evidence, and the documented contract
   to a fresh adjudicator. Preserve both original readers unchanged. Unsupported
   diagnoses remain unresolved. A confirmed defect needs exact source locations,
   expected and observed behavior, and a replayable reproduction.
7. Review the pilot and reproductions before expanding the corpus or changing
   parsers. Turn accepted findings into focused regressions separately.

Runtime, formal, schema, and SQL profiles keep language-native relations distinct.
The comparison preserves repeated uses on one line, nested owners, deferred or
specification context, and unresolved binding. It never resolves an ambiguous
target by its terminal name alone.

## Isolation and controls

Reader and adjudicator jobs use fresh containers, read-only source views, fresh
sessions, and a restricted source-reading MCP interface. Target files are data,
including comments and embedded instructions. Repository agent/config metadata
and symlinks are excluded from reader views. Readers cannot run target build
scripts, imports, tests, or arbitrary commands. Provider traffic uses restricted
egress; source reads are audited. Authentication stays in host memory and travels
through private container stdin. The launcher creates a mode-0600 auth file only
on its verified private runtime tmpfs, then removes it after Codex exits. Credentials
never enter host temporary files, bind mounts, container environment, arguments,
or artifacts. Run the isolation probe before trusting results.

Before Docker or credential access, the worker enforces a conservative
1,048,576-byte UTF-8 prompt budget for the pinned CLI's character limit. Oversized
inputs return `input_limit_exceeded` with zero attempts. Exact prompt/schema
artifacts remain under `rejected-input/`, with sizes and hashes. The worker never
truncates, splits, or retries them. Change scope or preregister batching in a new
protocol rather than modifying sealed inputs.

Measurements use the frozen installation outside reader containers. The worker
builds the graph twice, checks full graph hashes, and compares source inventories
before and after parsing. Nondeterminism or source changes invalidate the result.
A crash or timeout retains its status and stderr; it never becomes a successful
empty graph. Controls and failures belong in the report alongside successes.

`measure.py` saves three views:

| Field | Meaning |
| --- | --- |
| `raw_graph` | Explicit-language `parse_directory` result before default analysis |
| `graph` | Full model snapshot after default `QueryEngine` directory analysis |
| `public_export` | Public `QueryEngine.to_json()` representation |

The full model snapshot supplements public JSON because public JSON omits edge
locations. It retains raw IDs, relative paths, origins, annotations, entrypoints,
and edge attributes. Public query answers are separate. No display-name or
basename collapse is allowed.

Public-query evidence must match both the original graph and its runtime
fingerprint, including package, grammar, and source hashes. Reference schema 2
requires explicit question `edge_kinds` before measurement. Callee and caller
questions use `calls`; ownership uses `contains`; dependencies and paths declare
their relation kinds. The evaluator never infers these from prose or a language
profile. A negative path question is not evaluated
when the mapped domain can contain a simple path beyond the 20-edge search bound.

Schema-1 campaigns retain their original evaluator checkout and semantics.
Replay them there; do not add relation kinds to their sealed references or
reinterpret their path outcomes as if readers had declared those kinds.

Judge prompts explicitly excerpt default global diagnostics and entrypoint lists
to the case, retaining unknown locations and labelling scalar counts as global.
An exclusion ledger records rules, counts, and hashes; full originals stay sealed.
Readers, source text, the scoped graph, and every public-navigation answer remain
unchanged. Named reference tables then share repeated evidence with an exact
serialized round-trip check. This encoding preserves the case excerpt, not the
original global context. `prompt-evidence.json`, `prompt-presentation.json`, and
`prompt-provenance.json` freeze both views, their versions, hashes, and final size.
The output schema enumerates the exact required claim IDs; semantic validation
also rejects missing, duplicate, or extra decisions.

Independent validators supply bounded evidence:

| Language | Validator and limit |
| --- | --- |
| Python | Standard-library AST; syntax, declarations, syntactic calls; no binding proof |
| TypeScript | Pinned compiler API; syntax and declarations; no typechecking or execution |
| GraphQL | `graphql-core`; whole-file SDL/schema consistency; no resolver execution |
| SQL | `pglast`; PostgreSQL outer statements; no PL/pgSQL body, psql, or extension execution |

Each file has its own validation process. Missing dependencies are `unavailable`;
unsupported languages are `not_configured`. Neither counts as a pass. Template
placeholders, split schemas, or missing context can explain rejection within a
validator's scope. Acceptance does not prove that a measured graph is correct.

Keep validators separate from Trailmark. Pin Python packages with a hash-checked
requirements lock and the compiler with npm's lockfile. Supply the validator
interpreter and `NODE_PATH`; preserve locks and actual versions. Verify positive
and negative syntax controls before interpreting results.

## Running a campaign

Keep artifacts outside every checkout, for example
`~/tmp-worktrees/trailmark-eval/pilot-20261119`. Prompts, responses, source-read
logs, copied source, graphs, and reports stay there. Git contains the evaluator,
integrity tests, concise decisions, and accepted regressions.

From the evaluator checkout, prepare a campaign using the frozen Trailmark Python,
the independent validator Python, and an immutable worker image ID:

```bash
python -m tests.graph_eval prepare ~/tmp-worktrees/trailmark-eval/pilot-20261119 \
  --tool-python /path/to/frozen-trailmark/bin/python \
  --validator-python /path/to/validator-env/bin/python \
  --image sha256:IMAGE_ID --live-preflight
python -m tests.graph_eval run ~/tmp-worktrees/trailmark-eval/pilot-20261119 --workers 4
python -m tests.graph_eval report ~/tmp-worktrees/trailmark-eval/pilot-20261119
python -m tests.graph_eval replay ~/tmp-worktrees/trailmark-eval/pilot-20261119 --case CASE_ID
```

`prepare` requires either `--image` or `--build-image`. The latter builds the
current worker and records its immutable image ID and retention tag. Builds use
unique tags; later builds preserve images retained by earlier campaigns.

`prepare` pins source worktrees and preregisters cases. `run` performs the frozen
reader/measurement/comparison/adjudication protocol. `report` regenerates results
from artifacts. `replay` reproduces a selected case using recorded inputs. Use
`python -m tests.graph_eval COMMAND --help` for exact environment options.
Select model and reasoning settings before preparation. Responses can vary even
when model settings and inputs are pinned.

Run phases separately with `run CAMPAIGN --phase readers`, `--phase measure`,
or `--phase judge`; the default is `--phase all`. Filter with `--language NAME`
or `--case ID`. Parallelize independent cases with `--workers 4`; shard using
`--shard 1/4` on separate workers. Stable case-ID hashes assign each case to
exactly one shard regardless of input order. Reader sessions share no writable
memory. Keep attempts separate and verify hashes before resuming or replaying.
Finish the reader phase for both cases of a project before measuring that project;
a one-case filter or shard cannot bypass this barrier. Failed readings remain
visible and do not count as reference agreement.

Freeze the evaluator in its own Git worktree before measurement. Retain its commit
and file hashes alongside the manifest so later evaluator edits cannot silently
change a replay. The original model response stays sealed; independent review
corrections use `append_adjudication(..., provenance=...)` and a regenerated
`adjudication.json` export. Provenance records `reviewer`, `reason`, and
`reviewed_at`; the hash-linked journal retains every earlier decision.

## Reading results

Report correctness against the documented contract separately from usefulness
for source-backed navigation. Adjudication categories are `confirmed_defect`,
`documented_limitation`, `capability_need`, `expectation_error`, `comparison_error`,
`intentional_approximation`, `unresolved`, and `agree`.

These are model adjudicator classifications pending independent review. Even
`confirmed_defect` is an unverified nomination until a reviewer checks the source,
contract, and an executed reproduction. Nonempty evidence fields do not prove
that those checks occurred. Preserve review decisions and reproduction receipts
alongside the frozen evidence, and identify reviewed findings separately.

Show per-language and per-stratum counts, reader disagreements, unresolved
claims, unavailable validators, diagnostics, crashes, timeouts, and navigation
outcomes. Keep agreement separate from confirmed correctness. Do not report
precision or recall without a complete adjudicated inventory. Bounded scopes
and honest uncertainty take precedence over an aggregate score.
