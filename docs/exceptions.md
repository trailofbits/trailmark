# Exception evidence and support audit

Trailmark retains source evidence about exceptional execution and publishes its
limits through `QueryEngine.exception_events(name)` and `exception_support()`.
This audit covers every registered frontend. Its regression corpus lives under
`tests/fixtures/exceptions/`; `tests/test_exceptions.py` checks ownership,
source spans, event kinds, confidence, diagnostics and JSON behavior.

The corpus verifies specific grammar cases, not runtime conformance. Examples
may refer to external types or helpers. No language runtime version, compiler
flags, error preferences, dialect semantics or program execution is inferred
from successful parsing. Read the source diagnostics as well as this matrix.
An empty exception list is not proof that a function cannot fail.

## Evidence contract

Events record `raise`, `rethrow`, `handler`, `cleanup`, `propagate`, `terminate`
and `declares` separately. They retain unknown operands, handler headers, type
hints, source coordinates and lexical protected regions where available.
A protected region is not a runtime dispatch edge. Events inside a modeled nested
callable belong to that callable, including declarations sharing one source line.
Go function literals, Rust closures/async blocks and Ruby blocks without their own graph nodes
are omitted from events rather than assigned to the enclosing function. Embedded
Verus macro bodies are also omitted. Their absence does not establish safety.

Constructor-like call names are syntactic type hints; they can denote factories.
Variable operands remain expressions rather than invented type names. Handler
types are separate from directly raised types. A TypeScript catch annotation is
not an exception filter. Thrift throws describe declarations, not implementation
execution. Query type references include raised, caught and declared type hints.
The legacy `exception_types` field retains frontend-specific metadata, including
Thrift declarations and Ruby hints that can include blocks; use the event kinds
and coverage boundaries when examining direct source evidence.

`certain` means the exceptional construct itself is explicit syntax. Failure-API
calls and macros whose dispatch is unresolved remain `uncertain` candidates.
Known local callable targets or callable parameters suppress such candidates.
These confidence values do not establish that a statement executes, that a call
reaches the builtin, or that an exception escapes its function.

JSON retains nonempty node `exception_events` and top-level `exception_support`.
Structural diffs compare event kind, syntax, operand, type hints, confidence and
presence of a protected region. Relocating source alone does not change events.
Missing node queries return an empty list; unknown frontend profiles are unverified.

## Frontend matrix

Every cell is a capability disposition: `partial` means reviewed cases exist,
`unsupported` means the model does not currently provide the capability, and
`not_applicable` means it is outside this frontend's source model. No row claims
complete language support. `unverified` is reserved for unknown/imported languages.

Interprocedural exception propagation, implicit execution triggered by failure,
and binary exception-flow recovery are unsupported for runtime-language inputs.
For Proto, GraphQL and Thrift those three dimensions are not applicable to their
schema-only source inputs; generated implementations require separate analysis.
An ordinary deferred call edge elsewhere in Trailmark does not establish runtime
exception dispatch or cleanup ordering.

| Frontend | Events | Type hints | Handlers | Cleanup | Declarations | Boundary |
| --- | --- | --- | --- | --- | --- | --- |
| python | partial | partial | partial | partial | not_applicable | No exception-group matching, context-manager suppression or cancellation analysis. |
| r | partial | not_applicable | partial | partial | not_applicable | Named calls are uncertain; restarts, lazy evaluation and handler dispatch are not modeled. |
| javascript | partial | partial | partial | partial | not_applicable | Arbitrary thrown values remain unknown; promise failures and coercion callbacks are not inferred. |
| typescript | partial | partial | partial | partial | not_applicable | Catch binding annotations are not handler filters; disposal and promise effects are not inferred. |
| php | partial | partial | partial | partial | not_applicable | Exception and Error handlers retain distinct names; magic methods, callbacks and fatal bailouts are not inferred. |
| perl | partial | not_applicable | partial | unsupported | not_applicable | die calls are uncertain; eval syntax does not model $@, destructor timing or string-generated code. |
| ruby | partial | partial | partial | partial | not_applicable | raise/catch/throw calls can be overridden; retry, nonlocal targets and coercion effects are not resolved. Unmodeled blocks are omitted from events. |
| lua | partial | not_applicable | partial | unsupported | not_applicable | Protected-call and error candidates can be shadowed; closing and GC metamethods are not inferred. |
| c | partial | not_applicable | partial | unsupported | not_applicable | setjmp/longjmp are uncertain named calls; signals, SEH and cleanup extensions are not modeled. |
| bash | partial | not_applicable | partial | unsupported | not_applicable | trap names are uncertain; errexit contexts, pipeline statuses and trap execution are not modeled. |
| cpp | partial | partial | partial | unsupported | partial | Catch syntax is retained; implicit RAII cleanup, constructor failure, noexcept and ABI dispatch are not inferred. |
| c_sharp | partial | partial | partial | partial | not_applicable | Filters remain syntax; disposal, async effects and runtime filter order are not analyzed. |
| powershell | partial | not_applicable | partial | partial | not_applicable | Explicit throw/catch/finally are retained; nonterminating errors and preference-dependent behavior are not inferred. |
| java | partial | partial | partial | partial | partial | Declared throws remain separate from direct throws; resource suppression and runtime matching are not analyzed. |
| vb | partial | partial | partial | partial | not_applicable | Structured handlers are retained; legacy On Error/Resume and disposal effects are not modeled. |
| go | partial | not_applicable | partial | partial | not_applicable | panic/recover candidates are uncertain; defer syntax does not prove recovery or goroutine propagation. Unmodeled function literals are omitted from events. |
| rust | partial | not_applicable | unsupported | unsupported | not_applicable | The ? operator propagates values; panic candidates do not establish unwind versus abort, Drop or FFI behavior. Embedded Verus macro bodies and unmodeled closures or async blocks are omitted from events. |
| zig | partial | not_applicable | partial | partial | not_applicable | try/catch/defer/errdefer syntax is retained; error-set inference, panic and cleanup guarantees are not analyzed. |
| solidity | partial | partial | partial | unsupported | not_applicable | Catch regions protect the attempted external call; rollback, low-level return flags and panic matching are not inferred. |
| aiken | partial | not_applicable | not_applicable | not_applicable | not_applicable | fail/expect are failure syntax; validator outcomes and evaluation effects are not proved. |
| cairo | partial | not_applicable | not_applicable | unsupported | not_applicable | Named panic candidates are uncertain; result effects and transaction rollback are not inferred. |
| circom | partial | not_applicable | not_applicable | not_applicable | not_applicable | Assertion candidates are distinct from constraint enforcement; witness and proof outcomes are not established. |
| haskell | partial | not_applicable | partial | partial | not_applicable | Named applications are uncertain; laziness, saturation, masking and asynchronous exceptions are not resolved. |
| julia | partial | not_applicable | partial | partial | not_applicable | Named throw/rethrow/error calls are uncertain; task failures and finalizers are not inferred. |
| erlang | partial | partial | partial | partial | not_applicable | Exception classes and after syntax are retained; process exit signals and catch dispatch are not resolved. |
| masm | partial | not_applicable | not_applicable | not_applicable | not_applicable | Assertion instructions may fail; VM failure and proof validity are not modeled as typed exceptions. |
| swift | partial | partial | partial | partial | partial | throws and throw remain distinct; typed throws grammar coverage, try variants and cancellation are incomplete. |
| objc | partial | partial | partial | partial | not_applicable | @throw/@catch/@finally are retained; ARC cleanup and Objective-C++ exception interoperability are not inferred. |
| kotlin | partial | partial | partial | partial | unsupported | Throw expressions are retained; @Throws, resource use and coroutine cancellation effects are not inferred. |
| dart | partial | partial | partial | partial | not_applicable | Throw/rethrow and on-only handlers are retained; futures, zones and async cleanup are incomplete. |
| move | partial | not_applicable | not_applicable | not_applicable | not_applicable | abort syntax is retained; transaction rollback and returned Result-like values are not inferred. |
| tact | partial | not_applicable | partial | unsupported | not_applicable | Named throws are uncertain; VM exit codes, bounce handling and message-state effects are not resolved. |
| func | partial | not_applicable | partial | unsupported | not_applicable | Try/catch syntax and uncertain throw calls are retained; VM stack restoration and exit codes are not inferred. |
| sway | partial | not_applicable | not_applicable | unsupported | not_applicable | Named revert/assert candidates are uncertain; contract rollback and external call failure are not inferred. |
| rego | unsupported | not_applicable | not_applicable | not_applicable | not_applicable | Undefined policy results and builtin evaluation errors require runtime configuration; no exception events are emitted. |
| proto | not_applicable | not_applicable | not_applicable | not_applicable | not_applicable | This frontend describes schemas; RPC implementation failures are outside the input. |
| thrift | partial | partial | not_applicable | not_applicable | partial | Declared exceptions describe the RPC contract, not implementation throws or runtime handling. |
| graphql | not_applicable | not_applicable | not_applicable | not_applicable | not_applicable | This frontend describes schemas; resolver exceptions and null propagation are outside the input. |
| sql | unsupported | unsupported | unsupported | unsupported | unsupported | PL/pgSQL RAISE bodies produce syntax diagnostics; dialect-specific handlers and transactional effects remain unsupported. |

## Findings and remediation

The implementation closes missing throw capture in Kotlin, Swift and Dart and
qualified constructor-name capture in PHP. It preserves throws whose types are
unknown, rethrows, handler types and lexical cleanup evidence instead of reducing
all exceptional behavior to a flat list of names. Bare returns and breaks remain
separate from throws. Each frontend now has an explicit tested disposition,
including schema-only inputs and SQL grammar failure.

The remaining work requires different analysis capabilities:

1. **Runtime dispatch and propagation.** Resolve thrown values, exception
   inheritance, handler ordering, filters, exception groups and rethrows across
   calls. Add compiler/runtime differential tests before claiming this support.
2. **Implicit execution and interrupted invariants.** Recover callback,
   destructor, resource-disposal and coercion execution with provenance and
   conservative uncertainty. Include mutation during callbacks, partially built
   objects, handler failure and cleanup that raises or suppresses another error.
3. **Runtime-specific failures.** Keep fatal bailouts, cancellation, panic/abort,
   transaction rollback, VM failure and ordinary error-return propagation distinct.
   Check configuration and version boundaries rather than treating all as throws.
4. **Grammar boundaries.** Extend PL/pgSQL body parsing and review incomplete
   forms such as Swift typed throws and embedded Verus macro bodies with focused cases.
   Existing syntax diagnostics must remain visible until those forms are supported.
5. **Compiled exceptional flow.** Recover call-site ranges, landing pads,
   personality functions and cleanup/handler actions with ABI-specific adapters.
   Existing binary call extraction does not establish exception-flow completeness.

## Compiled exception audit

The native importer was reviewed separately from source parsers. Its ELF,
Mach-O and PE adapters establish containers, function ranges, source mappings
and ordinary call evidence. None recovers exception-table actions, landing-pad
dispatch, personality semantics or implicit cleanup edges. The existing JSON
binary call-graph importer does not add those capabilities either.

`tests/fixtures/exceptions/unwind.cpp` provides a concrete boundary check: a
throwing callee, a guard destructor and a typed catch. The native integration
test compiles it with `-std=c++17 -O0 -g -fexceptions -fno-inline`, verifies an
exception-table section exists, and imports its function ranges without executing
the image. Source evidence records the catch; it does not infer the guard's
destructor call during unwinding. Even an import with `complete=true` does not
certify exceptional control-flow completeness.

The local run used Apple clang 21.0.0, arm64 Mach-O, LLVM tools 23.1.4 and LIEF
1.0.0-d05b3499b. ELF and PE exception dispatch remain unsupported by code review;
this host run does not establish their ABI behavior. Follow-up fixtures must
separate Itanium/LSDA, Windows SEH/C++ metadata and other runtime-specific
mechanisms instead of assigning a generic exception edge to every call.

## PHP interruption audit

`php_interruptions.php` covers source ownership for `__wakeup`, `__unserialize`,
`__toString`, an error-handler closure, a destructor sink and a finally block
that raises. It preserves the names `Exception`, `Error` and `Throwable` rather
than treating them as interchangeable. A direct call inside a destructor is
retained; the graph does not invent a caller for implicit destruction or coercion.

The optional `tests/test_exception_runtime.py` runs the self-contained
`php_runtime.php` with `php -n`. On PHP 8.5.12 it verified:

- A string conversion changes previously checked state, throws `Error`, bypasses
  an `Exception` handler, and still executes `finally` before the outer handler.
- A user warning invokes a callback that throws `ErrorException`.
- Deserialization invokes `__unserialize` rather than `__wakeup` when both
  exist, and initialization can fail inside that callback.
- An exception raised in `finally` replaces the earlier exception, which remains
  available through the previous-exception chain.

These tests distinguish observed runtime execution from the parser's direct
throw hints. The runtime probe requires PHP 7.4 or newer: PHP's
[magic-method documentation](https://www.php.net/manual/en/language.oop5.magic.php)
dates `__unserialize` and throwing from `__toString` to that version. Earlier
runtimes require separate fatal-error tests. The
[error-handler contract](https://www.php.net/manual/en/function.set-error-handler.php)
also excludes several fatal/compile error categories; an error callback does not
make all failures catchable. Shutdown, cyclic GC, session/stream callbacks,
extension reentrancy and engine memory safety remain unverified by this corpus.

## Cross-language review seeds

Use the PHP cases as questions to test against each runtime, rather than assuming
the same exploit transfers:

| Review question | Candidate boundaries | Evidence still needed |
| --- | --- | --- |
| Can user code mutate a value between validation and use? | PHP coercion/error callbacks; JavaScript getters, proxies and coercions; Python properties and special methods; Ruby conversions; R lazy arguments | Actual callback dispatch, alias identity and mutation order |
| Does failure run a sensitive cleanup action or replace the original failure? | C++ destructors; Java resources; C#/VB disposal; Python context managers; Go/Zig/Swift defers; Lua closing hooks | Runtime version, cleanup order, suppression, replacement or termination rules |
| Can restoration expose a partially initialized object? | PHP unserialization; Java object streams; Python pickle; Ruby Marshal; application serializers | Reachable hooks, object lifetime and side effects before initialization completes |
| Does a handler cover the relevant failure family? | PHP Error versus Exception; PowerShell terminating versus nonterminating errors; Erlang exception classes versus process signals; Rust unwind versus abort | Configuration, runtime matching and process/thread boundaries |
| Is failure being mistaken for a catchable exception? | C nonlocal jumps/signals; Solidity/Move/Sway rollback; Cairo/Tact/FunC/assembly VM failure; Rego undefined results; SQL transactional errors | Target-specific state, rollback and continuation semantics |
| Can execution be interrupted outside an explicit throw? | Haskell asynchronous exceptions; cancellation in Kotlin/Swift/Dart; JavaScript promises; FFI in native languages | Scheduling, masking, callback and cancellation semantics |

These are prioritization hypotheses, not detected vulnerabilities. The support
matrix states which syntax Trailmark currently retains; proving a path requires
the missing dispatch and runtime evidence.

## Security research basis

Akshay's [2019 McSema article](https://blog.trailofbits.com/2019/01/21/how-mcsema-handles-c-exceptions/)
shows why ordinary calls alone do not recover exceptional control flow: handler
and cleanup dispatch depends on runtime behavior and compiler metadata. It guides
this audit's separation of source evidence from inferred execution. The
[Itanium exception ABI](https://itanium-cxx-abi.github.io/cxx-abi/abi-eh.html)
is one platform model, not a universal model for every language or binary format.

Stefan Esser's [2009 interruption research](https://www.blackhat.com/presentations/bh-usa-09/ESSER/BHUSA09-Esser-PostExploitationPHP-SLIDES.pdf)
motivates tests for native operations that invoke user callbacks while holding
assumptions about mutable values. Error handlers, conversions and user handlers
can invalidate those assumptions. His [2010 code-reuse presentation](https://media.blackhat.com/bh-us-10/presentations/Esser/BlackHat-USA-2010-Esser-Utilizing-Code-Reuse-Or-Return-Oriented-Programming-In-PHP-Application-Exploits-slides.pdf)
adds deserialization-triggered magic methods and object-controlled call chains.
[His SPL type-confusion report](https://www.sektioneins.de/blog/14-08-27-unserialize-typeconfusion.html)
keeps application-level object injection distinct from engine memory corruption.

These produce audit hypotheses, not vulnerability claims about current runtimes:
mutate a checked value during a callback; reach a sensitive operation during
cleanup; deserialize an object whose initialization fails; bypass an Exception
handler with a different Throwable branch; or interrupt native cleanup with a
fatal bailout. Transfer those questions to other languages' coercions, getters,
finalizers, context managers, disposal hooks, lazy evaluation and FFI boundaries.
Confirm each runtime's rules before asserting that a cross-language variant exists.

## Reproduction

Run `uv run pytest -q tests/test_exceptions.py` for the bounded corpus, and the
full repository checks from `CONTRIBUTING.md` for integration. Snapshot changes
must be reviewed separately; never update snapshots during CI or mutation tests.
Run `uv run pytest -q tests/test_exception_runtime.py` for the optional PHP probe.
With the binary extra and LLVM tools installed, run
`uv run pytest -q tests/test_native_binary.py::test_exception_metadata_does_not_imply_recovered_exception_dispatch`
for the host compiled fixture. Missing optional tools produce a skip, not a
verified runtime or binary result.

Audit environment: tree-sitter 0.25.2, tree-sitter-language-pack 1.22.1, tree-sitter-sql 0.3.11. Bundled grammar sources are versioned with
this checkout. Runtime/ABI versions remain unverified unless separately recorded.
