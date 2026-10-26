"""Resolve Python HTTP decorators from lexical framework receiver evidence.

This module reads syntax only. It never imports or executes the inspected code.
Unknown receivers are retained as diagnostics, rather than HTTP claims.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from trailmark.models.nodes import SourceLocation

_CONSTRUCTORS = {
    "flask.Flask": "flask",
    "flask.Blueprint": "flask",
    "fastapi.FastAPI": "fastapi",
    "fastapi.APIRouter": "fastapi",
    "aiohttp.web.RouteTableDef": "aiohttp",
    "sanic.Sanic": "sanic",
    "sanic.Blueprint": "sanic",
}
_VERBS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})
_METHODS = _VERBS | {"route", "api_route", "websocket"}


@dataclass(frozen=True)
class PythonHTTPRoute:
    handler_line: int
    handler_col: int
    location: SourceLocation
    receiver: str
    framework: str | None
    methods: tuple[str, ...]
    path: str | None
    reason: str | None = None
    transport: str = "http"


@dataclass(frozen=True)
class _Binding:
    name: str = ""
    framework: str | None = None
    prefix: str | None = ""
    factory: bool = False


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _name(node.value)
        return f"{base}.{node.attr}" if base else ""
    return ""


def _string(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def python_http_routes(path: str) -> list[PythonHTTPRoute]:
    """Return proven registrations and unresolved HTTP-shaped decorators."""
    try:
        module = ast.parse(Path(path).read_bytes())
    except (OSError, SyntaxError, UnicodeError):
        return []
    result: list[PythonHTTPRoute] = []

    def resolve(node: ast.AST, scope: dict[str, _Binding]) -> _Binding:
        if isinstance(node, ast.Name):
            return scope.get(node.id, _Binding())
        if isinstance(node, ast.Attribute):
            parent = resolve(node.value, scope)
            return _Binding(name=f"{parent.name}.{node.attr}" if parent.name else "")
        if isinstance(node, ast.Call):
            target = resolve(node.func, scope)
            if target.factory:
                return _Binding(framework=target.framework, prefix=target.prefix)
            framework = _CONSTRUCTORS.get(target.name)
            if framework:
                keywords = {k.arg: k.value for k in node.keywords}
                prefix_node = keywords.get("url_prefix", keywords.get("prefix"))
                prefix = _string(prefix_node) if prefix_node is not None else ""
                return _Binding(framework=framework, prefix=prefix)
        return _Binding()

    def visit(body: list[ast.stmt], scope: dict[str, _Binding]) -> list[_Binding]:
        returns: list[_Binding] = []
        for statement in body:
            if isinstance(statement, ast.Import):
                for item in statement.names:
                    scope[item.asname or item.name.split(".")[0]] = _Binding(
                        name=item.name if item.asname else item.name.split(".")[0]
                    )
            elif isinstance(statement, ast.ImportFrom):
                for item in statement.names:
                    scope[item.asname or item.name] = _Binding(
                        name=f"{statement.module}.{item.name}" if not statement.level else ""
                    )
            elif isinstance(statement, (ast.Assign, ast.AnnAssign)):
                value = (
                    resolve(statement.value, scope) if statement.value is not None else _Binding()
                )
                targets = (
                    statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                )
                for target in targets:
                    for child in ast.walk(target):
                        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                            scope[child.id] = value if isinstance(target, ast.Name) else _Binding()
            elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in statement.decorator_list:
                    if not isinstance(decorator, ast.Call) or not isinstance(
                        decorator.func, ast.Attribute
                    ):
                        continue
                    verb = decorator.func.attr
                    if verb not in _METHODS:
                        continue
                    receiver = resolve(decorator.func.value, scope)
                    keywords = {k.arg: k.value for k in decorator.keywords}
                    path_node = (
                        decorator.args[0]
                        if decorator.args
                        else keywords.get("path", keywords.get("rule"))
                    )
                    route_path = _string(path_node)
                    prefix = receiver.prefix
                    route_path = (
                        prefix + route_path
                        if prefix is not None and route_path is not None
                        else None
                    )
                    methods: tuple[str, ...] = (verb.upper(),) if verb in _VERBS else ()
                    method_node = keywords.get("methods")
                    if isinstance(method_node, (ast.List, ast.Tuple)):
                        values = [_string(n) for n in method_node.elts]
                        if all(v is not None for v in values):
                            methods = tuple(sorted({v.upper() for v in values if v is not None}))
                    elif verb == "route" and receiver.framework == "flask" and method_node is None:
                        methods = ("GET",)
                    result.append(
                        PythonHTTPRoute(
                            statement.lineno,
                            statement.col_offset,
                            SourceLocation(
                                path,
                                decorator.lineno,
                                decorator.end_lineno or decorator.lineno,
                                decorator.col_offset,
                                decorator.end_col_offset or 0,
                            ),
                            _name(decorator.func.value),
                            receiver.framework,
                            methods,
                            route_path,
                            None
                            if receiver.framework
                            else (
                                "non_http_receiver"
                                if receiver.name.startswith("unittest.mock")
                                else "unresolved_receiver"
                            ),
                            transport="websocket" if verb == "websocket" else "http",
                        )
                    )
                inner = scope.copy()
                args = statement.args
                for argument in [
                    *args.posonlyargs,
                    *args.args,
                    *args.kwonlyargs,
                    args.vararg,
                    args.kwarg,
                ]:
                    if argument is not None:
                        inner[argument.arg] = _Binding()
                inferred = visit(statement.body, inner)
                known = inferred[0] if inferred else _Binding()
                scope[statement.name] = (
                    _Binding(framework=known.framework, prefix=known.prefix, factory=True)
                    if known.framework and all(r == known for r in inferred)
                    else _Binding()
                )
            elif isinstance(statement, ast.ClassDef):
                visit(statement.body, scope.copy())
                scope[statement.name] = _Binding()
            elif isinstance(statement, ast.Return):
                returns.append(resolve(statement.value, scope) if statement.value else _Binding())
            elif isinstance(statement, ast.Delete):
                for target in statement.targets:
                    scope.pop(_name(target), None)
            else:
                # Conditional assignments must agree on every path. Loops,
                # try blocks and dynamic control flow cannot establish a receiver.
                for child in ast.walk(statement):
                    if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                        scope[child.id] = _Binding()
        return returns

    visit(module.body, {})
    return result
