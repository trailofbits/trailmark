"""Typed reconstruction of endpoint JSON records."""

from __future__ import annotations

from typing import Any

from trailmark.models.endpoints import EndpointRegistration, HandlerBinding, RegistrationEvidence
from trailmark.models.nodes import SourceLocation


def registration_from_dict(record: dict[str, Any]) -> EndpointRegistration:
    values = record.copy()
    values["evidence"] = tuple(
        RegistrationEvidence(**_location(item)) for item in values.get("evidence", ())
    )
    values["bindings"] = tuple(
        HandlerBinding(**_location(item)) for item in values.get("bindings", ())
    )
    values["acl"] = tuple(values.get("acl", ()))
    values["methods"] = tuple(values.get("methods", ()))
    values["declaration_nodes"] = tuple(values.get("declaration_nodes", ()))
    values["transport_registrations"] = tuple(values.get("transport_registrations", ()))
    return EndpointRegistration(**values)


def _location(item: dict[str, Any]) -> dict[str, Any]:
    values = item.copy()
    values["location"] = SourceLocation(**item["location"]) if item.get("location") else None
    return values
