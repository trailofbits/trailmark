# Constraint annotations

Constraint annotations let an agent save a claim about an argument at one source
call site. They preserve the argument expression, source fingerprints, evidence,
assumptions, and the producer's justification. They do not infer constraints or
prove that a claim follows from the source. This API is unreleased.

Use `call_sites(name)` to inspect operands and current target bindings,
`annotate_constraint(...)` to save a claim, `constraints_of(name)` to check its
freshness, and `constraint_summary(name, parameter)` to combine incoming claims
under explicit assumptions. `constraint_support()` describes all registered
frontends and any unverified imported languages.

## Example

Save this as `demo/app.php`. The length check deliberately refers to `$string`.

```php
<?php
function foo(string $string, array $optional = []): string {
    if (strlen($string) === 35 && substr($string, 0, 3) === "v1:" && ctype_xdigit(substr($string, 3))) {
        return bar($string, $optional);
    }
    return baz($string, $optional);
}
function bar(string $value, array $options): string { return $value; }
function baz(string $value, array $options): string { return $value; }
```

After reviewing the guards, builtin bindings, and value passed at the call, an
agent can record its reasoning:

```python
from trailmark.models import (
    ArgumentRef, ConstraintBoolean, ConstraintEvidence, ConstraintLiteral,
    ConstraintPredicate, ConstraintSlice, SourceLocation,
)
from trailmark.query.api import QueryEngine

engine = QueryEngine.from_directory("demo", language="php")
site = next(site for site in engine.call_sites("foo")
            if any(binding["target_id"].endswith(":bar") for binding in site["bindings"]))
argument = ArgumentRef(0)
suffix = ArgumentRef(0, slices=(ConstraintSlice(3, None, "bytes"),))
predicate = ConstraintBoolean("and", (
    ConstraintPredicate(argument, "length", (ConstraintLiteral("integer", 35),), "bytes"),
    ConstraintPredicate(argument, "prefix", (ConstraintLiteral("bytes", b"v1:".hex()),), "bytes"),
    ConstraintPredicate(suffix, "charset",
                        (ConstraintLiteral("bytes", b"0123456789abcdefABCDEF".hex()),), "bytes"),
))
engine.annotate_constraint(
    "foo", site["id"], predicate,
    semantics="php-8.6:byte-strings",
    evidence=(ConstraintEvidence(
        SourceLocation(site["location"]["file_path"], 3, 4), site["source_sha256"]),),
    assumptions=("php_string_builtins",),
    source="llm",
)
claims = engine.constraints_of("foo")
summary = engine.constraint_summary("bar", 0, assumptions=(
    "complete_callers", "php_string_builtins", "accept_asserted_constraints",
    f"identity_parameter_value:{site['id']}:0",
))
optional_summary = engine.constraint_summary("bar", 1, assumptions=(
    "complete_callers", "php_string_builtins", "accept_asserted_constraints",
    f"identity_parameter_value:{site['id']}:1",
))
```

The first summary is conditional. The optional argument remains unconstrained.
Changing the check to `strlen($bar)` removes the basis for the length-35 claim
about `$string`. Keep that distinction; do not repair source typos while reasoning.
PHP's [`strlen`](https://www.php.net/manual/en/function.strlen.php) measures bytes.
The compact regex description is an explanation, not the stored representation
or an additional proof.

## Grammar version 1

`ConstraintClaim.grammar_version` is `1`. Frozen model classes in
`trailmark.models.constraints` define the grammar; query dictionaries and full
snapshots serialize those fields without executable expressions.

| Model | Meaning |
| --- | --- |
| `ArgumentRef(index, receiver=False, slices=())` | A zero-based source operand; select the receiver with `index=None, receiver=True` |
| `ParameterRef(parameter, slices=())` | A zero-based callee parameter in a summary |
| `ConstraintSlice(start, stop, unit)` | A nonnegative, half-open slice, clamped at the sequence bounds; `None` means through the end |
| `ConstraintLiteral(sort, value)` | A typed `bytes`, `text`, `integer`, `real`, `boolean`, or `null` constant |
| `ConstraintPredicate(subject, operation, values, unit=None)` | A predicate over one subject |
| `ConstraintBoolean(operation, terms=())` | `true`, `false`, `and`, `or`, or unary `not` |

Byte literals use lowercase hexadecimal. Text literals use Unicode code points.
Real constants must be finite. Booleans do not count as integer literals.
Sequence units are `bytes`, `codepoints`, `utf16_code_units`, or `elements`;
slice and predicate units must agree. The grammar performs no encoding conversion.

Predicate operations are:

- `type`: one text literal naming `bytes`, `text`, `integer`, `real`, `boolean`,
  `null`, `sequence`, or `mapping`.
- `equals`: one typed value; `one_of`: a nonempty finite set of same-sort values.
- `range`: two same-sort numeric values with inclusive bounds.
- `length`: one nonnegative integer for exact length, or two for inclusive bounds.
- `prefix`, `suffix`, and `charset`: one byte or text literal with matching units.
  `charset` constrains every element, so an empty sequence satisfies it; record
  a nonempty length separately when required.

`and` and `or` take nonempty tuples. `not` takes one term. Ranges, slice bounds,
literal sorts, units, and operand references are validated. Arbitrary source
expressions, regular-expression engines, solvers, and general heap relationships
are outside this grammar. Preserve unsupported reasoning in an explanatory note
and leave the corresponding constraint unknown.

Byte `prefix`, `suffix`, and `charset` predicates use `bytes`; their text forms
use `codepoints`. Length predicates and slices also support `utf16_code_units`
and `elements`. Stored expressions may nest up to 128 levels.

## Claims and summaries

The `constraint` annotation kind requires a typed `ConstraintClaim` payload.
It describes a claim about supplied values. A required `precondition` describes
an obligation and does not establish that a caller met it.

The annotation's `source` identifies its producer, such as `llm` or `manual`.
`asserted` is the default justification. `reviewed=True` records a review
attestation; neither status is a machine proof. Schema and provenance validation
do not promote either status to verified semantics.

Summaries conjoin accepted facts at each call site and disjoin alternative
incoming sites. They preserve argument correlations conservatively when projecting
onto one parameter. Negating a fact about another argument supplies no fact about
the selected argument. An unconstrained possible incoming path yields an unknown
summary. Contradictory claims do not prove dead code or justify dismissing findings.
Claims must use the same semantics profile across all incoming sites. Trailmark
does not translate between profiles; a mixture yields an unknown summary with a
`conflicting_semantics_profiles` gap, while retaining the original claims.

The caller supplies these named assumptions only after checking their meaning:

- `complete_callers`: actual entries to this callee, with compatible dispatch,
  are exactly the inspected incoming call sites in this graph and source policy.
  Visibility alone does not establish this. Check callbacks, reflection, dynamic
  dispatch, FFI, external clients, and excluded or generated code.
- `accept_asserted_constraints`: include unreviewed claims in a conditional
  summary. The default excludes them.
- `exclude_external_entrypoint:NODE_ID`: exclude an otherwise known external
  entrypoint for a deliberately narrower analysis. It remains visible as an assumption.
- `argument_mapping:CALL_SITE_ID:PARAMETER_INDEX=ARGUMENT_INDEX`: attest a specific
  unchanged value transfer from an operand to a parameter where a frontend cannot
  establish it, including any coercions or narrowing. Indices are
  zero-based. Conflicting or invalid mappings remain unknown; an individual actual
  argument never substitutes for a variadic aggregate.
- `identity_parameter_value:CALL_SITE_ID:PARAMETER_INDEX`: establish that a mapped
  operand reaches a typed parameter without conversion. Typed PHP parameters may
  coerce values, so slot matching alone does not permit constraint propagation.

Each claim can require additional named assumptions, such as unshadowed builtins
or a deployment configuration. The summary includes a claim only when its
assumptions were supplied. Python and PHP retain source signature binding modes.
Methods, decorated Python functions, and other binding profiles require explicit
mapping assumptions because receivers and wrappers can change value binding. Unpacking,
implicit inputs, defaults without a supplied operand, recovered syntax, and missing
call evidence retain explicit gaps.
Receiver claims are retained at their source sites; parameter summaries currently
project supplied argument operands only.

When source operands contain non-UTF-8 bytes, `expression` and `syntax` use escaped
display text. Their `expression_bytes` and `syntax_bytes` fields retain the original
bytes as lowercase hexadecimal. The `source_encoding_unverified` gap prevents
callee summaries from treating this display as verified text.

Summaries return `conditional` or `unknown`, their assumptions and gaps, the
participating site IDs, semantics profiles, and a graph fingerprint. There is no
unconditional proof status. No automatic inference pass runs in this milestone.

## Identity and persistence

Call-site identity includes caller, language, source span, and the source-file
SHA-256 digest. Repeated calls to one callee remain distinct. Target rebinding
does not rename a source occurrence, but invalidates dependent claims.

`active` means the claim's context matches this graph snapshot, including source
fingerprints, selection policy, signatures, target edges, entrypoint candidates,
dependency and binary evidence, diagnostics, and operand evidence. It does not
check the current filesystem. Use
`QueryEngine.from_snapshot(snapshot, verify_sources=True)` when claims must match
current source files. Historical snapshots remain usable without those files.

Full analysis snapshots use **snapshot format 2** and preserve call sites,
parameter binding modes, and typed annotations. Format-1 snapshots are rejected;
reparse their sources with this Trailmark version because the older format lacks
operand and binding evidence. The lightweight `to_json()`
export continues to omit annotations and full call-site evidence.

Changing context makes stored claims stale rather than deleting them.
`constraints_of()` exposes stale claims for inspection; summaries exclude them.
Historical supporting evidence can refer to a changed or excluded source file.
Reloading preserves that evidence and keeps its claim inactive. New claims require
current evidence. Reparsing a changed call creates a new occurrence ID; annotations
are not migrated to that occurrence automatically.
Missing-node reads return empty results. Invalid subjects, ownership, grammar,
evidence references, and conflicting source identities raise `ValueError`.

## Agent workflow

1. Establish the source roots, exclusions, language versions, parse diagnostics,
   and frontend capability limits. Verify current sources before reusing a snapshot
   for a current-code claim.
2. Select an exact caller and source occurrence with `call_sites()`. Inspect raw
   operands, binding confidence, receivers, unpacking, and remaining gaps.
3. Trace each argument's value through the controlling path. Check guard dominance,
   short-circuit behavior, early returns, exceptions, aliases, reassignment,
   callbacks, and side effects between validation and use. An else path negates
   the whole condition; it does not negate every conjunct separately.
4. Confirm validator identity and semantics. Record byte, code-point, code-unit,
   integer-width, overflow, locale, and deployment assumptions where relevant.
5. Save only supported predicates with source spans and named assumptions. Keep
   unrelated arguments unconstrained and leave agent claims asserted by default.
6. Before drawing callee-wide conclusions, audit all incoming paths and external
   entry routes. Request a conditional summary with the assumptions actually
   established. Report its gaps and justification limits alongside the result.
7. After code, bindings, signatures, policy, or caller inventory changes, recompute
   and review stale claims. A stored annotation is reusable reasoning evidence,
   not permission to suppress a security finding automatically.

## Frontend matrix

Every row is a capability disposition. `partial` records supported source forms,
not complete language coverage. Specification and schema relationships do not
become runtime calls. Miden's stack inputs remain implicit; Rust/Verus recovered
macro calls and other unavailable syntax retain gaps. Automatic inference is
unsupported for invocation frontends and not applicable to declarative inputs.

<!-- frontend-matrix -->

| Frontend | Call sites | Operand syntax | Parameter binding |
| --- | --- | --- | --- |
| aiken | partial | partial | unsupported |
| bash | partial | partial | unsupported |
| c | partial | partial | unsupported |
| c_sharp | partial | partial | unsupported |
| cairo | partial | partial | unsupported |
| circom | partial | partial | unsupported |
| cpp | partial | partial | unsupported |
| dafny | partial | partial | unsupported |
| dart | partial | partial | unsupported |
| erlang | partial | partial | unsupported |
| fstar | partial | partial | unsupported |
| func | partial | partial | unsupported |
| go | partial | partial | unsupported |
| graphql | not_applicable | not_applicable | not_applicable |
| haskell | partial | partial | unsupported |
| java | partial | partial | unsupported |
| javascript | partial | partial | unsupported |
| julia | partial | partial | unsupported |
| kotlin | partial | partial | unsupported |
| lean | partial | partial | unsupported |
| lua | partial | partial | unsupported |
| masm | partial | unsupported | unsupported |
| move | partial | partial | unsupported |
| objc | partial | partial | unsupported |
| perl | partial | partial | unsupported |
| php | partial | partial | partial |
| powershell | partial | partial | unsupported |
| proto | not_applicable | not_applicable | not_applicable |
| proverif | not_applicable | not_applicable | not_applicable |
| python | partial | partial | partial |
| r | partial | partial | unsupported |
| rego | partial | partial | unsupported |
| rocq | not_applicable | not_applicable | not_applicable |
| ruby | partial | partial | unsupported |
| rust | partial | partial | unsupported |
| solidity | partial | partial | unsupported |
| sql | unsupported | unsupported | unsupported |
| sway | partial | partial | unsupported |
| swift | partial | partial | unsupported |
| tact | partial | partial | unsupported |
| tamarin | not_applicable | not_applicable | not_applicable |
| thrift | not_applicable | not_applicable | not_applicable |
| tlaplus | not_applicable | not_applicable | not_applicable |
| typescript | partial | partial | unsupported |
| vb | partial | partial | unsupported |
| whyml | partial | partial | unsupported |
| zig | partial | partial | unsupported |
