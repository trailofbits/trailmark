"""Source-ranged, reviewable execution facts for authentication boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from trailmark.models.endpoints import RegistrationEvidence
from trailmark.models.nodes import SourceLocation


@dataclass(frozen=True)
class ExecutionPoint:
    """A precise program point inside a code unit; line ranges are inclusive."""

    node_id: str
    location: SourceLocation
    role: Literal[
        "credential",
        "gate",
        "rejection",
        "continuation",
        "handler",
        "middleware",
        "dispatch",
        "unknown",
    ] = "unknown"

    def __post_init__(self) -> None:
        if self.role not in {
            "credential",
            "gate",
            "rejection",
            "continuation",
            "handler",
            "middleware",
            "dispatch",
            "unknown",
        }:
            raise ValueError(f"Unknown execution point role: {self.role}")
        loc = self.location
        if loc.start_line < 1 or loc.end_line < loc.start_line:
            raise ValueError("Invalid execution source range")
        if any(c is not None and c < 0 for c in (loc.start_col, loc.end_col)):
            raise ValueError("Invalid execution source column")
        if (
            loc.start_line == loc.end_line
            and loc.start_col is not None
            and loc.end_col is not None
            and loc.end_col < loc.start_col
        ):
            raise ValueError("Invalid execution source columns")


@dataclass(frozen=True)
class ExecutionFlow:
    """One evidenced transition; ordinary calls and trust tags imply no gate.

    Facts are supplied by an adapter or a reviewer, with their source kept
    explicit. User identity is established only on a successful user gate.
    """

    id: str
    source: ExecutionPoint
    target: ExecutionPoint
    kind: Literal["dispatch", "middleware", "credential", "gate_success", "gate_reject"]
    evidence: tuple[RegistrationEvidence, ...]
    identity: Literal["user", "capability", "csrf", "none"] = "none"
    confidence: Literal["certain", "inferred", "uncertain"] = "uncertain"
    reviewed: bool = False
    order: int | None = None
    registration_id: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"dispatch", "middleware", "credential", "gate_success", "gate_reject"}:
            raise ValueError(f"Unknown execution flow kind: {self.kind}")
        if self.identity not in {"user", "capability", "csrf", "none"}:
            raise ValueError(f"Unknown identity evidence: {self.identity}")
        if self.confidence not in {"certain", "inferred", "uncertain"}:
            raise ValueError(f"Unknown execution confidence: {self.confidence}")
        if type(self.reviewed) is not bool:
            raise ValueError("reviewed must be a boolean")
        if self.order is not None and (type(self.order) is not int or self.order < 0):
            raise ValueError("Middleware order must be a nonnegative integer")
        if not self.evidence:
            raise ValueError("An execution flow requires evidence")
        if self.source.role == "rejection":
            raise ValueError("A rejection sink cannot have a successful continuation")
        if self.kind in {"gate_success", "gate_reject"} and self.source.role != "gate":
            raise ValueError("A gate transition must start at a gate point")
        if self.kind == "gate_reject" and self.target.role != "rejection":
            raise ValueError("Gate rejection must end at a rejection point")
        if self.kind == "gate_success" and self.target.role in {"credential", "gate", "rejection"}:
            raise ValueError(
                "Gate success must target a continuation, not gate evaluation or rejection"
            )
        if self.identity != "none" and self.kind != "gate_success":
            raise ValueError("Only a successful gate can establish identity")
