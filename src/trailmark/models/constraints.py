"""Versioned constraint claims and source occurrences, independent of inference."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import PurePosixPath
from typing import Literal

from trailmark.models.nodes import SourceLocation

type ConstraintUnit = Literal["bytes", "codepoints", "utf16_code_units", "elements"]
type ConstraintSort = Literal["bytes", "text", "integer", "real", "boolean", "null"]


def _named(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_digest(value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError("Expected a lowercase SHA-256 digest")


def _source_span(location: SourceLocation) -> None:
    if (
        not isinstance(location, SourceLocation)
        or not _named(location.file_path)
        or type(location.start_line) is not int
        or type(location.end_line) is not int
        or location.start_line < 1
        or location.end_line < location.start_line
        or any(
            column is not None and (type(column) is not int or column < 0)
            for column in (location.start_col, location.end_col)
        )
        or location.start_line == location.end_line
        and location.start_col is not None
        and location.end_col is not None
        and location.end_col < location.start_col
    ):
        raise ValueError("Invalid source evidence span")


@dataclass(frozen=True)
class ConstraintLiteral:
    """A typed constant; byte values use lowercase hexadecimal, never implicit UTF-8."""

    sort: ConstraintSort
    value: str | int | float | bool | None

    def __post_init__(self) -> None:
        expected = {
            "bytes": str,
            "text": str,
            "integer": int,
            "real": float,
            "boolean": bool,
            "null": type(None),
        }
        if self.sort not in expected or type(self.value) is not expected[self.sort]:
            raise ValueError("Constraint literal has an invalid sort or value")
        if (
            self.sort == "bytes"
            and isinstance(self.value, str)
            and (len(self.value) % 2 or any(c not in "0123456789abcdef" for c in self.value))
        ):
            raise ValueError("Byte literals require lowercase hexadecimal")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("Constraint real values must be finite")


@dataclass(frozen=True)
class ConstraintSlice:
    """A nonnegative, half-open, clamped slice in explicitly named units."""

    start: int
    stop: int | None
    unit: ConstraintUnit

    def __post_init__(self) -> None:
        if (
            type(self.start) is not int
            or self.start < 0
            or self.stop is not None
            and (type(self.stop) is not int or self.stop < self.start)
        ):
            raise ValueError("Constraint slices require ordered nonnegative bounds")
        if self.unit not in {"bytes", "codepoints", "utf16_code_units", "elements"}:
            raise ValueError("Unknown constraint slice unit")


@dataclass(frozen=True)
class ArgumentRef:
    """An argument's source-order index, or the explicit receiver, at one call site."""

    index: int | None = None
    receiver: bool = False
    slices: tuple[ConstraintSlice, ...] = ()

    def __post_init__(self) -> None:
        if type(self.receiver) is not bool or (
            self.receiver
            and self.index is not None
            or not self.receiver
            and (type(self.index) is not int or self.index < 0)
        ):
            raise ValueError("Select one argument index or the receiver")
        if not isinstance(self.slices, tuple) or any(
            not isinstance(item, ConstraintSlice) for item in self.slices
        ):
            raise ValueError("Constraint slices must be an immutable tuple")


@dataclass(frozen=True)
class ParameterRef:
    """A callee parameter in a summary, never a source operand in a stored claim."""

    parameter: int
    slices: tuple[ConstraintSlice, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.parameter) is not int
            or self.parameter < 0
            or not isinstance(self.slices, tuple)
            or any(not isinstance(item, ConstraintSlice) for item in self.slices)
        ):
            raise ValueError("Invalid summary parameter")


@dataclass(frozen=True)
class ConstraintPredicate:
    """A predicate over one subject, without executing source code or regular expressions.

    Ranges and two-value lengths have inclusive bounds. Character sets constrain
    every element, so the empty sequence satisfies a charset predicate.
    """

    subject: ArgumentRef | ParameterRef
    operation: Literal["type", "equals", "one_of", "range", "length", "prefix", "suffix", "charset"]
    values: tuple[ConstraintLiteral, ...]
    unit: ConstraintUnit | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.subject, (ArgumentRef, ParameterRef))
            or not isinstance(self.values, tuple)
            or any(not isinstance(item, ConstraintLiteral) for item in self.values)
        ):
            raise ValueError("Invalid constraint subject or operands")
        if self.operation not in _PREDICATE_ARITIES:
            raise ValueError("Invalid constraint operation or operand count")
        arities = _PREDICATE_ARITIES[self.operation]
        if not self.values or (arities and len(self.values) not in arities):
            raise ValueError("Invalid constraint operation or operand count")
        if len({item.sort for item in self.values}) != 1:
            raise ValueError("Constraint operands must have one sort")
        for validate in _PREDICATE_VALIDATORS[self.operation]:
            validate(self)
        _validate_predicate_units(self)
        _validate_sliced_predicate(self)


def _validate_type_predicate(predicate: ConstraintPredicate) -> None:
    value = predicate.values[0]
    if value.sort != "text" or value.value not in {
        "bytes",
        "text",
        "integer",
        "real",
        "boolean",
        "null",
        "sequence",
        "mapping",
    }:
        raise ValueError("Unknown constrained type")


def _validate_bounds(predicate: ConstraintPredicate) -> None:
    sort = predicate.values[0].sort
    allowed = {"integer"} if predicate.operation == "length" else {"integer", "real"}
    if sort not in allowed:
        raise ValueError("Bounds require numeric operands")
    bounds = [item.value for item in predicate.values if isinstance(item.value, (int, float))]
    if len(bounds) == 2 and bounds[0] > bounds[1]:
        raise ValueError("Constraint bounds are reversed")
    if predicate.operation == "length" and any(value < 0 for value in bounds):
        raise ValueError("Lengths cannot be negative")


def _validate_sequence_predicate(predicate: ConstraintPredicate) -> None:
    sort = predicate.values[0].sort
    if sort not in {"bytes", "text"}:
        raise ValueError("Sequence predicates require byte or text operands")
    if predicate.unit != {"bytes": "bytes", "text": "codepoints"}[sort]:
        raise ValueError("Sequence literal and predicate units differ")


def _validate_predicate_units(predicate: ConstraintPredicate) -> None:
    needs_unit = predicate.operation in {"length", "prefix", "suffix", "charset"}
    slice_units = {item.unit for item in predicate.subject.slices}
    if len(slice_units) > 1 or (slice_units and needs_unit and slice_units != {predicate.unit}):
        raise ValueError("Constraint slice and predicate units differ")
    if needs_unit and predicate.unit not in {"bytes", "codepoints", "utf16_code_units", "elements"}:
        raise ValueError("Sequence predicates require an explicit unit")
    if not needs_unit and predicate.unit is not None:
        raise ValueError("This predicate does not take a unit")


def _validate_sliced_predicate(predicate: ConstraintPredicate) -> None:
    if not predicate.subject.slices:
        return
    unit = predicate.subject.slices[0].unit
    sort = {"bytes": "bytes", "codepoints": "text", "utf16_code_units": "text"}.get(unit)
    if predicate.operation == "range":
        raise ValueError("Numeric ranges cannot constrain a sequence slice")
    if predicate.operation in {"equals", "one_of"} and predicate.values[0].sort != sort:
        raise ValueError("Constraint literal sort cannot describe this sequence slice")
    if predicate.operation == "type" and predicate.values[0].value not in {sort, "sequence"}:
        raise ValueError("Constrained type cannot describe this sequence slice")


_PREDICATE_ARITIES = {
    "type": (1,),
    "equals": (1,),
    "one_of": (),
    "range": (2,),
    "length": (1, 2),
    "prefix": (1,),
    "suffix": (1,),
    "charset": (1,),
}
_PREDICATE_VALIDATORS = {
    "type": (_validate_type_predicate,),
    "equals": (),
    "one_of": (),
    "range": (_validate_bounds,),
    "length": (_validate_bounds,),
    "prefix": (_validate_sequence_predicate,),
    "suffix": (_validate_sequence_predicate,),
    "charset": (_validate_sequence_predicate,),
}


@dataclass(frozen=True)
class ConstraintBoolean:
    """Boolean composition; alternatives remain separate rather than intersecting callers."""

    operation: Literal["true", "false", "and", "or", "not"]
    terms: tuple[ConstraintExpr, ...] = ()

    def __post_init__(self) -> None:
        if (
            self.operation not in {"true", "false", "and", "or", "not"}
            or not isinstance(self.terms, tuple)
            or any(
                not isinstance(term, (ConstraintPredicate, ConstraintBoolean))
                for term in self.terms
            )
        ):
            raise ValueError("Invalid constraint composition")
        if (
            self.operation in {"true", "false"}
            and self.terms
            or self.operation == "not"
            and len(self.terms) != 1
            or self.operation in {"and", "or"}
            and not self.terms
        ):
            raise ValueError("Invalid Boolean constraint arity")


type ConstraintExpr = ConstraintPredicate | ConstraintBoolean


def _source_text(text: str, raw: str | None) -> None:
    if not isinstance(text, str):
        raise ValueError("Source display text must be a string")
    if raw is None:
        return
    if not isinstance(raw, str):
        raise ValueError("Source bytes must be lowercase hexadecimal")
    try:
        data = bytes.fromhex(raw)
    except ValueError as error:
        raise ValueError("Source bytes must be lowercase hexadecimal") from error
    if data.hex() != raw or data.decode(errors="backslashreplace") != text:
        raise ValueError("Source bytes must match the escaped display text")


@dataclass(frozen=True)
class CallArgument:
    """An observed operand; its source index does not assert a formal parameter binding."""

    index: int
    expression: str
    location: SourceLocation
    kind: Literal["positional", "named", "unpacked", "implicit"] = "positional"
    name: str | None = None
    expression_bytes: str | None = None

    def __post_init__(self) -> None:
        _source_span(self.location)
        _source_text(self.expression, self.expression_bytes)
        if (
            type(self.index) is not int
            or self.index < 0
            or self.kind not in {"positional", "named", "unpacked", "implicit"}
        ):
            raise ValueError("Invalid call argument")
        if not self.expression or (
            not _named(self.name) if self.kind == "named" else self.name is not None
        ):
            raise ValueError("Call arguments require expressions and consistent labels")


@dataclass(frozen=True)
class CallSite:
    """One source call occurrence. Target bindings are queried from located graph edges."""

    caller_id: str
    language: str
    location: SourceLocation
    source_sha256: str
    syntax: str
    arguments: tuple[CallArgument, ...] = ()
    receiver: CallArgument | None = None
    execution: Literal["call", "deferred", "unknown"] = "call"
    gaps: tuple[str, ...] = ()
    syntax_bytes: str | None = None
    source_key: tuple[str, str] = field(kw_only=True)

    def __post_init__(self) -> None:
        _source_span(self.location)
        _source_text(self.syntax, self.syntax_bytes)
        validate_digest(self.source_sha256)
        _validate_source_key(self.source_key)
        if (
            not _named(self.caller_id)
            or not _named(self.language)
            or self.execution not in {"call", "deferred", "unknown"}
        ):
            raise ValueError("Invalid call-site owner, language, or execution")
        if (
            not isinstance(self.arguments, tuple)
            or any(not isinstance(argument, CallArgument) for argument in self.arguments)
            or tuple(arg.index for arg in self.arguments) != tuple(range(len(self.arguments)))
            or self.receiver is not None
            and not isinstance(self.receiver, CallArgument)
            or not isinstance(self.gaps, tuple)
            or any(not _named(gap) for gap in self.gaps)
            or len(set(self.gaps)) != len(self.gaps)
        ):
            raise ValueError("Call arguments require contiguous source-order indices")

    @cached_property
    def id(self) -> str:
        caller = self.caller_id.removeprefix(f"{self.source_key[0]}::")
        caller = caller.partition(":")[2] or caller
        payload = {
            "caller": caller,
            "language": self.language,
            "source_key": self.source_key,
            "location": (
                self.location.start_line,
                self.location.end_line,
                self.location.start_col,
                self.location.end_col,
            ),
            "source_sha256": self.source_sha256,
        }
        return (
            "call:" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24]
        )


def _validate_source_key(key: tuple[str, str]) -> None:
    if not isinstance(key, tuple) or len(key) != 2 or any(not _named(part) for part in key):
        raise ValueError("Call-site source key requires a root name and relative file")
    root, path = key
    relative = PurePosixPath(path)
    if ":" in root or relative.is_absolute() or ".." in relative.parts or path == ".":
        raise ValueError("Call-site source key must be relative to its named root")
    if relative.as_posix() != path:
        raise ValueError("Call-site source key must use a normalized relative path")


@dataclass(frozen=True)
class ParameterBinding:
    """Source signature binding mode, without asserting runtime dispatch."""

    name: str
    kind: Literal[
        "positional_only",
        "positional_or_keyword",
        "keyword_only",
        "var_positional",
        "var_keyword",
        "unknown",
    ]
    value_preserving: bool = False

    def __post_init__(self) -> None:
        if type(self.value_preserving) is not bool:
            raise ValueError("Value preservation must be a Boolean")
        if not _named(self.name) or self.kind not in {
            "positional_only",
            "positional_or_keyword",
            "keyword_only",
            "var_positional",
            "var_keyword",
            "unknown",
        }:
            raise ValueError("Invalid parameter binding mode")


@dataclass(frozen=True)
class ConstraintEvidence:
    location: SourceLocation
    source_sha256: str

    def __post_init__(self) -> None:
        _source_span(self.location)
        validate_digest(self.source_sha256)


@dataclass(frozen=True)
class ConstraintClaim:
    """A source-bound assertion. Reviewed is an attestation, never a machine proof."""

    call_site_id: str
    predicate: ConstraintExpr
    graph_fingerprint: str
    semantics: str
    evidence: tuple[ConstraintEvidence, ...]
    assumptions: tuple[str, ...] = ()
    justification: Literal["asserted", "reviewed"] = "asserted"
    grammar_version: int = 1

    def __post_init__(self) -> None:
        validate_digest(self.graph_fingerprint)
        if (
            not _named(self.call_site_id)
            or not _named(self.semantics)
            or not self.evidence
            or type(self.grammar_version) is not int
            or self.grammar_version != 1
            or self.justification not in {"asserted", "reviewed"}
            or not isinstance(self.predicate, (ConstraintPredicate, ConstraintBoolean))
        ):
            raise ValueError("Invalid constraint claim")
        if (
            not isinstance(self.evidence, tuple)
            or any(not isinstance(item, ConstraintEvidence) for item in self.evidence)
            or not isinstance(self.assumptions, tuple)
            or any(not isinstance(item, str) or not item.strip() for item in self.assumptions)
            or len(set(self.assumptions)) != len(self.assumptions)
        ):
            raise ValueError(
                "Constraint evidence and named assumptions must be immutable and distinct"
            )


@dataclass(frozen=True)
class ConstraintSummary:
    node_id: str
    parameter: int
    predicate: ConstraintExpr
    assumptions: tuple[str, ...]
    gaps: tuple[str, ...]
    call_site_ids: tuple[str, ...]
    semantics: tuple[str, ...]
    status: Literal["conditional", "unknown"]
    graph_fingerprint: str
