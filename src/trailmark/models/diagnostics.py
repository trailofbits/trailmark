"""Source analysis diagnostics kept separate from inspected-file counts."""

from __future__ import annotations

from dataclasses import dataclass

from trailmark.models.nodes import SourceLocation


@dataclass(frozen=True)
class ParseDiagnostic:
    """A syntax error or explicitly unsupported construct in an inspected file."""

    language: str
    grammar: str
    location: SourceLocation
    kind: str
    message: str
