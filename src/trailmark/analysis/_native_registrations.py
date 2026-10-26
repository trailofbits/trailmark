"""Explicit native registry readers; no application execution or implicit imports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from trailmark.analysis._registration_context import RegistrationContext
from trailmark.models.endpoints import EndpointRegistration, RegistrationEvidence, registration_id
from trailmark.models.graph import CodeGraph


def import_native_registrations(
    graph: CodeGraph,
    path: str,
    *,
    framework: str,
    source_root: str | None = None,
    path_mappings: tuple[tuple[str, str], ...] = (),
) -> int:
    """Read a caller-selected native snapshot, preserving every registry row.

    File mappings are explicit prefix mappings, needed when a snapshot names
    container paths. They never resolve targets by terminal name alone.
    """
    context = RegistrationContext(graph)
    payload = json.loads(Path(path).read_text())
    root = str(Path(source_root or graph.root_path).resolve())
    rows: list[tuple[str, dict[str, Any]]]
    if framework == "symfony":
        if not isinstance(payload, dict):
            raise ValueError("Symfony debug:router export must be an object")
        rows = list(payload.items())
    elif framework in {"django", "medusa"}:
        if not isinstance(payload, dict) or not isinstance(payload.get("routes"), list):
            raise ValueError("Native registry must contain a routes array")
        rows = [(str(i), row) for i, row in enumerate(payload["routes"])]
    elif framework == "magento":
        if not isinstance(payload, list):
            raise ValueError("Magento registry must be an array")
        rows = [(str(i), row) for i, row in enumerate(payload)]
    else:
        raise ValueError(f"Unsupported native registry format: {framework}")
    registrations: list[EndpointRegistration] = []
    service_evidence: dict[str, list[RegistrationEvidence]] = {}
    service_aliases: dict[str, set[str]] = {}
    if framework == "symfony":
        from trailmark.analysis._symfony_registrations import _php_services

        service_aliases = _php_services(context, service_evidence)
    occurrences: dict[str, int] = {}
    for key, row in rows:
        if not isinstance(row, dict):
            raise ValueError("Native registry rows must be objects")
        file = None
        line = None
        acl: tuple[str, ...] = ()
        namespace = None
        if framework == "django":
            route_path = row.get("path_pattern")
            methods = row.get("methods") or [None]
            symbol = row.get("handler")
            file, line = row.get("file"), row.get("line")
            name = row.get("name")
            if name is not None and not isinstance(name, str):
                raise ValueError("Native Django route name must be a string or null")
            namespace = (name or "").rpartition(":")[0] or None
        elif framework == "symfony":
            route_path = row.get("path")
            value = row.get("method") or "ANY"
            if not isinstance(value, str):
                raise ValueError("Native Symfony method must be a string or null")
            methods = value.split("|") if value != "ANY" else [None]
            defaults = row.get("defaults")
            symbol = defaults.get("_controller") if isinstance(defaults, dict) else None
        elif framework == "magento":
            route_path = row.get("path")
            methods = row.get("methods") or [None]
            service = row.get("service") or {}
            if (
                not isinstance(service, dict)
                or service
                and not all(isinstance(service.get(key), str) for key in ("class", "method"))
            ):
                raise ValueError("Native Magento service requires class and method strings")
            controller = row.get("controller")
            controller_file = row.get("controller_file")
            if any(
                value is not None and not isinstance(value, str)
                for value in (controller, controller_file)
            ):
                raise ValueError("Native Magento controller and file must be strings or null")
            symbol = (
                f"{service['class']}::{service['method']}"
                if service
                else f"{controller}::execute"
                if controller
                else None
            )
            file = str(Path(root) / controller_file) if controller_file else None
            namespace = row.get("area")
            resources = row.get("acl_resources") or []
            if not isinstance(resources, list) or any(not isinstance(r, str) for r in resources):
                raise ValueError("Native Magento ACL resources must be a list of strings")
            acl = tuple(resources)
        else:
            route_path = row.get("matcher")
            methods = [row.get("method")]
            symbol = row.get("handler")
            file = row.get("absolutePath")
        if symbol is not None and not isinstance(symbol, str):
            raise ValueError("Native handler must be a string or null")
        if namespace is not None and not isinstance(namespace, str):
            raise ValueError("Native namespace must be a string or null")
        if file:
            if not isinstance(file, str):
                raise ValueError("Native source file must be a string")
            for before, after in path_mappings:
                if Path(file).is_relative_to(before):
                    file = str(Path(after) / Path(file).relative_to(before))
                    break
        if line is not None and (type(line) is not int or line < 1):
            raise ValueError("Native source line must be a positive integer")
        if not isinstance(methods, (list, tuple)) or any(
            m is not None and not isinstance(m, str) for m in methods
        ):
            raise ValueError("Native methods must be a list of strings")
        if route_path is not None and not isinstance(route_path, str):
            raise ValueError("Native route path must be a string or null")
        bindings = context.bind(symbol, file=file, line=line, allow_class=True)
        static_evidence: tuple[RegistrationEvidence, ...] = ()
        if framework == "symfony" and all(b.status != "resolved" for b in bindings):
            service, _, method_name = (symbol or "").partition("::")
            aliases = service_aliases.get(service, set())
            if len(aliases) == 1:
                bindings = context.bind(next(iter(aliases)) + "::" + (method_name or "__invoke"))
                static_evidence = tuple(service_evidence.get(service, ()))
        if framework == "symfony" and all(b.status != "resolved" for b in bindings):
            matches = [
                r
                for r in graph.registrations.values()
                if r.framework == "symfony"
                and r.registration_key == key
                and r.path == route_path
                and any(e.source == "static" for e in r.evidence)
            ]
            if matches:
                bindings = tuple(dict.fromkeys(b for r in matches for b in r.bindings))
                static_evidence = tuple(dict.fromkeys(e for r in matches for e in r.evidence))
        # Keep one native row as one registration. A multi-method row has an
        # explicit list in its provenance; method-specific comparisons can
        # expand it without losing the registry's original census unit.
        detail = json.dumps(row, sort_keys=True, separators=(",", ":"))
        row_key = key if framework == "symfony" else registration_id(framework, detail)
        occurrence = occurrences.get(row_key, 0)
        occurrences[row_key] = occurrence + 1
        stable_key = f"{row_key}:{occurrence}"
        method = methods[0] if len(methods) == 1 else None
        registrations.append(
            EndpointRegistration(
                registration_id("native", framework, root, stable_key),
                "http",
                root,
                (RegistrationEvidence("native", detail=detail), *static_evidence),
                bindings,
                framework=framework,
                namespace=namespace,
                path=route_path,
                method=method,
                transport="graphql-http" if row.get("kind") == "graphql_transport" else "http",
                enabled=row.get("enabled", True),
                confidence="certain",
                registration_key=stable_key,
                acl=acl,
                methods=tuple(m for m in methods if m is not None),
            )
        )
    # Validate and stage the whole snapshot before touching the graph.
    for registration in registrations:
        existing = graph.registrations.get(registration.id)
        if existing is not None and existing != registration:
            raise ValueError(f"Conflicting native snapshot: {registration.id}")
    for registration in registrations:
        context.add(registration)
    return len(registrations)
