"""Associate selected Magento layout references with PHP template candidates."""

from __future__ import annotations

from pathlib import Path

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from trailmark.analysis._registration_context import RegistrationContext
from trailmark.models.endpoints import (
    EndpointRegistration,
    HandlerBinding,
    RegistrationEvidence,
    registration_id,
)
from trailmark.models.nodes import SourceLocation


def link_magento_templates(context: RegistrationContext) -> None:
    """Preserve layout evidence and competing theme overrides without asserting activation."""
    templates: dict[tuple[str, str], list[HandlerBinding]] = {}
    for unit in context.graph.nodes.values():
        if dict(unit.attributes).get("php_execution_kind") != "template":
            continue
        path = Path(unit.location.file_path)
        parts = path.parts
        if "templates" not in parts:
            continue
        index = len(parts) - 1 - parts[::-1].index("templates")
        relative = "/".join(parts[index + 1 :])
        if "view" in parts[:index]:
            view = len(parts[:index]) - 1 - parts[:index][::-1].index("view")
            if view < 2:
                continue
            module = parts[view - 2] + "_" + parts[view - 1]
        else:
            module = parts[index - 1]
        templates.setdefault((module, relative), []).append(
            HandlerBinding(str(path), unit.id, unit.location, "resolved")
        )
    for file in context.selected_files((".xml",)):
        if "layout" not in file.parts:
            continue
        try:
            root = ElementTree.parse(str(file)).getroot()
        except (OSError, ValueError, ElementTree.ParseError, DefusedXmlException) as exc:
            context.diagnostic(file, "invalid_template_layout", str(exc))
            continue
        for index, element in enumerate(root.iter()):
            reference = element.get("template")
            if not reference:
                continue
            if "::" not in reference:
                context.diagnostic(file, "unsupported_template_reference", reference)
                continue
            module, _, relative = reference.partition("::")
            candidates = templates.get((module, relative), [])
            # A selected theme does not prove it wins Magento's inheritance,
            # area, store or runtime template-selection rules.
            bindings = tuple(
                HandlerBinding(
                    b.symbol,
                    b.node_id,
                    b.location,
                    "ambiguous" if len(candidates) > 1 else "resolved",
                )
                for b in candidates
            ) or (HandlerBinding(reference, status="unresolved"),)
            context.add(
                EndpointRegistration(
                    registration_id("magento-layout", str(file), index, reference),
                    "template",
                    context.owning_root(file),
                    (
                        RegistrationEvidence(
                            "static",
                            SourceLocation(str(file), 1, len(file.read_bytes().splitlines()) or 1),
                            "Magento layout template reference; "
                            "theme selection and rendering unverified; "
                            f"block={element.get('class') or 'unknown'}",
                        ),
                    ),
                    bindings,
                    framework="magento",
                    registration_key=reference,
                )
            )
