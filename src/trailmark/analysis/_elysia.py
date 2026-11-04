"""Bounded, syntax-only Elysia HTTP registration discovery.

Only receivers rooted in Elysia ES imports and local const bindings qualify.
Registration is evidence of a potential HTTP entrypoint, not proof that an app
is mounted or listening. No calls are invented for route registration itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tree_sitter import Node

from trailmark.analysis._entrypoint_declarations import DeclarationCache
from trailmark.models.annotations import AssetValue, EntrypointKind, EntrypointTag, TrustLevel
from trailmark.models.entrypoints import EntrypointEvidence
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit, NodeKind
from trailmark.parsers._binding_index import ScopeIndex, pattern_names, syntax_nodes
from trailmark.parsers._callable_syntax import unwrap_callable
from trailmark.parsers._common import make_location, node_text
from trailmark.parsers._ecmascript import CALLABLES

_EXTENSIONS = frozenset({".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"})
_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "all"})
# Known builder methods return the Elysia instance. Unknown calls end provenance.
_FLUENT = _METHODS | {
    "route",
    "use",
    "group",
    "guard",
    "state",
    "decorate",
    "model",
    "derive",
    "resolve",
    "mapDerive",
    "mapResolve",
    "macro",
    "as",
    "headers",
    "on",
    "onRequest",
    "onParse",
    "onTransform",
    "onBeforeHandle",
    "onAfterHandle",
    "onMapResponse",
    "onAfterResponse",
    "onError",
    "onStart",
    "onStop",
    "trace",
}
_SCOPES = CALLABLES | {
    "program",
    "statement_block",
    "switch_body",
    "class_body",
    "class",
    "class_declaration",
    "abstract_class_declaration",
    "class_static_block",
    "catch_clause",
    "for_statement",
    "for_in_statement",
}
_FIELDS = (
    ("variable_declarator", "name"),
    ("function_declaration", "name"),
    ("generator_function_declaration", "name"),
    ("class_declaration", "name"),
    ("assignment_expression", "left"),
    ("augmented_assignment_expression", "left"),
    ("catch_clause", "parameter"),
    ("for_in_statement", "left"),
)


@dataclass(frozen=True)
class _Value:
    kind: str
    node: Node | None = None


_UNKNOWN = _Value("unknown")


def detect_elysia_entrypoints(
    graph: CodeGraph, declarations: DeclarationCache
) -> dict[str, EntrypointTag]:
    """Tag locally bound HTTP handlers and retain unresolved registration evidence."""
    files: dict[str, dict[tuple[int, int | None], CodeUnit]] = {}
    for unit in graph.nodes.values():
        path = unit.location.file_path
        if Path(path).suffix.lower() in _EXTENSIONS:
            units = files.setdefault(path, {})
            if unit.kind in {NodeKind.FUNCTION, NodeKind.METHOD}:
                units[(unit.location.start_line, unit.location.start_col)] = unit
    result: dict[str, EntrypointTag] = {}
    for path, units in files.items():
        root = declarations.ecmascript_root(path)
        if root is None:
            continue
        imports = _imports(root)
        if not imports:
            continue
        analyzer = _Analyzer(root, imports)
        for call in syntax_nodes(root, frozenset({"call_expression"})):
            route = analyzer.route(call)
            if route is None:
                continue
            method, path_node, handler = route
            value = analyzer.value(handler)
            target = value.node if value.kind == "handler" else None
            unit = (
                units.get((target.start_point.row + 1, target.start_point.column))
                if target
                else None
            )
            # Elysia accepts static response values too; they have no callable node.
            if value.kind == "static":
                continue
            route_path = _literal(path_node)
            graph.add_entrypoint_evidence(
                EntrypointEvidence(
                    unit.id if unit else None,
                    "heuristic",
                    "elysia.route",
                    make_location(call, path),
                    symbol=node_text(handler)[:120],
                    binding="resolved" if unit else "unresolved",
                    details=(("framework", "elysia"), ("method", method), ("path", route_path)),
                    arguments=(node_text(path_node)[:120],),
                    unresolved=(("handler_binding",) if unit is None else ())
                    + (("route_path",) if route_path is None else ())
                    + (("route_method",) if method == "dynamic" else ())
                    + ("registrar_semantics", "runtime_activation"),
                )
            )
            if unit is not None:
                result[unit.id] = EntrypointTag(
                    kind=EntrypointKind.API,
                    trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
                    asset_value=AssetValue.HIGH,
                    description="Elysia HTTP route handler",
                )
    return result


class _Analyzer:
    def __init__(self, root: Node, imports: dict[str, _Value]) -> None:
        self.imports = imports
        self.index = ScopeIndex(root, _SCOPES, _FIELDS)
        self.cache: dict[int, _Value] = {}
        # A write in any branch makes the name unsafe to resolve through its initializer.
        self.written: set[str] = set()
        for node in syntax_nodes(
            root,
            frozenset(
                {
                    "assignment_expression",
                    "augmented_assignment_expression",
                    "update_expression",
                    "for_in_statement",
                }
            ),
        ):
            if node.type == "for_in_statement" and any(
                child.type in {"const", "let", "var"} for child in node.children
            ):
                continue
            target = node.child_by_field_name("left") or node.child_by_field_name("argument")
            if target is not None:
                self.written.update(pattern_names(target))

    def value(self, node: Node, depth: int = 0) -> _Value:
        if depth >= 64:
            return _UNKNOWN
        node = unwrap_callable(node)
        if node.id not in self.cache:
            self.cache[node.id] = _UNKNOWN  # Cyclic aliases stay unresolved.
            self.cache[node.id] = self._value(node, depth + 1)
        return self.cache[node.id]

    def _value(self, node: Node, depth: int) -> _Value:
        if node.type in CALLABLES:
            return _Value("handler", node)
        if node.type in {
            "string",
            "template_string",
            "number",
            "true",
            "false",
            "null",
            "object",
            "array",
        }:
            return _Value("static")
        if node.type == "identifier":
            name = node_text(node)
            if name in self.written:
                return _UNKNOWN
            declaration = self.index.declaration(node, name, ordered=False)
            if declaration is None:
                return self.imports.get(name, _UNKNOWN)
            if declaration.type in {"function_declaration", "generator_function_declaration"}:
                return _Value("handler", declaration)
            if declaration.type == "variable_declarator":
                binding = declaration.child_by_field_name("name")
                parent = declaration.parent
                if binding is None or binding.type != "identifier" or parent is None:
                    return _UNKNOWN
                if not any(child.type == "const" for child in parent.children):
                    return _UNKNOWN
                value = declaration.child_by_field_name("value")
                if value is not None:
                    return self.value(value, depth)
            return _UNKNOWN
        if node.type == "member_expression":
            receiver = node.child_by_field_name("object")
            member = node.child_by_field_name("property")
            if (
                receiver is not None
                and member is not None
                and self.value(receiver, depth).kind == "namespace"
                and node_text(member) in {"Elysia", "default"}
            ):
                return _Value("constructor")
        if node.type == "new_expression":
            constructor = node.child_by_field_name("constructor")
            if constructor is not None and self.value(constructor, depth).kind == "constructor":
                return _Value("instance")
        if node.type == "call_expression":
            member = _member(node)
            if member is not None:
                receiver, method = member
                if method in _FLUENT and self.value(receiver, depth).kind == "instance":
                    return _Value("instance")
        return _UNKNOWN

    def route(self, call: Node) -> tuple[str, Node, Node] | None:
        member = _member(call)
        if member is None:
            return None
        receiver, method = member
        if method not in _METHODS | {"route"} or self.value(receiver).kind != "instance":
            return None
        arguments = call.child_by_field_name("arguments")
        args = [c for c in arguments.named_children if not c.is_extra] if arguments else []
        offset = 1 if method == "route" else 0
        if len(args) < offset + 2 or any(c.type == "spread_element" for c in args[: offset + 2]):
            return None
        verb = (_literal(args[0]) or "dynamic") if offset else method.upper()
        return verb, args[offset], args[offset + 1]


def _member(call: Node) -> tuple[Node, str] | None:
    function = call.child_by_field_name("function")
    if function is None or function.type != "member_expression":
        return None
    receiver = function.child_by_field_name("object")
    member = function.child_by_field_name("property")
    if receiver is None or member is None or member.type != "property_identifier":
        return None
    return receiver, node_text(member)


def _literal(node: Node) -> str | None:
    node = unwrap_callable(node)
    if node.type not in {"string", "template_string"}:
        return None
    if any(
        child.type in {"template_substitution", "escape_sequence"} for child in node.named_children
    ):
        return None
    return node_text(node)[1:-1]


def _imports(root: Node) -> dict[str, _Value]:
    result: dict[str, _Value] = {}
    for statement in root.named_children:
        if statement.type != "import_statement" or any(
            c.type == "type" for c in statement.children
        ):
            continue
        source = statement.child_by_field_name("source")
        if source is None or _literal(source) != "elysia":
            continue
        clause = next((c for c in statement.named_children if c.type == "import_clause"), None)
        if clause is None:
            continue
        for child in clause.named_children:
            if child.type == "identifier":
                result[node_text(child)] = _Value("constructor")
            elif child.type == "namespace_import":
                name = next((c for c in child.named_children if c.type == "identifier"), None)
                if name is not None:
                    result[node_text(name)] = _Value("namespace")
            elif child.type == "named_imports":
                for specifier in child.named_children:
                    if any(c.type == "type" for c in specifier.children):
                        continue
                    name = specifier.child_by_field_name("name")
                    alias = specifier.child_by_field_name("alias") or name
                    if (
                        name is not None
                        and alias is not None
                        and node_text(name) in {"Elysia", "default"}
                    ):
                        result[node_text(alias)] = _Value("constructor")
    return result
