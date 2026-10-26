"""Language-neutral evidence for reported entrypoints and unresolved candidates."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Literal

from trailmark.models.nodes import Attribute, SourceLocation


@dataclass(frozen=True)
class EntrypointEvidence:
    """One source observation, without asserting that a candidate executes.

    Multiple observations can target one node. An unresolved symbol can have
    no node ID; ambiguous references retain one observation per possible target.
    ``unresolved`` records the facts needed before claiming external execution.
    """

    node_id: str | None
    basis: Literal["language", "declaration", "heuristic", "reference", "override"]
    rule: str
    location: SourceLocation
    source_id: str | None = None
    symbol: str | None = None
    binding: Literal["resolved", "unresolved", "ambiguous"] = "resolved"
    details: tuple[Attribute, ...] = ()
    arguments: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.basis not in {"language", "declaration", "heuristic", "reference", "override"}:
            raise ValueError(f"Unknown entrypoint evidence basis: {self.basis}")
        if self.binding not in {"resolved", "unresolved", "ambiguous"}:
            raise ValueError(f"Unknown entrypoint binding: {self.binding}")
        if self.binding == "resolved" and self.node_id is None:
            raise ValueError("Resolved evidence requires a node ID")
        if not self.rule:
            raise ValueError("Entrypoint evidence requires a rule")

    @property
    def id(self) -> str:
        """Stable identity includes the source occurrence, not only its handler."""
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return "entrypoint:" + hashlib.sha256(payload.encode()).hexdigest()[:24]
