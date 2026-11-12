"""Source evidence for exceptional execution, independent of runtime dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from trailmark.models.edges import EdgeConfidence
from trailmark.models.nodes import SourceLocation, TypeRef


class ExceptionEventKind(StrEnum):
    """What the source explicitly says about exceptional execution."""

    RAISE = "raise"
    RETHROW = "rethrow"
    HANDLER = "handler"
    CLEANUP = "cleanup"
    PROPAGATE = "propagate"
    TERMINATE = "terminate"
    DECLARES = "declares"


@dataclass(frozen=True)
class ExceptionEvent:
    """An owned syntax occurrence, not a proof of propagation or handler matching.

    ``types`` contains syntactic type hints; an empty tuple means unknown, not
    that the operation cannot throw. ``protected_region`` associates a handler
    or finally clause with the lexical try body, without asserting it executes.
    ``expression`` preserves operands whose runtime types cannot be resolved.
    """

    kind: ExceptionEventKind
    location: SourceLocation
    syntax: str
    expression: str | None = None
    types: tuple[TypeRef, ...] = ()
    protected_region: SourceLocation | None = None
    confidence: EdgeConfidence = EdgeConfidence.CERTAIN
