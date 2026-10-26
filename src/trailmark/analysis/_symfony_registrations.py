"""Read Symfony route imports and explicit API Platform operation metadata."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from tree_sitter import Parser
from tree_sitter_language_pack import get_language

from trailmark.analysis._registration_context import RegistrationContext
from trailmark.models.endpoints import EndpointRegistration, RegistrationEvidence, registration_id
from trailmark.models.nodes import SourceLocation
from trailmark.parsers._common import make_location, node_text
from trailmark.parsers._simple import descendants


def _php_services(
    context: RegistrationContext,
    provenance: dict[str, list[RegistrationEvidence]] | None = None,
) -> dict[str, set[str]]:
    """Read literal configurator service aliases without evaluating PHP."""
    services: dict[str, set[str]] = {}
    for file in context.selected_files((".php",)):
        source = file.read_bytes()
        if b"DependencyInjection\\Loader\\Configurator" not in source:
            continue
        tree = Parser(get_language("php")).parse(source)
        nodes = list(descendants(tree.root_node))
        aliases = {}
        namespaces = {
            node_text(name)
            for node in nodes
            if node.type == "namespace_definition"
            if (name := node.child_by_field_name("name")) is not None
        }
        for node in nodes:
            if node.type != "namespace_use_declaration":
                continue
            match = re.fullmatch(r"use\s+([\\\w]+)(?:\s+as\s+(\w+))?\s*;", node_text(node))
            if match:
                target, alias = match.groups()
                aliases[alias or target.rsplit("\\", 1)[-1]] = target
        configurator = "Symfony\\Component\\DependencyInjection\\Loader\\Configurator"
        containers = set()
        for node in nodes:
            if node.type != "simple_parameter":
                continue
            type_node, variable = node.child_by_field_name("type"), node.child_by_field_name("name")
            if type_node is None or variable is None:
                continue
            type_name = node_text(type_node).lstrip("\\")
            qualified = aliases.get(type_name, type_name)
            if qualified == configurator + "\\ContainerConfigurator" or (
                configurator in namespaces and type_name == "ContainerConfigurator"
            ):
                containers.add(node_text(variable))
        # Accept only a services() result assigned from a container variable.
        receivers = set()
        for node in nodes:
            if node.type != "assignment_expression":
                continue
            left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
            if left is None or right is None or right.type != "member_call_expression":
                continue
            name = right.child_by_field_name("name")
            receiver = right.child_by_field_name("object")
            if (
                name is not None
                and node_text(name) == "services"
                and receiver is not None
                and node_text(receiver) in containers
            ):
                receivers.add(node_text(left))
        for node in nodes:
            if node.type != "member_call_expression":
                continue
            name, receiver = node.child_by_field_name("name"), node.child_by_field_name("object")
            arguments = node.child_by_field_name("arguments")
            if (
                name is None
                or node_text(name) != "set"
                or receiver is None
                or node_text(receiver) not in receivers
                or arguments is None
            ):
                continue
            values = arguments.named_children
            if len(values) < 2:
                continue
            identifier = re.fullmatch(r"['\"]([\w.\\-]+)['\"]", node_text(values[0]))
            target = re.fullmatch(r"([\\\w]+)::class", node_text(values[1]))
            if identifier and target:
                symbol = target.group(1)
                symbol = aliases.get(symbol, symbol)
                if "\\" in symbol:
                    services.setdefault(identifier.group(1), set()).add(symbol)
                    if provenance is not None:
                        provenance.setdefault(identifier.group(1), []).append(
                            RegistrationEvidence(
                                "static",
                                make_location(node, str(file)),
                                "Literal Symfony ContainerConfigurator service alias",
                            )
                        )
    return services


def discover_symfony(context: RegistrationContext) -> None:
    files = context.selected_files((".yaml", ".yml", ".xml"))
    selected = {f.resolve() for f in files}
    documents: dict[Path, dict[str, Any]] = {}
    service_evidence: dict[str, list[RegistrationEvidence]] = {}
    services = _php_services(context, service_evidence)
    for file in files:
        try:
            if file.suffix.lower() in {".yaml", ".yml"}:
                data = yaml.safe_load(file.read_bytes())
                if isinstance(data, dict):
                    documents[file] = data
                    service_rows = data.get("services", {})
                    for key, value in (
                        service_rows.items() if isinstance(service_rows, dict) else ()
                    ):
                        if isinstance(value, dict) and isinstance(value.get("class"), str):
                            services.setdefault(key, set()).add(value["class"])
            else:
                tree = ElementTree.parse(str(file))
                root = tree.getroot()
                tag = root.tag.rsplit("}", 1)[-1]
                if tag == "routes":
                    data = {}
                    for index, element in enumerate(root):
                        kind = element.tag.rsplit("}", 1)[-1]
                        if kind in {"route", "import"}:
                            row: dict[str, Any] = dict(element.attrib)
                            row["defaults"] = {
                                n.get("key"): n.text
                                for n in element
                                if n.tag.rsplit("}", 1)[-1] == "default"
                            }
                            if "methods" in row:
                                row["methods"] = row["methods"].split("|")
                            data[element.get("id", str(index))] = row
                    documents[file] = data
                elif tag == "resources":
                    resources = {}
                    for resource in root:
                        if resource.tag.rsplit("}", 1)[-1] != "resource":
                            continue
                        operations = {}
                        for operation in resource.iter():
                            if operation.tag.rsplit("}", 1)[-1] != "operation":
                                continue
                            key = operation.get("class", operation.get("name", "unknown"))
                            operations[key] = dict(operation.attrib)
                        resources[resource.get("class", "unknown")] = {"operations": operations}
                    documents[file] = {"resources": resources}
                for element in root.iter():
                    if element.tag.rsplit("}", 1)[-1] == "service" and element.get("class"):
                        services.setdefault(element.get("id", ""), set()).add(element.get("class"))
        except (
            OSError,
            ValueError,
            yaml.YAMLError,
            ElementTree.ParseError,
            DefusedXmlException,
        ) as exc:
            context.diagnostic(file, "invalid_registration_input", str(exc))

    def imports(file: Path, resource: str) -> list[Path]:
        if resource.startswith("@"):
            bundle, _, relative = resource[1:].partition("/")
            paths = [
                Path(u.location.file_path).parent / relative
                for key, units in context.symbols.items()
                if key.rsplit(".", 1)[-1] == bundle
                for u in units
            ]
        else:
            paths = [file.parent / resource]
        return sorted(
            {
                candidate.resolve()
                for path in paths
                for candidate in (path.parent.glob(path.name) if "*" in path.name else [path])
                if candidate.resolve() in selected
            }
        )

    imported: set[Path] = set()
    for file, document in documents.items():
        for row in document.values():
            if isinstance(row, dict) and isinstance(row.get("resource"), str):
                imported.update(imports(file, row["resource"]))

    def emit(file: Path, prefix: str, namespace: str, chain: tuple[Path, ...]) -> None:
        if file in chain:
            context.diagnostic(file, "cyclic_import", "Symfony route import cycle")
            return
        document = documents.get(file, {})
        for key, row in document.items():
            if not isinstance(row, dict):
                continue
            resource = row.get("resource")
            if isinstance(resource, str):
                children = imports(file, resource)
                if not children:
                    context.diagnostic(file, "unsupported_import", resource)
                for child in children:
                    emit(
                        child,
                        prefix + str(row.get("prefix", "")),
                        namespace + str(row.get("name_prefix", "")),
                        (*chain, file),
                    )
                continue
            if isinstance(row.get("path"), str):
                defaults = row.get("defaults")
                controller = row.get("controller")
                if controller is None and isinstance(defaults, dict):
                    controller = defaults.get("_controller")
                _emit(
                    context,
                    file,
                    namespace + str(key),
                    prefix + row["path"],
                    row.get("methods", ()),
                    controller,
                    services,
                    "Symfony route import chain: " + " -> ".join(str(p) for p in (*chain, file)),
                    service_evidence=service_evidence,
                )
        resources = document.get("resources", {})
        if isinstance(resources, dict):
            for resource_class, metadata in resources.items():
                if not isinstance(metadata, dict):
                    continue
                operations = metadata.get("operations", {})
                if not isinstance(operations, dict):
                    context.diagnostic(file, "unsupported_generated_operation", str(resource_class))
                    continue
                for operation, operation_data in operations.items():
                    values = operation_data if isinstance(operation_data, dict) else {}
                    route_path = values.get("uriTemplate")
                    if not isinstance(route_path, str):
                        context.diagnostic(file, "unsupported_generated_operation", str(operation))
                        continue
                    method = str(values.get("method") or operation).rsplit("\\", 1)[-1]
                    method = method.removesuffix("Collection").upper()
                    methods = (
                        [method] if method in {"GET", "POST", "PUT", "PATCH", "DELETE"} else []
                    )
                    _emit(
                        context,
                        file,
                        str(operation),
                        prefix + route_path,
                        methods,
                        values.get("controller"),
                        services,
                        f"API Platform resource {resource_class}; "
                        "provider/processor dispatch unverified",
                        service_evidence=service_evidence,
                    )

    for file in sorted(set(documents) - imported):
        emit(file, "", "", ())


def _emit(
    context: RegistrationContext,
    file: Path,
    key: str,
    path: str,
    methods: object,
    controller: object,
    services: dict[str, set[str]],
    detail: str,
    *,
    service_evidence: dict[str, list[RegistrationEvidence]],
) -> None:
    symbol = controller if isinstance(controller, str) else None
    extra: tuple[RegistrationEvidence, ...] = ()
    if symbol:
        service, separator, method = symbol.partition("::")
        extra = tuple(service_evidence.get(service, ()))
        if not separator and ":" in symbol:
            service, separator, method = symbol.partition(":")
        classes = services.get(service, set())
        if len(classes) == 1:
            symbol = next(iter(classes)) + "::" + (method or "__invoke")
        elif not separator and "\\" in service:
            symbol = service + "::__invoke"
    bindings = context.bind(symbol)
    values = (
        (methods,)
        if isinstance(methods, str)
        else tuple(methods)
        if isinstance(methods, (list, tuple))
        else ()
    )
    verbs = tuple(str(v).upper() for v in values)
    location = SourceLocation(str(file), 1, len(file.read_bytes().splitlines()) or 1)
    context.add(
        EndpointRegistration(
            registration_id("symfony", str(file), key, path),
            "http",
            context.owning_root(file),
            (RegistrationEvidence("static", location, detail), *extra),
            bindings,
            framework="api-platform" if detail.startswith("API Platform") else "symfony",
            registration_key=key,
            path=path,
            method=verbs[0] if len(verbs) == 1 else None,
            methods=verbs,
            transport="http",
        )
    )
