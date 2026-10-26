"""Magento module, REST, controller and DI registration evidence."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from xml.etree.ElementTree import Element

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


def discover_magento(context: RegistrationContext) -> None:
    files = context.selected_files((".xml", ".php"))
    xml: dict[Path, Element] = {}
    modules: dict[str, Path] = {}
    state: dict[str, bool] = {}
    preferences: dict[str, set[str]] = {}
    for file in files:
        if file.as_posix().endswith("/app/etc/config.php"):
            text = file.read_text()
            state.update(
                {
                    name: flag == "1"
                    for name, flag in re.findall(
                        r"['\"]([A-Za-z0-9]+_[A-Za-z0-9_]+)['\"]\s*=>\s*([01])\b", text
                    )
                }
            )
        if file.name not in {"module.xml", "routes.xml", "webapi.xml", "di.xml"}:
            continue
        try:
            root = ElementTree.parse(str(file)).getroot()
        except (OSError, ValueError, ElementTree.ParseError, DefusedXmlException) as exc:
            context.diagnostic(file, "invalid_registration_input", str(exc))
            continue
        xml[file] = root
        if file.name == "module.xml":
            for module in root.findall("module"):
                if module.get("name"):
                    modules[module.get("name")] = file.parent.parent
        if file.name == "di.xml" and file.parent.name == "etc":
            for preference in root.findall("preference"):
                if preference.get("for") and preference.get("type"):
                    preferences.setdefault(preference.get("for"), set()).add(preference.get("type"))
    context.graph.registration_diagnostics.extend(
        {
            "file": str(context.roots[0]),
            "reason": "module_configuration",
            "detail": f"{name}={'enabled' if flag else 'disabled'} "
            "(source config; runtime unverified)",
        }
        for name, flag in sorted(state.items())
    )
    for file, root in xml.items():
        if file.name == "di.xml":
            for element in root.findall("type"):
                if element.get("name") != "Magento\\Framework\\App\\AreaList":
                    continue
                front = element.find(
                    "arguments/argument[@name='areas']/item[@name='graphql']/item[@name='frontName']"
                )
                if front is not None and front.text:
                    module = next((m for m, p in modules.items() if file.is_relative_to(p)), None)
                    _emit(
                        context,
                        file,
                        "graphql-transport",
                        "/" + front.text.strip(),
                        (),
                        context.bind("Magento\\GraphQl\\Controller\\GraphQl::dispatch"),
                        "graphql",
                        (),
                        "Magento GraphQL area frontName; runtime activation unknown",
                        state.get(module),
                        transport="graphql-http",
                    )
        elif file.name == "webapi.xml":
            for index, route in enumerate(root.findall("route")):
                service = route.find("service")
                symbol = None
                if service is not None:
                    service_class = service.get("class", "")
                    method = service.get("method", "")
                    classes = preferences.get(service_class, set())
                    target = next(iter(classes)) if len(classes) == 1 else service_class
                    symbol = target + "::" + method
                bindings = context.bind(symbol)
                acl = tuple(
                    str(resource.get("ref"))
                    for resource in route.findall("resources/resource")
                    if resource.get("ref")
                )
                module = next((m for m, p in modules.items() if file.is_relative_to(p)), None)
                _emit(
                    context,
                    file,
                    f"webapi:{index}",
                    route.get("url"),
                    (str(route.get("method")),) if route.get("method") else (),
                    bindings,
                    "webapi",
                    acl,
                    f"Magento webapi.xml; module={module}; configured={state.get(module)}; "
                    "REST store-code prefix and runtime activation unknown",
                    state.get(module),
                )
        elif file.name == "routes.xml":
            area = file.parent.name
            for router in root.findall("router"):
                for route in router.findall("route"):
                    front = route.get("frontName")
                    for module in route.findall("module"):
                        module_name = module.get("name")
                        module_root = modules.get(module_name)
                        if module_root is None:
                            context.diagnostic(file, "out_of_root_module", str(module_name))
                            continue
                        controller_root = module_root / "Controller"
                        if area == "adminhtml":
                            controller_root /= "Adminhtml"
                        for controller_file in sorted(context.locations):
                            source = Path(controller_file)
                            if not source.is_relative_to(controller_root):
                                continue
                            methods = tuple(
                                sorted(
                                    set(
                                        re.findall(
                                            r"Http(Get|Post|Put|Delete|Patch|Head|Options)ActionInterface",
                                            source.read_text(),
                                        )
                                    )
                                )
                            )
                            verbs = tuple(m.upper() for m in methods)
                            execute = [
                                u for u in context.locations[controller_file] if u.name == "execute"
                            ]
                            if len(execute) != 1:
                                context.diagnostic(
                                    source, "unresolved_controller", "No unique execute method"
                                )
                                continue
                            unit = execute[0]
                            relative = source.relative_to(controller_root).with_suffix("")
                            prefix = "{admin_frontName}" if area == "adminhtml" else front
                            path = (
                                "/"
                                + str(prefix)
                                + "/"
                                + "/".join(p.lower() for p in relative.parts)
                                if prefix
                                else None
                            )
                            acl = tuple(
                                re.findall(
                                    r"ADMIN_RESOURCE\s*=\s*['\"]([^'\"]+)['\"]", source.read_text()
                                )
                            )
                            _emit(
                                context,
                                file,
                                f"{route.get('id')}:{module_name}:{unit.id}",
                                path,
                                verbs,
                                context.bind(
                                    None, file=controller_file, line=unit.location.start_line
                                ),
                                area,
                                acl,
                                f"Magento routes.xml; module={module_name}; "
                                f"configured={state.get(module_name)}; "
                                f"ordering={dict(module.attrib)}; "
                                "rewrites, secret keys and runtime activation unknown",
                                state.get(module_name),
                            )
    for identifier, registration in list(context.graph.registrations.items()):
        if registration.kind != "graphql" or registration.namespace != "magento":
            continue
        flags = {
            state.get(module)
            for node_id in registration.declaration_nodes
            for module, directory in modules.items()
            if Path(context.graph.nodes[node_id].location.file_path).is_relative_to(directory)
        }
        configured = True if True in flags else False if flags == {False} else None
        context.graph.registrations[identifier] = replace(
            registration, framework="magento", declared_enabled=configured
        )


def _emit(
    context: RegistrationContext,
    file: Path,
    key: str,
    path: str | None,
    methods: tuple[str, ...],
    bindings: tuple[HandlerBinding, ...],
    namespace: str,
    acl: tuple[str, ...],
    detail: str,
    declared_enabled: bool | None = None,
    *,
    transport: str = "http",
) -> None:
    context.add(
        EndpointRegistration(
            registration_id("magento", str(file), key),
            "http",
            context.owning_root(file),
            (
                RegistrationEvidence(
                    "static",
                    SourceLocation(str(file), 1, len(file.read_bytes().splitlines()) or 1),
                    detail,
                ),
            ),
            bindings,
            framework="magento",
            namespace=namespace,
            path=path,
            method=methods[0] if len(methods) == 1 else None,
            methods=methods,
            transport=transport,
            registration_key=key,
            declared_enabled=declared_enabled,
            acl=acl,
        )
    )
