"""Versioned registration records, separate from generic entrypoint tags."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Literal

from trailmark.models.nodes import SourceLocation

RegistrationKind = Literal["http", "graphql", "cli", "task", "template", "entrypoint"]
BindingStatus = Literal["resolved", "unresolved", "out_of_root", "unsupported", "ambiguous"]


@dataclass(frozen=True)
class RegistrationEvidence:
    """An independently attributable declaration or native registration."""

    source: Literal["static", "native", "manual"]
    location: SourceLocation | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        if self.source not in {"static", "native", "manual"}:
            raise ValueError(f"Unknown evidence source: {self.source}")


@dataclass(frozen=True)
class HandlerBinding:
    """A target reference and the outcome of resolving it in selected roots."""

    symbol: str | None = None
    node_id: str | None = None
    location: SourceLocation | None = None
    status: BindingStatus = "unresolved"

    def __post_init__(self) -> None:
        if self.status not in {"resolved", "unresolved", "out_of_root", "unsupported", "ambiguous"}:
            raise ValueError(f"Unknown binding status: {self.status}")
        if self.status == "resolved" and self.node_id is None:
            raise ValueError("A resolved handler requires a node ID")


@dataclass(frozen=True)
class EndpointRegistration:
    """One registration; multiple registrations may bind the same handler.

    ``None`` means unknown, including deployment state and service namespace.
    Schema presence and static route conventions never establish activation.
    """

    id: str
    kind: RegistrationKind
    owning_root: str
    evidence: tuple[RegistrationEvidence, ...]
    bindings: tuple[HandlerBinding, ...] = ()
    framework: str | None = None
    namespace: str | None = None
    method: str | None = None
    path: str | None = None
    operation_type: str | None = None
    operation_name: str | None = None
    transport: str | None = None
    enabled: bool | None = None
    confidence: Literal["certain", "inferred", "uncertain"] = "inferred"
    registration_key: str | None = None
    acl: tuple[str, ...] = ()
    schema_version: int = 1
    methods: tuple[str, ...] = ()
    declaration_nodes: tuple[str, ...] = ()
    transport_registrations: tuple[str, ...] = ()
    schema_binding: str | None = None
    declared_enabled: bool | None = None

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("Unsupported endpoint registration schema version")
        if not self.evidence:
            raise ValueError("A registration requires provenance")
        if self.kind not in {"http", "graphql", "cli", "task", "template", "entrypoint"}:
            raise ValueError(f"Unknown registration kind: {self.kind}")
        if self.confidence not in {"certain", "inferred", "uncertain"}:
            raise ValueError(f"Unknown registration confidence: {self.confidence}")
        if self.enabled is not None and type(self.enabled) is not bool:
            raise ValueError("enabled must be a boolean or null")
        if self.declared_enabled is not None and type(self.declared_enabled) is not bool:
            raise ValueError("declared_enabled must be a boolean or null")
        if self.kind != "http" and (
            self.method is not None or self.path is not None or self.methods
        ):
            raise ValueError("Only HTTP registrations have method/path identity")
        if self.enabled is not None and not any(e.source == "native" for e in self.evidence):
            raise ValueError("Deployment state requires explicit native registration evidence")

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible record, including explicit unknowns."""
        return json.loads(json.dumps(asdict(self)))

    @classmethod
    def from_dict(cls, record: dict[str, object]) -> EndpointRegistration:
        """Load the versioned export, rejecting invalid or unknown fields."""
        from trailmark.models._endpoint_json import registration_from_dict

        return registration_from_dict(record)


def registration_id(*parts: object) -> str:
    """Derive a deterministic identity from registration-specific evidence."""
    payload = json.dumps(parts, sort_keys=True, separators=(",", ":"))
    return "registration:" + hashlib.sha256(payload.encode()).hexdigest()[:24]
