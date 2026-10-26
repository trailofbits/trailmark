"""Conservative Django URLconf reader, including nested namespaces and CBVs."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from trailmark.analysis._registration_context import RegistrationContext
from trailmark.models.endpoints import EndpointRegistration, RegistrationEvidence, registration_id
from trailmark.models.nodes import SourceLocation


@dataclass(frozen=True)
class _Route:
    path: str | None
    target: str | None
    include: str | None
    namespace: str | None
    name: str | None
    location: SourceLocation
    class_view: bool = False
    schema_binding: str | None = None


def _literal(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _qualified(node: ast.AST, aliases: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id)
    if isinstance(node, ast.Attribute):
        parent = _qualified(node.value, aliases)
        return f"{parent}.{node.attr}" if parent else None
    return None


def discover_django(context: RegistrationContext) -> None:
    modules: dict[str, list[_Route]] = {}
    for file in context.selected_files((".py",)):
        try:
            tree = ast.parse(file.read_bytes())
        except (OSError, SyntaxError, UnicodeError):
            continue
        root = Path(context.owning_root(file))
        relative = file.relative_to(root).with_suffix("")
        parts = list(relative.parts)
        if parts[-1] == "__init__":
            parts.pop()
        if (root / "__init__.py").exists():
            parts.insert(0, root.name)
        module = ".".join(parts)
        package = module if file.stem == "__init__" else module.rpartition(".")[0]
        aliases: dict[str, str] = {}
        for statement in tree.body:
            if isinstance(statement, ast.Import):
                for name in statement.names:
                    aliases[name.asname or name.name.split(".")[0]] = (
                        name.name if name.asname else name.name.split(".")[0]
                    )
            elif isinstance(statement, ast.ImportFrom):
                base = statement.module or ""
                if statement.level:
                    prefix = package.split(".")[: len(package.split(".")) - statement.level + 1]
                    base = ".".join([*prefix, *([base] if base else [])])
                for name in statement.names:
                    aliases[name.asname or name.name] = f"{base}.{name.name}".strip(".")
            elif isinstance(statement, (ast.FunctionDef, ast.ClassDef)):
                aliases[statement.name] = f"{module}.{statement.name}"
        routes: list[_Route] = []
        # Only urlpatterns expressions are registrations. Calls in unrelated
        # functions or test assertions do not establish a URLconf.
        for statement in tree.body:
            targets = (
                statement.targets
                if isinstance(statement, ast.Assign)
                else (
                    [statement.target]
                    if isinstance(statement, (ast.AnnAssign, ast.AugAssign))
                    else []
                )
            )
            if not any(isinstance(t, ast.Name) and t.id == "urlpatterns" for t in targets):
                continue
            value = getattr(statement, "value", None)
            if value is None:
                continue
            for call in ast.walk(value):
                if not isinstance(call, ast.Call):
                    continue
                if _qualified(call.func, aliases) not in {
                    "django.urls.path",
                    "django.urls.re_path",
                    "django.conf.urls.url",
                }:
                    continue
                if len(call.args) < 2:
                    context.diagnostic(
                        file, "unsupported_registration", "URL pattern has dynamic arguments"
                    )
                    continue
                path = _literal(call.args[0])
                target = call.args[1]
                keywords = {k.arg: k.value for k in call.keywords}
                namespace = None
                included = None
                class_view = False
                schema_binding = None
                if isinstance(target, ast.Call) and _qualified(target.func, aliases) in {
                    "django.urls.include",
                    "django.conf.urls.include",
                }:
                    included = _literal(target.args[0]) if target.args else None
                    if target.args and isinstance(target.args[0], ast.Tuple):
                        items = target.args[0].elts
                        included = _literal(items[0]) if items else None
                        namespace = _literal(items[1]) if len(items) > 1 else None
                    include_keywords = {k.arg: k.value for k in target.keywords}
                    namespace = _literal(include_keywords.get("namespace")) or namespace
                    symbol = None
                else:
                    if (
                        isinstance(target, ast.Call)
                        and _qualified(target.func, aliases)
                        == "django.views.decorators.csrf.csrf_exempt"
                        and target.args
                    ):
                        target = target.args[0]
                    if (
                        isinstance(target, ast.Call)
                        and isinstance(target.func, ast.Attribute)
                        and target.func.attr == "as_view"
                    ):
                        class_view = True
                        symbol = _qualified(target.func.value, aliases)
                        for keyword in target.keywords:
                            if keyword.arg == "schema":
                                schema_binding = _qualified(keyword.value, aliases)
                    else:
                        symbol = _qualified(target, aliases)
                routes.append(
                    _Route(
                        path,
                        symbol,
                        included,
                        namespace,
                        _literal(keywords.get("name")),
                        SourceLocation(
                            str(file),
                            call.lineno,
                            call.end_lineno or call.lineno,
                            call.col_offset,
                            call.end_col_offset,
                        ),
                        class_view,
                        schema_binding,
                    )
                )
        if routes:
            modules[module] = routes
    included_modules = {r.include for rows in modules.values() for r in rows if r.include}

    def emit(module: str, prefix: str | None, namespace: str | None, seen: tuple[str, ...]) -> None:
        if module in seen:
            context.diagnostic(module, "cyclic_import", "Django include cycle")
            return
        for row in modules.get(module, []):
            path = prefix + row.path if prefix is not None and row.path is not None else None
            ns = ":".join(n for n in (namespace, row.namespace) if n) or None
            if row.include in modules:
                emit(row.include, path, ns, (*seen, module))
                continue
            symbol = row.target
            if row.class_view and symbol:
                bindings = context.bind(symbol + ".dispatch")
                if bindings[0].status != "resolved":
                    bindings = context.bind(symbol, allow_class=True)
            else:
                bindings = context.bind(symbol)
            detail = "Django URLconf"
            if row.include:
                detail += f"; unresolved include: {row.include}"
                context.diagnostic(row.location.file_path, "unresolved_include", row.include)
            context.add(
                EndpointRegistration(
                    registration_id(
                        "django",
                        context.owning_root(row.location.file_path),
                        module,
                        seen,
                        row.location.start_line,
                        row.location.start_col,
                        prefix,
                        ns,
                    ),
                    "http",
                    context.owning_root(row.location.file_path),
                    (RegistrationEvidence("static", row.location, detail),),
                    bindings,
                    framework="django",
                    namespace=ns,
                    path=path,
                    registration_key=row.name,
                    schema_binding=row.schema_binding,
                    transport="http",
                )
            )

    for module in sorted(set(modules) - included_modules):
        emit(module, "", None, ())
