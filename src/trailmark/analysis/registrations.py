"""Static registration discovery and explicit native-evidence ingestion.

Adapters are syntax readers. Ordinary scanning never starts applications.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

from tree_sitter import Parser
from tree_sitter_language_pack import get_language

from trailmark.analysis._python_http import python_http_routes
from trailmark.models.annotations import EntrypointKind, EntrypointTag, TrustLevel
from trailmark.models.endpoints import (
    EndpointRegistration,
    HandlerBinding,
    RegistrationEvidence,
    registration_id,
)
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit, NodeKind, SourceLocation


def _binding(unit: CodeUnit) -> HandlerBinding:
    return HandlerBinding(unit.id, unit.id, unit.location, "resolved")


def _root(graph: CodeGraph, unit: CodeUnit) -> str:
    roots = graph.source_policy.get("roots", [])
    if isinstance(roots, list):
        for root in roots:
            if isinstance(root, dict) and unit.id.startswith(str(root["name"]) + "::"):
                return str(root["path"])
    return graph.root_path


def _record(
    graph: CodeGraph,
    unit: CodeUnit,
    *,
    kind: str,
    framework: str | None,
    location: SourceLocation | None = None,
    method: str | None = None,
    path: str | None = None,
    operation_type: str | None = None,
    operation_name: str | None = None,
    namespace: str | None = None,
    detail: str = "",
    transport: str | None = None,
) -> None:
    from typing import cast

    from trailmark.models.endpoints import RegistrationKind

    loc = location or unit.location
    root = _root(graph, unit)
    identifier = registration_id(
        kind,
        root,
        namespace,
        method,
        path,
        operation_type,
        operation_name,
        unit.id,
        loc.start_line,
        loc.start_col,
    )
    if kind == "graphql":
        identifier = registration_id("graphql", root, namespace, operation_type, operation_name)
    record = EndpointRegistration(
        id=identifier,
        kind=cast(RegistrationKind, kind),
        owning_root=root,
        evidence=(RegistrationEvidence("static", loc, detail),),
        bindings=(HandlerBinding(),) if kind == "graphql" else (_binding(unit),),
        framework=framework,
        namespace=namespace,
        method=method,
        path=path,
        operation_type=operation_type,
        operation_name=operation_name,
        transport=transport,
        methods=(method,) if method else (),
        declaration_nodes=(unit.id,),
    )
    previous = graph.registrations.get(identifier)
    if previous is not None and kind == "graphql":
        record = replace(
            previous,
            evidence=tuple(dict.fromkeys((*previous.evidence, *record.evidence))),
            declaration_nodes=tuple(sorted(set(previous.declaration_nodes) | {unit.id})),
        )
        graph.registrations[identifier] = record
    else:
        graph.add_registration(record)


def discover_registrations(graph: CodeGraph) -> None:
    """Collect registrations without converting unknown deployment into enabled."""
    by_file: dict[str, list[CodeUnit]] = {}
    for unit in graph.nodes.values():
        if unit.kind in {NodeKind.FUNCTION, NodeKind.METHOD}:
            by_file.setdefault(unit.location.file_path, []).append(unit)
    for path, units in sorted(by_file.items()):
        suffix = Path(path).suffix.lower()
        if suffix == ".py":
            indexed = {(u.location.start_line, u.location.start_col): u for u in units}
            for route in python_http_routes(path):
                if not route.framework:
                    if route.reason == "non_http_receiver":
                        continue
                    diagnostic = {
                        "file": path,
                        "line": str(route.location.start_line),
                        "reason": route.reason or "unknown",
                        "receiver": route.receiver,
                    }
                    if diagnostic not in graph.registration_diagnostics:
                        graph.registration_diagnostics.append(diagnostic)
                    continue
                unit = indexed.get((route.handler_line, route.handler_col))
                if unit is not None:
                    for method in route.methods or (None,):
                        _record(
                            graph,
                            unit,
                            kind="http",
                            framework=route.framework,
                            location=route.location,
                            method=method,
                            path=route.path,
                            detail=f"Framework receiver: {route.receiver}",
                            transport=route.transport,
                        )
        elif suffix in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}:
            _file_routes(graph, path, units)
        elif suffix == ".phtml":
            for unit in units:
                if dict(unit.attributes).get("php_execution_kind") == "template":
                    _record(
                        graph,
                        unit,
                        kind="template",
                        framework="php",
                        detail="PHP template body; rendering and deployment unverified",
                    )
        elif suffix in {".graphql", ".gql", ".graphqls"}:
            for unit in units:
                if dict(unit.attributes).get("schema_role") == "root_operation":
                    parent = unit.id.rsplit(":", 1)[-1].rsplit(".", 1)[0]
                    _record(
                        graph,
                        unit,
                        kind="graphql",
                        framework=None,
                        operation_type=parent,
                        operation_name=unit.name,
                        namespace=_schema_namespace(path),
                        detail="Schema declaration; execution unverified",
                    )
    bound = {b.node_id for r in graph.registrations.values() for b in r.bindings}
    bound.update(n for r in graph.registrations.values() for n in r.declaration_nodes)
    for node_id, tag in graph.entrypoints.items():
        if node_id in bound:
            continue
        unit = graph.nodes.get(node_id)
        if unit is None:
            continue
        description = tag.description or ""
        if (
            "CLI" in description
            or "[project.scripts]" in description
            or description in {"Haskell main", "Rust async main (tokio/actix/async-std)"}
        ):
            _record(graph, unit, kind="cli", framework=None, detail=description)
        elif "task queue" in description:
            _record(graph, unit, kind="task", framework="celery", detail=description)
        else:
            _record(
                graph,
                unit,
                kind="entrypoint",
                framework=None,
                detail=description or "Existing source entrypoint; transport unspecified",
            )
    from trailmark.analysis._django_registrations import discover_django
    from trailmark.analysis._magento_registrations import discover_magento
    from trailmark.analysis._registration_context import RegistrationContext
    from trailmark.analysis._symfony_registrations import discover_symfony

    context = RegistrationContext(graph)
    discover_django(context)
    discover_symfony(context)
    discover_magento(context)
    from trailmark.analysis._template_registrations import link_magento_templates

    link_magento_templates(context)
    from trailmark.analysis._graphql_registrations import link_graphql_registrations

    link_graphql_registrations(context)


def _schema_namespace(path: str) -> str | None:
    parts = Path(path).parts
    for marker in ("shop-api", "admin-api"):
        if marker in parts:
            return marker
    file = Path(path)
    if file.suffix == ".graphqls" and (file.parent / "module.xml").is_file():
        return "magento"
    return None


def file_route_framework(path: str) -> str | None:
    """Identify file-route conventions using source/framework evidence."""
    p = Path(path)
    try:
        source = p.read_bytes()
    except OSError:
        return None
    root = Parser(get_language("typescript")).parse(source).root_node
    imports = [
        (node.text or b"").decode(errors="replace").strip("\"'")
        for statement in root.named_children
        if statement.type == "import_statement"
        if (node := statement.child_by_field_name("source")) is not None
    ]
    if any(value.startswith("@medusajs/") for value in imports) and "api" in p.parts:
        return "medusa"
    if any(value == "next" or value.startswith("next/") for value in imports):
        return "nextjs"
    for parent in p.parents:
        manifest = parent / "package.json"
        if not manifest.is_file():
            continue
        try:
            package = json.loads(manifest.read_bytes())
        except (OSError, ValueError):
            return None
        if not isinstance(package, dict):
            return None
        packages = [package.get("dependencies"), package.get("devDependencies")]
        relative = p.relative_to(parent).parts
        if any(isinstance(group, dict) and "next" in group for group in packages) and (
            relative[0] in {"app", "pages"}
            or len(relative) > 1
            and relative[:2] in {("src", "app"), ("src", "pages")}
        ):
            return "nextjs"
        return None
    return None


def _file_routes(graph: CodeGraph, path: str, units: list[CodeUnit]) -> None:
    p = Path(path)
    if p.stem != "route":
        return
    framework = file_route_framework(path)
    parts = p.parent.parts
    anchor = "api" if framework == "medusa" else "app"
    route_path = None
    if framework and anchor in parts:
        segments = list(parts[len(parts) - 1 - list(reversed(parts)).index(anchor) + 1 :])
        route_path = "/" + "/".join(re.sub(r"\[([^\]]+)\]", r":\1", s) for s in segments)
    for unit in units:
        if unit.name not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
            continue
        _record(
            graph,
            unit,
            kind="http",
            framework=framework,
            method=unit.name,
            path=route_path,
            detail="File route declaration; loader activation unverified",
            transport="http",
        )


def mark_registered_entrypoints(graph: CodeGraph) -> None:
    """Expose resolved registrations through the compatible generic API."""
    for registration in graph.registrations.values():
        if registration.kind == "template":
            continue
        if registration.enabled is False or registration.declared_enabled is False:
            continue
        for binding in registration.bindings:
            if binding.status == "resolved" and binding.node_id in graph.nodes:
                graph.entrypoints.setdefault(
                    binding.node_id,
                    EntrypointTag(
                        EntrypointKind.API,
                        TrustLevel.UNTRUSTED_EXTERNAL,
                        description=f"{registration.framework or registration.kind} registration",
                    ),
                )
