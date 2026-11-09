"""Explicit exception capability limits for each public frontend.

Partial means that reviewed source cases are represented, not that all language
forms or runtime effects are understood. Keep this catalog paired with the
39-frontend regression corpus and the exception support guide.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ExceptionSupportStatus(StrEnum):
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"
    NOT_APPLICABLE = "not_applicable"
    UNVERIFIED = "unverified"


@dataclass(frozen=True)
class ExceptionSupport:
    """Frontend capabilities; this does not certify a particular input graph."""

    language: str
    events: ExceptionSupportStatus
    type_hints: ExceptionSupportStatus
    handlers: ExceptionSupportStatus
    cleanup: ExceptionSupportStatus
    declarations: ExceptionSupportStatus
    interprocedural_propagation: ExceptionSupportStatus
    implicit_execution: ExceptionSupportStatus
    binary_exception_flow: ExceptionSupportStatus
    notes: str


# events, type hints, handler syntax, cleanup syntax, declarations, caveat.
# P: partial, U: unsupported, N: not applicable to this frontend's source model.
_PROFILES = {
    "python": (
        "PPPPN",
        "No exception-group matching, context-manager suppression or cancellation analysis.",
    ),
    "r": (
        "PNPPN",
        "Named calls are uncertain; restarts, lazy evaluation and handler dispatch are not "
        "modeled.",
    ),
    "javascript": (
        "PPPPN",
        "Arbitrary thrown values remain unknown; promise failures and coercion callbacks are "
        "not inferred.",
    ),
    "typescript": (
        "PPPPN",
        "Catch binding annotations are not handler filters; disposal and promise effects are "
        "not inferred.",
    ),
    "php": (
        "PPPPN",
        "Exception and Error handlers retain distinct names; magic methods, callbacks and "
        "fatal bailouts are not inferred.",
    ),
    "perl": (
        "PNPUN",
        "die calls are uncertain; eval syntax does not model $@, destructor timing or "
        "string-generated code.",
    ),
    "ruby": (
        "PPPPN",
        "raise/catch/throw calls can be overridden; retry, nonlocal targets and coercion "
        "effects are not resolved. Unmodeled blocks are omitted from events.",
    ),
    "lua": (
        "PNPUN",
        "Protected-call and error candidates can be shadowed; closing and GC metamethods are "
        "not inferred.",
    ),
    "c": (
        "PNPUN",
        "setjmp/longjmp are uncertain named calls; signals, SEH and cleanup extensions are "
        "not modeled.",
    ),
    "bash": (
        "PNPUN",
        "trap names are uncertain; errexit contexts, pipeline statuses and trap execution are "
        "not modeled.",
    ),
    "cpp": (
        "PPPUP",
        "Catch syntax is retained; implicit RAII cleanup, constructor failure, noexcept and "
        "ABI dispatch are not inferred.",
    ),
    "c_sharp": (
        "PPPPN",
        "Filters remain syntax; disposal, async effects and runtime filter order are not analyzed.",
    ),
    "powershell": (
        "PNPPN",
        "Explicit throw/catch/finally are retained; nonterminating errors and "
        "preference-dependent behavior are not inferred.",
    ),
    "java": (
        "PPPPP",
        "Declared throws remain separate from direct throws; resource suppression and runtime "
        "matching are not analyzed.",
    ),
    "vb": (
        "PPPPN",
        "Structured handlers are retained; legacy On Error/Resume and disposal effects are "
        "not modeled.",
    ),
    "go": (
        "PNPPN",
        "panic/recover candidates are uncertain; defer syntax does not prove recovery or "
        "goroutine propagation. Unmodeled function literals are omitted from events.",
    ),
    "rust": (
        "PNUUN",
        "The ? operator propagates values; panic candidates do not establish unwind versus "
        "abort, Drop or FFI behavior. Embedded Verus macro bodies and unmodeled closures "
        "or async blocks are omitted from events.",
    ),
    "zig": (
        "PNPPN",
        "try/catch/defer/errdefer syntax is retained; error-set inference, panic and cleanup "
        "guarantees are not analyzed.",
    ),
    "solidity": (
        "PPPUN",
        "Catch regions protect the attempted external call; rollback, low-level return flags "
        "and panic matching are not inferred.",
    ),
    "aiken": (
        "PNNNN",
        "fail/expect are failure syntax; validator outcomes and evaluation effects are not proved.",
    ),
    "cairo": (
        "PNNUN",
        "Named panic candidates are uncertain; result effects and transaction rollback are "
        "not inferred.",
    ),
    "circom": (
        "PNNNN",
        "Assertion candidates are distinct from constraint enforcement; witness and proof "
        "outcomes are not established.",
    ),
    "haskell": (
        "PNPPN",
        "Named applications are uncertain; laziness, saturation, masking and asynchronous "
        "exceptions are not resolved.",
    ),
    "julia": (
        "PNPPN",
        "Named throw/rethrow/error calls are uncertain; task failures and finalizers are not "
        "inferred.",
    ),
    "erlang": (
        "PPPPN",
        "Exception classes and after syntax are retained; process exit signals and catch "
        "dispatch are not resolved.",
    ),
    "masm": (
        "PNNNN",
        "Assertion instructions may fail; VM failure and proof validity are not modeled as "
        "typed exceptions.",
    ),
    "swift": (
        "PPPPP",
        "throws and throw remain distinct; typed throws grammar coverage, try variants and "
        "cancellation are incomplete.",
    ),
    "objc": (
        "PPPPN",
        "@throw/@catch/@finally are retained; ARC cleanup and Objective-C++ exception "
        "interoperability are not inferred.",
    ),
    "kotlin": (
        "PPPPU",
        "Throw expressions are retained; @Throws, resource use and coroutine cancellation "
        "effects are not inferred.",
    ),
    "dart": (
        "PPPPN",
        "Throw/rethrow and on-only handlers are retained; futures, zones and async cleanup "
        "are incomplete.",
    ),
    "move": (
        "PNNNN",
        "abort syntax is retained; transaction rollback and returned Result-like values are "
        "not inferred.",
    ),
    "tact": (
        "PNPUN",
        "Named throws are uncertain; VM exit codes, bounce handling and message-state effects "
        "are not resolved.",
    ),
    "func": (
        "PNPUN",
        "Try/catch syntax and uncertain throw calls are retained; VM stack restoration and "
        "exit codes are not inferred.",
    ),
    "sway": (
        "PNNUN",
        "Named revert/assert candidates are uncertain; contract rollback and external call "
        "failure are not inferred.",
    ),
    "rego": (
        "UNNNN",
        "Undefined policy results and builtin evaluation errors require runtime "
        "configuration; no exception events are emitted.",
    ),
    "proto": (
        "NNNNN",
        "This frontend describes schemas; RPC implementation failures are outside the input.",
    ),
    "thrift": (
        "PPNNP",
        "Declared exceptions describe the RPC contract, not implementation throws or runtime "
        "handling.",
    ),
    "graphql": (
        "NNNNN",
        "This frontend describes schemas; resolver exceptions and null propagation are "
        "outside the input.",
    ),
    "sql": (
        "UUUUU",
        "PL/pgSQL RAISE bodies produce syntax diagnostics; dialect-specific handlers and "
        "transactional effects remain unsupported.",
    ),
}
_STATUS = {
    "P": ExceptionSupportStatus.PARTIAL,
    "U": ExceptionSupportStatus.UNSUPPORTED,
    "N": ExceptionSupportStatus.NOT_APPLICABLE,
}


def exception_support_for(language: str) -> ExceptionSupport:
    """Describe a frontend; unknown/imported graph languages remain unverified."""
    canonical = "c_sharp" if language == "csharp" else language
    profile = _PROFILES.get(canonical)
    if profile is None:
        return ExceptionSupport(
            canonical,
            *([ExceptionSupportStatus.UNVERIFIED] * 8),
            notes="No reviewed exception capability profile for this language.",
        )
    flags, notes = profile
    runtime = (
        ExceptionSupportStatus.NOT_APPLICABLE
        if canonical
        in {
            "proto",
            "graphql",
            "thrift",
        }
        else ExceptionSupportStatus.UNSUPPORTED
    )
    return ExceptionSupport(
        canonical, *(_STATUS[flag] for flag in flags), runtime, runtime, runtime, notes
    )
