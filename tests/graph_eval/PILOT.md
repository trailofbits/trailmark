# Pilot review: keep the blinding, narrow the questions

The pilot found four reproducible contract issues and several useful capability
gaps. It also exposed enough evaluator and adjudication limits that **the current
protocol should not expand unchanged**. These are bounded observations, not
frontend accuracy estimates.

## Evidence and scope

The preregistered sample has 32 scopes across eight languages and 16 pinned public
repositories. All 64 independent source readings completed; five inventories
remain explicitly partial. All 16 whole-project measurements produced repeatable
graphs with unchanged sources. Twelve projects had executable public-query plans;
their repeated graphs and complete runtime fingerprints match the originals.
Four projects had no executable requests.

Independent validators accepted ten files and rejected three: one whole GraphQL
schema has duplicate-field diagnostics outside the primary ranges, and two SQL
files contain build-time placeholders. Sixteen files have no configured
independent validator. These statuses constrain interpretation; they are not graph
correctness verdicts.

The baseline is `455f6554aa1b6c138c62a7d2a114ae4aba111af7`; the frozen evaluator is
`b7f1a693e0a7893dbe8d5fa8cbbd042701c78c28`, using reference schema 1. The released
harness subsequently fixes issues discovered during the pilot. Replay original
evidence from its frozen checkout. Do not migrate sealed predictions to schema 2.

[corpus.py](corpus.py) pins revisions; [pilot.py](pilot.py) fixes source ranges.
Raw sources, prompts, responses, graphs, failures, validators, and reproduction
receipts remain outside Git in the campaign directory. Its `report.md` and
`results.json` record every case, including failed adjudication. Independent
reviews and executed controls are under `independent-review/`.

The original automatic adjudication accepted three outputs, rejected seven for
extra decision IDs, and rejected 22 oversized prompts before model turns. The
independent reviews below are separate from those automatic outcomes. Five model
defect nominations described one TLA+ LET-local representation issue; review left
its contract interpretation unresolved and appended the correction with provenance.
Original reports and the journal retain the initial decisions.

The worker logs record 118 attempts and 25,127 summed job-wall seconds, overlapping
across parallel jobs. That includes local input-limit rejections; it is not a count
of model turns. Recorded usage includes about 12.7 million input tokens and 2.1
million output tokens, with caching and reasoning reported separately in the raw
artifacts. Broad inventories are costly even before expert review.

Two fresh sessions using the same model provide procedural independence, not
statistical independence. The sample is purposeful. Agreement between readers,
accepted validator syntax, and repeated graph hashes do not establish correctness.

## Independently reproduced issues

Separate reviewers checked all eight languages against frozen source and the
public contract. The following counterexamples have executed parsing controls;
they do not depend on an automatic adjudicator accepting a nomination.

| Issue | Pilot witness | Reproduced behavior |
| --- | --- | --- |
| Python local binding | Requests `auth.py:289–290`, `331–332` | A walrus-bound callback lacks the ordinary local-binding guard. Adding a same-name module function makes the call resolve incorrectly to that function with certain confidence; an ordinary-assignment control stays blocked. |
| TypeScript generator binding | tRPC `middleware.ts:286`, `314` | The nested async-generator node exists, but its direct invocation remains a proxy. A same-name outer function makes the invocation resolve incorrectly to the outer target. Ordinary nested functions resolve correctly. |
| Solidity call text and spans | Uniswap `Hooks.sol:85–99` | Fourteen receiver calls absorb comparison prefixes into their raw targets and all start on line 85. Parenthesizing the actual calls restores their names, individual spans, and shared proxy identity. Receiver resolution is a separate question. |
| F* effect metadata | HACL Base `addcarry_st`; FStar.Option pairs | Implementations inherit execution classification but retain an empty effect string where the matching signature records `Tot`, `ML`, or `Stack`, contrary to the documented metadata-inheritance promise. Calls still classify correctly. |

The first three concern binding or call-site fidelity. The fourth is a smaller
metadata/documentation inconsistency. Parser changes and regression-fix PRs are
separate work after this review.

## Controls and capability gaps

- Python retains all 90 callable-body AST call sites in the selected Requests
  auth/hooks files. Selected direct helpers, repeated same-line callback sites,
  and TypeScript nested body ownership pass their controls.
- Rust local helper identities, repeated sites, and the four rustls receiver-chain
  occurrences pass. Unresolved receiver proxies follow the documented boundary.
- Solidity retains the selected ERC20 state-access occurrences and public-query
  deduplication, plus direct Hooks helpers without false ABI-designator recursion.
- F* returned function values remain references; the selected callback does not
  bind to a global namesake. TLA+ preserves the DieHard reference path and creates
  no runtime calls. CoffeeCan proof steps are unsupported and diagnosed.
- GraphQL retains all 59 reader-A fields across the four scopes. A valid schema
  control returns an ownership path through `contains`, with no runtime calls.
- SQL's independently accepted outer PostgreSQL syntax can still produce
  diagnosed omissions and corrupt routine boundaries. PGMQ `read_grouped_rr`
  spans lines 163–244 with three parameters; the graph gives 163–371 and eight,
  and omits the polling wrapper. A three-statement reduction reproduces parameter
  merging. The causal syntax feature is unresolved; this is a diagnosed coverage
  and representation gap, not an unqualified silent-call failure.

Other reproduced needs include explicit TypeScript import disambiguation in the
presence of an unrelated private namesake, constructor-call coverage, and TLA+
local recursive-value navigation. These exceed the narrow guarantees reviewed
here. Parameters, locals, import bindings, proof steps, schema arguments, and
symbolic state uses also need explicit representation mapping before a missing
node or edge becomes a defect claim.

## Method decisions

The frozen public-query dispositions are 126 blocked, 21 unsupported, 20
discrepancies, and 25 agreements across 192 questions. **These are not accuracy
scores.** Many readers asked for containment, type, or state paths while the
evaluator supplied a different relation set. Treat schema-1 path outcomes as
semantically unscored; the GraphQL control demonstrates the resulting false
mismatch. Release schema 2 requires explicit question `edge_kinds`.

The review also found and fixed bounded-negative-path and incomplete-alignment
guards, call-only caller/callee semantics, missing query-runtime identity checks,
and hidden validator outcomes. Model adjudicator categories remain explicitly
unverified until independent source review and an executed reproduction support
them. Missing decision IDs invalidate an adjudication.

Full-case adjudication also exceeded the pinned CLI's input limit. Preserve those
failures rather than truncating evidence or retrying toward a preferred answer.
The release checks its conservative prompt budget before Docker or credential
access. Size audits show repeated global summaries and comparison evidence; even
lossless sharing alone does not make every broad case fit.

The release therefore makes a declared case-context projection before sharing
repeated evidence through named tables. Excluded global context has a count/hash
ledger; full originals stay sealed. Reader facts, source text, the scoped graph,
and all public-navigation fields and answers remain unchanged. Serialized table
decoding reconstructs that excerpt exactly. All 32 retained cases fit in an
offline audit: the largest final prompt is 963,932 UTF-8 bytes, or 964,256 with two
additional relation labels per reader question, below the 1,048,576-byte budget.
Exact required claim IDs now constrain both prompt and output schema. These are
versioned changes for future campaigns. They were not sent to models in this
pilot, so their effect on adjudication quality remains unmeasured.

For the next protocol, retain separate worktrees, source-only sessions, project
barriers, immutable artifacts, real public queries, and corruption/metamorphic
controls. Preregister a small set of typed navigation questions and source-site
witnesses, then map native facts to the public graph contract before measurement.
Keep desired capabilities separate from promised behavior. Test input/output
budgets on representative worst cases and preregister any claim batching.

Do not expand the remaining 39 frontends or change parsers as part of this pilot.
