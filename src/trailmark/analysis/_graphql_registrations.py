"""Join schema identity to explicit framework resolver registration evidence."""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

from tree_sitter import Node, Parser
from tree_sitter_language_pack import get_language

from trailmark.analysis._django_registrations import _qualified
from trailmark.analysis._registration_context import RegistrationContext
from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.endpoints import (
    EndpointRegistration,
    HandlerBinding,
    RegistrationEvidence,
    registration_id,
)
from trailmark.models.nodes import SourceLocation
from trailmark.parsers._common import make_location, node_text

type _Key = tuple[str | None, str, str]
type _Candidates = dict[_Key, list[tuple[tuple[HandlerBinding, ...], RegistrationEvidence]]]


def _walk(node: Node) -> list[Node]:
    nodes = []
    pending = [node]
    while pending:
        current = pending.pop()
        nodes.append(current)
        pending.extend(reversed(current.named_children))
    return nodes


def _pairs(node: Node) -> dict[str, Node]:
    result = {}
    for child in node.named_children:
        if child.type == "pair":
            key, value = child.child_by_field_name("key"), child.child_by_field_name("value")
            if key is not None and value is not None:
                result[node_text(key).strip("\"'")] = value
    return result


def _text_string(node: Node | None) -> str | None:
    return node_text(node)[1:-1] if node is not None and node.type == "string" else None


def _nest_candidates(context: RegistrationContext, candidates: _Candidates) -> dict[_Key, set[str]]:
    schemas: dict[_Key, set[str]] = {}
    sources: dict[Path, tuple[Node, dict[str, tuple[str, str]], dict[str, Node]]] = {}
    namespaces: dict[tuple[Path, str], set[str]] = {}
    modules: dict[tuple[Path, str], tuple[list[str], SourceLocation]] = {}
    classes: dict[tuple[Path, str], tuple[list[Node], dict[str, tuple[str, str]]]] = {}
    extensions: list[tuple[Path, Node, str, SourceLocation]] = []
    for file in context.selected_files((".ts", ".tsx")):
        try:
            root = Parser(get_language("typescript")).parse(file.read_bytes()).root_node
        except OSError:
            continue
        imports: dict[str, tuple[str, str]] = {}
        arrays: dict[str, Node] = {}
        nodes = _walk(root)
        for node in root.named_children:
            if node.type != "import_statement":
                continue
            source = _text_string(node.child_by_field_name("source"))
            if source is None:
                continue
            for specifier in _walk(node):
                if specifier.type == "import_specifier":
                    name = specifier.child_by_field_name("name")
                    alias = specifier.child_by_field_name("alias")
                    if name is not None:
                        imports[node_text(alias or name)] = (source, node_text(name))
                elif specifier.type == "import_clause":
                    for child in specifier.named_children:
                        if child.type == "identifier":
                            imports[node_text(child)] = (source, "default")
        for node in nodes:
            if node.type == "variable_declarator":
                name, value = node.child_by_field_name("name"), node.child_by_field_name("value")
                if name is not None and value is not None and value.type == "array":
                    arrays[node_text(name)] = value
        sources[file.resolve()] = root, imports, arrays
        for node in nodes:
            if node.type != "class_declaration":
                continue
            name = node.child_by_field_name("name")
            if name is None:
                continue
            parent = (
                node.parent
                if node.parent is not None and node.parent.type == "export_statement"
                else node
            )
            decorators = [n for n in parent.named_children if n.type == "decorator"]
            key = file.resolve(), node_text(name)
            if any(
                imports.get(node_text(function)) == ("@nestjs/graphql", "Resolver")
                for decorator in decorators
                for call in decorator.named_children
                if (function := call.child_by_field_name("function")) is not None
            ):
                classes[key] = _walk(node), imports
            for decorator in decorators:
                for call in decorator.named_children:
                    function = call.child_by_field_name("function")
                    imported = imports.get(node_text(function)) if function is not None else None
                    if imported not in {
                        ("@nestjs/common", "Module"),
                        ("@vendure/core", "VendurePlugin"),
                    }:
                        continue
                    arguments = call.child_by_field_name("arguments")
                    if arguments is None or not arguments.named_children:
                        continue
                    options = _pairs(arguments.named_children[0])
                    if imported == ("@vendure/core", "VendurePlugin"):
                        for api in ("admin", "shop"):
                            extension = options.get(api + "ApiExtensions")
                            if extension is None or extension.type != "object":
                                continue
                            values = _pairs(extension)
                            resolvers = values.get("resolvers")
                            schema = values.get("schema")
                            namespace = api + "-api"
                            plugin_key = key[0], key[1] + ":" + namespace
                            location = make_location(decorator, str(file))
                            if resolvers is not None and resolvers.type == "array":
                                modules[plugin_key] = (
                                    [
                                        node_text(n)
                                        for n in resolvers.named_children
                                        if n.type in {"identifier", "type_identifier"}
                                    ],
                                    location,
                                )
                                namespaces[plugin_key] = {namespace}
                            if schema is not None:
                                extensions.append((file, schema, namespace, location))
                        continue
                    providers = options.get("providers")
                    if providers is None:
                        continue

                    def expand(
                        array: Node,
                        seen: frozenset[str] = frozenset(),
                        definitions: dict[str, Node] = arrays,
                    ) -> list[str]:
                        values = []
                        for child in array.named_children:
                            text = node_text(child).removeprefix("...")
                            if (
                                child.type == "spread_element"
                                and text in definitions
                                and text not in seen
                            ):
                                values.extend(expand(definitions[text], seen | {text}, definitions))
                            elif child.type in {"identifier", "type_identifier"}:
                                values.append(text)
                        return values

                    modules[key] = expand(providers), make_location(decorator, str(file))
        for node in nodes:
            if node.type != "object":
                continue
            pairs = _pairs(node)
            api_type = _text_string(pairs.get("apiType"))
            resolver_module = pairs.get("resolverModule")
            if api_type in {"shop", "admin"} and resolver_module is not None:
                name = node_text(resolver_module)
                module_file, class_name = _import_target(file, name, imports)
                namespaces.setdefault((module_file, class_name), set()).add(api_type + "-api")
                binding = f"nest:{module_file}:{class_name}"
                context.add(
                    EndpointRegistration(
                        registration_id("nest-transport", str(file), node.start_byte),
                        "http",
                        context.owning_root(file),
                        (
                            RegistrationEvidence(
                                "static",
                                make_location(node, str(file)),
                                "Nest GraphQL module configuration; API path may be dynamic",
                            ),
                        ),
                        (HandlerBinding("Nest GraphQL transport", status="unresolved"),),
                        framework="nestjs",
                        namespace=api_type + "-api",
                        path=_text_string(pairs.get("apiPath")),
                        transport="graphql-http",
                        schema_binding=binding,
                    )
                )
    for file, schema, namespace, location in extensions:
        _plugin_schema(context, sources, file, schema, namespace, location)
    for module_key, (providers, location) in modules.items():
        file = module_key[0]
        imports = sources[file][1]
        for provider in providers:
            provider_key = _import_target(file, provider, imports)
            definition = classes.get(provider_key)
            if definition is None:
                context.diagnostic(location.file_path, "unresolved_graphql_provider", provider)
                continue
            provider_nodes, provider_imports = definition
            for method in provider_nodes:
                if method.type != "method_definition":
                    continue
                name = method.child_by_field_name("name")
                if name is None:
                    continue
                decorators = [n for n in method.named_children if n.type == "decorator"]
                previous = method.prev_named_sibling
                while previous is not None and previous.type == "decorator":
                    decorators.insert(0, previous)
                    previous = previous.prev_named_sibling
                for decorator in decorators:
                    for call in decorator.named_children:
                        function = call.child_by_field_name("function")
                        imported = (
                            provider_imports.get(node_text(function))
                            if function is not None
                            else None
                        )
                        if (
                            imported is None
                            or imported[0] != "@nestjs/graphql"
                            or imported[1] not in {"Query", "Mutation", "Subscription"}
                        ):
                            continue
                        arguments = call.child_by_field_name("arguments")
                        operation = node_text(name)
                        if arguments is not None:
                            for argument in arguments.named_children:
                                if argument.type == "string":
                                    operation = _text_string(argument) or operation
                                elif argument.type == "object":
                                    operation = (
                                        _text_string(_pairs(argument).get("name")) or operation
                                    )
                        symbol = f"{provider_key[1]}.{node_text(name)}"
                        units = [
                            u
                            for u in context.locations.get(str(provider_key[0]), [])
                            if u.id.rsplit(":", 1)[-1] == symbol
                        ]
                        bindings = tuple(
                            HandlerBinding(symbol, u.id, u.location, "resolved") for u in units
                        )
                        for namespace in namespaces.get(module_key, {None}):
                            schemas.setdefault((namespace, imported[1], operation), set()).add(
                                f"nest:{module_key[0]}:{module_key[1]}"
                            )
                            candidates.setdefault((namespace, imported[1], operation), []).append(
                                (
                                    bindings or (HandlerBinding(symbol),),
                                    RegistrationEvidence(
                                        "static",
                                        location,
                                        "Nest module provider and GraphQL decorator",
                                    ),
                                )
                            )
    return schemas


def _import_target(file: Path, name: str, imports: dict[str, tuple[str, str]]) -> tuple[Path, str]:
    source, original = imports.get(name, ("", name))
    if not source.startswith("."):
        return file.resolve(), original
    stem = file.parent / source
    if stem.suffix in {".js", ".mjs", ".cjs"}:
        stem = stem.with_suffix("")
    target = next(
        (p for p in (stem.with_suffix(stem.suffix + ".ts"), stem / "index.ts") if p.exists()), stem
    )
    return target.resolve(), original


def _plugin_schema(
    context: RegistrationContext,
    sources: dict[Path, tuple[Node, dict[str, tuple[str, str]], dict[str, Node]]],
    file: Path,
    schema: Node,
    namespace: str,
    registration_location: SourceLocation,
) -> None:
    """Read only a plugin's explicitly referenced, literal graphql-tag schema."""
    target, name = _import_target(file, node_text(schema), sources[file.resolve()][1])
    definition = sources.get(target)
    if definition is None:
        context.diagnostic(file, "unresolved_plugin_schema", node_text(schema))
        return
    root, imports, _ = definition
    for node in _walk(root):
        if node.type != "variable_declarator":
            continue
        variable, value = node.child_by_field_name("name"), node.child_by_field_name("value")
        if variable is None or node_text(variable) != name or value is None:
            continue
        function = value.child_by_field_name("function")
        template = value.child_by_field_name("arguments")
        if (
            function is None
            or imports.get(node_text(function)) != ("graphql-tag", "default")
            or template is None
            or template.type != "template_string"
            or any(n.type == "template_substitution" for n in template.named_children)
        ):
            context.diagnostic(target, "unsupported_plugin_schema", name)
            continue
        content = (template.text or b"")[1:-1]
        document = Parser(get_language("graphql")).parse(content).root_node
        if document.has_error:
            context.diagnostic(target, "invalid_plugin_schema", name)
        for owner in _walk(document):
            if owner.type not in {"object_type_definition", "object_type_extension"}:
                continue
            operation = next((node_text(n) for n in owner.named_children if n.type == "name"), "")
            if operation not in {"Query", "Mutation", "Subscription"}:
                continue
            for field in _walk(owner):
                if field.type != "field_definition":
                    continue
                field_name = next(
                    (node_text(n) for n in field.named_children if n.type == "name"), ""
                )
                start, end = field.start_point, field.end_point
                location = SourceLocation(
                    str(target),
                    template.start_point.row + start.row + 1,
                    template.start_point.row + end.row + 1,
                    start.column + (template.start_point.column + 1 if start.row == 0 else 0),
                    end.column + (template.start_point.column + 1 if end.row == 0 else 0),
                )
                record = EndpointRegistration(
                    registration_id(
                        "graphql", context.owning_root(target), namespace, operation, field_name
                    ),
                    "graphql",
                    context.owning_root(target),
                    (
                        RegistrationEvidence("static", location, "Vendure plugin schema extension"),
                        RegistrationEvidence(
                            "static", registration_location, "Vendure plugin registration"
                        ),
                    ),
                    (HandlerBinding(),),
                    framework="nestjs",
                    namespace=namespace,
                    operation_type=operation,
                    operation_name=field_name,
                )
                existing = context.graph.registrations.get(record.id)
                if existing is None:
                    context.add(record)
                else:
                    context.graph.registrations[record.id] = replace(
                        existing,
                        framework=existing.framework or record.framework,
                        evidence=tuple(dict.fromkeys((*existing.evidence, *record.evidence))),
                    )


def _graphene_candidates(
    context: RegistrationContext, candidates: _Candidates
) -> dict[_Key, set[str]]:
    schemas: dict[_Key, set[str]] = {}
    classes: dict[str, tuple[ast.ClassDef, dict[str, str], str]] = {}
    roots: list[tuple[str, str, SourceLocation, str | None, bool]] = []
    for file in context.selected_files((".py",)):
        try:
            tree = ast.parse(file.read_bytes())
        except (OSError, SyntaxError, UnicodeError):
            continue
        root = Path(context.owning_root(file))
        parts = list(file.relative_to(root).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        if (root / "__init__.py").exists():
            parts.insert(0, root.name)
        module = ".".join(parts)
        package = module if file.stem == "__init__" else module.rpartition(".")[0]
        aliases: dict[str, str] = {}
        for statement in tree.body:
            if isinstance(statement, ast.Import):
                for item in statement.names:
                    aliases[item.asname or item.name.split(".")[0]] = item.name
            elif isinstance(statement, ast.ImportFrom):
                base = statement.module or ""
                if statement.level:
                    prefix = package.split(".")[: len(package.split(".")) - statement.level + 1]
                    base = ".".join([*prefix, *([base] if base else [])])
                for item in statement.names:
                    aliases[item.asname or item.name] = f"{base}.{item.name}".strip(".")
            elif isinstance(statement, ast.ClassDef):
                aliases[statement.name] = f"{module}.{statement.name}"
        for statement in tree.body:
            if isinstance(statement, ast.ClassDef):
                classes[f"{module}.{statement.name}"] = statement, aliases, str(file)
            if not isinstance(statement, (ast.Assign, ast.AnnAssign)) or not isinstance(
                statement.value, ast.Call
            ):
                continue
            call = statement.value
            constructor = _qualified(call.func, aliases)
            if constructor not in {
                "graphene.Schema",
                "saleor.graphql.core.federation.schema.build_federated_schema",
            }:
                continue
            values = {k.arg: k.value for k in call.keywords}
            if call.args:
                values.setdefault("query", call.args[0])
            schema_binding = None
            if (
                isinstance(statement, ast.Assign)
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
            ):
                schema_binding = module + "." + statement.targets[0].id
            camelcase = values.get("auto_camelcase")
            auto_camelcase = not (isinstance(camelcase, ast.Constant) and camelcase.value is False)
            for role in ("query", "mutation", "subscription"):
                if role in values and (symbol := _qualified(values[role], aliases)):
                    roots.append(
                        (
                            role.title(),
                            symbol,
                            SourceLocation(str(file), call.lineno, call.end_lineno or call.lineno),
                            schema_binding,
                            auto_camelcase,
                        )
                    )

    def fields(
        symbol: str, seen: frozenset[str]
    ) -> dict[str, tuple[ast.Call, str, dict[str, str], str]]:
        if symbol in seen or symbol not in classes:
            return {}
        node, aliases, file = classes[symbol]
        result = {}
        for base in node.bases:
            parent = _qualified(base, aliases)
            if parent:
                result.update(fields(parent, seen | {symbol}))
        for statement in node.body:
            if isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Call):
                for target in statement.targets:
                    if isinstance(target, ast.Name) and not target.id.startswith("_"):
                        result[target.id] = statement.value, symbol, aliases, file
        return result

    for role, root, location, schema_binding, auto_camelcase in roots:
        for field, (call, owner, aliases, file) in fields(root, frozenset()).items():
            values = {k.arg: k.value for k in call.keywords}
            resolver = _qualified(values["resolver"], aliases) if "resolver" in values else None
            if resolver is None:
                resolver = owner + ".resolve_" + field
            bindings = context.bind(resolver)
            if (
                bindings[0].status != "resolved"
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "Field"
            ):
                mutation = _qualified(call.func.value, aliases)
                if mutation:
                    bindings = context.bind(mutation + ".mutate")
                    if bindings[0].status != "resolved":
                        bindings = context.bind(mutation + ".perform_mutation")
            words = field.split("_")
            public_name = words[0] + "".join(w[:1].upper() + w[1:] for w in words[1:])
            if not auto_camelcase:
                public_name = field
            explicit_name = values.get("name")
            if isinstance(explicit_name, ast.Constant) and isinstance(explicit_name.value, str):
                public_name = explicit_name.value
            candidates.setdefault((None, role, public_name), []).append(
                (
                    bindings,
                    RegistrationEvidence(
                        "static",
                        location,
                        f"Graphene schema root {root}; field owner {owner}; {file}",
                    ),
                )
            )
            if schema_binding:
                schemas.setdefault((None, role, public_name), set()).add(schema_binding)
    return schemas


def link_graphql_registrations(context: RegistrationContext) -> None:
    """Keep namespace and framework registration evidence in every resolver join."""
    candidates: _Candidates = {}
    schemas = _nest_candidates(context, candidates)
    for key, values in _graphene_candidates(context, candidates).items():
        schemas.setdefault(key, set()).update(values)
    for identifier, registration in list(context.graph.registrations.items()):
        if registration.kind != "graphql":
            continue
        key = (
            registration.namespace,
            registration.operation_type or "",
            registration.operation_name or "",
        )
        matches = list(candidates.get(key, []))
        framework = registration.framework
        for node_id in registration.declaration_nodes:
            unit = context.graph.nodes[node_id]
            resolver = dict(unit.attributes).get("graphql_resolver")
            if isinstance(resolver, str):
                framework = "magento"
                matches.append(
                    (
                        context.bind(resolver + "::resolve"),
                        RegistrationEvidence(
                            "static", unit.location, "Magento @resolver directive"
                        ),
                    )
                )
        bindings = (
            tuple(dict.fromkeys(b for bound, _ in matches for b in bound)) or registration.bindings
        )
        evidence = tuple(dict.fromkeys((*registration.evidence, *(e for _, e in matches))))
        resolved = {b.node_id for b in bindings if b.status == "resolved" and b.node_id is not None}
        if len(resolved) > 1:
            bindings = tuple(replace(b, status="ambiguous") for b in bindings)
        schema_bindings = schemas.get(key, set())
        if matches and framework is None:
            framework = "nestjs" if any("Nest" in e.detail for _, e in matches) else "graphene"
        context.graph.registrations[identifier] = replace(
            registration,
            bindings=bindings,
            evidence=evidence,
            framework=framework,
            schema_binding=next(iter(schema_bindings)) if len(schema_bindings) == 1 else None,
        )
        if len(resolved) == 1 and all(b.status == "resolved" for b in bindings):
            target = next(iter(resolved))
            for node_id in registration.declaration_nodes:
                edge = CodeEdge(
                    node_id,
                    target,
                    EdgeKind.RESOLVES_TO,
                    EdgeConfidence.INFERRED,
                    context.graph.nodes[node_id].location,
                    (("registration_id", identifier), ("dispatch", "graphql_resolver")),
                )
                if edge not in context.graph.edges:
                    context.graph.edges.append(edge)
    link_graphql_transports(context)


def link_graphql_transports(context: RegistrationContext) -> None:
    """Associate only explicitly wired HTTP transports with query/mutation operations."""
    transports = [
        r
        for r in context.graph.registrations.values()
        if r.kind == "http" and (r.schema_binding is not None or r.transport == "graphql-http")
    ]
    for identifier, operation in list(context.graph.registrations.items()):
        if operation.kind != "graphql" or operation.operation_type not in {"Query", "Mutation"}:
            continue
        matches = [
            t.id
            for t in transports
            if (
                operation.schema_binding is not None
                and operation.schema_binding == t.schema_binding
            )
            or (
                operation.framework == t.framework == "magento"
                and operation.namespace == "magento"
                and t.transport == "graphql-http"
                and operation.owning_root == t.owning_root
            )
            or (
                operation.framework == t.framework == "nestjs"
                and operation.namespace == t.namespace
                and t.schema_binding is not None
                and any(e.detail == "Vendure plugin registration" for e in operation.evidence)
            )
        ]
        context.graph.registrations[identifier] = replace(
            operation, transport_registrations=tuple(sorted(set(matches)))
        )
