"""Private lookup state carried through source-graph merges, never exported."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from itertools import count

from trailmark.models.constraints import CallSite
from trailmark.models.edges import CodeEdge

_REFERENCE_IDS = count()


@dataclass(frozen=True)
class Lookup:
    tiers: tuple[tuple[str, ...], ...]
    position: tuple[int, int] = (0, 0)
    atomic: bool = False
    group: str | None = None


@dataclass(frozen=True)
class ArgumentScope:
    callee: int | None
    argument: int | str | None
    parent: str | None


@dataclass(frozen=True)
class PendingReference:
    lookup: Lookup | None = None
    receiver: Lookup | None = None
    field: str | None = None
    application: bool = False
    bound_application: bool = False
    position: int | None = None
    enclosing: str | None = None


@dataclass
class ParseState:
    references: dict[str, PendingReference] = field(default_factory=dict)
    arguments: dict[str, dict[str, ArgumentScope]] = field(default_factory=dict)
    resolved_type_files: set[str] = field(default_factory=set)
    call_sites: dict[str, CallSite] = field(default_factory=dict)

    def stage(self, edge: CodeEdge, reference: PendingReference) -> CodeEdge:
        key = str(next(_REFERENCE_IDS))
        self.references[key] = reference
        return replace(edge, attributes=(*edge.attributes, ("_lookup", key)))

    def reference(self, edge: CodeEdge) -> PendingReference | None:
        key = dict(edge.attributes).get("_lookup")
        return self.references.get(key) if isinstance(key, str) else None

    def merge(self, other: ParseState) -> None:
        self.references.update(other.references)
        for owner, scopes in other.arguments.items():
            self.arguments.setdefault(owner, {}).update(scopes)
        self.resolved_type_files.update(other.resolved_type_files)
        self.call_sites.update(other.call_sites)

    def clear_references(self) -> None:
        self.references.clear()
        self.arguments.clear()
