"""Edge types for the code graph."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from trailmark.models.nodes import Attribute, SourceLocation


class EdgeKind(Enum):
    """The kind of relationship between two code units."""

    CALLS = "calls"
    INHERITS = "inherits"
    CONTAINS = "contains"
    IMPORTS = "imports"
    IMPLEMENTS = "implements"
    RESOLVES_TO = "resolves_to"
    TYPE_USES = "type_uses"
    SPECIALIZES = "specializes"
    CORRESPONDS_TO = "corresponds_to"


class EdgeConfidence(Enum):
    """How confident we are that this edge exists at runtime."""

    CERTAIN = "certain"
    INFERRED = "inferred"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class CodeEdge:
    """A directed edge in the code graph."""

    source_id: str
    target_id: str
    kind: EdgeKind
    confidence: EdgeConfidence = EdgeConfidence.CERTAIN
    location: SourceLocation | None = None
    attributes: tuple[Attribute, ...] = ()
