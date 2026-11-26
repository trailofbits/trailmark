"""Versioned constraint claims and source occurrences, independent of inference."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Literal

from trailmark.models.nodes import SourceLocation

type ConstraintUnit = Literal["bytes", "codepoints", "utf16_code_units", "elements"]
type ConstraintSort = Literal["bytes", "text", "integer", "real", "boolean", "null"]


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
        or not location.file_path
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
        arities = {
            "type": (1,),
            "equals": (1,),
            "range": (2,),
            "length": (1, 2),
            "prefix": (1,),
            "suffix": (1,),
            "charset": (1,),
        }
        if (
            not isinstance(self.subject, (ArgumentRef, ParameterRef))
            or not isinstance(self.values, tuple)
            or any(not isinstance(item, ConstraintLiteral) for item in self.values)
        ):
            raise ValueError("Invalid constraint subject or operands")
        if (
            self.operation != "one_of"
            and (self.operation not in arities or len(self.values) not in arities[self.operation])
            or not self.values
        ):
            raise ValueError("Invalid constraint operation or operand count")
        if len({item.sort for item in self.values}) != 1:
            raise ValueError("Constraint operands must have one sort")
        sort = self.values[0].sort
        if self.operation == "type" and (
            sort != "text"
            or self.values[0].value
            not in {"bytes", "text", "integer", "real", "boolean", "null", "sequence", "mapping"}
        ):
            raise ValueError("Unknown constrained type")
        if self.operation in {"range", "length"}:
            if sort not in ({"integer"} if self.operation == "length" else {"integer", "real"}):
                raise ValueError("Bounds require numeric operands")
            bounds = [item.value for item in self.values if isinstance(item.value, (int, float))]
            if len(bounds) == 2 and bounds[0] > bounds[1]:
                raise ValueError("Constraint bounds are reversed")
            if self.operation == "length" and any(value < 0 for value in bounds):
                raise ValueError("Lengths cannot be negative")
        if self.operation in {"prefix", "suffix", "charset"} and sort not in {"bytes", "text"}:
            raise ValueError("Sequence predicates require byte or text operands")
        needs_unit = self.operation in {"length", "prefix", "suffix", "charset"}
        slice_units = {item.unit for item in self.subject.slices}
        if len(slice_units) > 1 or (slice_units and needs_unit and slice_units != {self.unit}):
            raise ValueError("Constraint slice and predicate units differ")
        if needs_unit and self.unit not in {"bytes", "codepoints", "utf16_code_units", "elements"}:
            raise ValueError("Sequence predicates require an explicit unit")
        if not needs_unit and self.unit is not None:
            raise ValueError("This predicate does not take a unit")
        if self.operation in {"prefix", "suffix", "charset"} and (
            sort == "bytes" and self.unit != "bytes" or sort == "text" and self.unit != "codepoints"
        ):
            raise ValueError("Sequence literal and predicate units differ")


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
    if raw is None:
        return
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
        if not self.expression or (self.kind == "named") != bool(self.name):
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

    def __post_init__(self) -> None:
        _source_span(self.location)
        _source_text(self.syntax, self.syntax_bytes)
        validate_digest(self.source_sha256)
        if (
            not self.caller_id
            or not self.language
            or self.execution not in {"call", "deferred", "unknown"}
        ):
            raise ValueError("Invalid call-site owner, language, or execution")
        if (
            not isinstance(self.arguments, tuple)
            or any(not isinstance(argument, CallArgument) for argument in self.arguments)
            or tuple(arg.index for arg in self.arguments) != tuple(range(len(self.arguments)))
            or not isinstance(self.gaps, tuple)
        ):
            raise ValueError("Call arguments require contiguous source-order indices")

    @property
    def id(self) -> str:
        payload = {
            "caller": self.caller_id,
            "language": self.language,
            "location": asdict(self.location),
            "source_sha256": self.source_sha256,
        }
        return (
            "call:" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24]
        )


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
        if not self.name or self.kind not in {
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
            not self.call_site_id
            or not self.semantics
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
