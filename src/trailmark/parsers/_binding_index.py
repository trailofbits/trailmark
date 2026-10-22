"""Parse-local indexes for lexical declarations and extracted callable owners."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps

from tree_sitter import Node

from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit, NodeKind


@dataclass
class _Scope:
    parent: int | None
    declarations: dict[str, list[Node]] = field(default_factory=dict)
    directives: dict[str, str] = field(default_factory=dict)
    callback_parameters: set[str] = field(default_factory=set)


class ScopeIndex:
    """Index a syntax tree once for a language's scope and declaration policy."""

    def __init__(
        self, root: Node, scopes: frozenset[str], fields: tuple[tuple[str, str], ...]
    ) -> None:
        self.owners: dict[int, int | None] = {}
        self.scopes: dict[int, _Scope] = {}
        self.names: set[str] = set()
        self.callback_names: set[str] = set()
        self._class_scopes: set[int] = set()
        self._python = scopes == frozenset({"function_definition", "lambda"})
        self._build(root, scopes, dict(fields))

    def _build(self, root: Node, scopes: frozenset[str], fields: dict[str, str]) -> None:
        pending: list[tuple[Node, int | None]] = [(root, None)]
        c_declarations = root.type == "translation_unit" and fields == {
            "init_declarator": "declarator",
            "declaration": "declarator",
        }
        while pending:
            node, owner = pending.pop()
            if (
                c_declarations
                and owner is None
                and node.type
                in {"declaration", "type_definition", "struct_specifier", "enum_specifier"}
            ):
                continue
            # A function's name belongs to its enclosing scope, not its body.
            if owner is not None and node != root:
                self._declaration(node, owner, fields)
            if node.type in scopes or self._python and node.type == "class_definition":
                owner = self._new_scope(node, owner)
            self.owners[node.id] = owner
            self._directives(node, owner)
            pending.extend((child, owner) for child in reversed(node.named_children))
        for scope in self.scopes.values():
            for nodes in scope.declarations.values():
                nodes.sort(key=lambda node: node.end_byte)

    def _new_scope(self, node: Node, owner: int | None) -> int:
        if self._python and node.type in {"function_definition", "lambda"}:
            while owner in self._class_scopes:
                owner = self.scopes[owner].parent
        if self._python and node.type == "class_definition":
            self._class_scopes.add(node.id)
        parameters = _callback_parameters(node)
        self.callback_names.update(parameters)
        self.scopes[node.id] = _Scope(owner, callback_parameters=parameters)
        return node.id

    def _directives(self, node: Node, owner: int | None) -> None:
        if owner is not None and node.type in {"global_statement", "nonlocal_statement"}:
            for name in node.named_children:
                self.scopes[owner].directives[_text(name)] = node.type

    def _declaration(self, node: Node, owner: int, fields: dict[str, str]) -> None:
        field_name = fields.get(node.type)
        if field_name is None or _require_import(node):
            return
        names = declaration_names(node, field_name)
        if (
            node.type == "variable_declaration"
            and node.parent is not None
            and node.parent.type == "property_declaration"
        ):
            node = node.parent
        for name in names:
            self.scopes[owner].declarations.setdefault(name, []).append(node)
            self.names.add(name)

    def declaration(self, occurrence: Node, name: str, ordered: bool) -> Node | None:
        if name not in self.names:
            return None
        owner = self.owners.get(occurrence.id)
        while owner is not None:
            scope = self.scopes[owner]
            nodes = scope.declarations.get(name, [])
            position = bisect_right(nodes, occurrence.start_byte, key=lambda node: node.end_byte)
            if nodes and (not ordered or position):
                return (
                    nodes[position - 1] if ordered else max(nodes, key=lambda node: node.start_byte)
                )
            owner = scope.parent
        return None

    def directive(self, occurrence: Node, name: str) -> str | None:
        owner = self.owners.get(occurrence.id)
        return self.scopes[owner].directives.get(name) if owner is not None else None

    def callback_parameter(self, occurrence: Node, name: str) -> bool:
        if name not in self.callback_names:
            return False
        owner = self.owners.get(occurrence.id)
        while owner is not None:
            scope = self.scopes[owner]
            if name in scope.callback_parameters:
                return True
            owner = scope.parent
        return False


def _callback_parameters(node: Node) -> set[str]:
    if node.type not in {"lambda", "func_literal"}:
        return set()
    parameters = node.child_by_field_name("parameters")
    if parameters is None:
        return set()
    if node.type == "lambda":
        return set().union(*(pattern_names(child) for child in parameters.named_children))
    return set().union(*(declaration_names(child, "name") for child in parameters.named_children))


@dataclass
class _Session:
    root: Node | None = None
    errors: list[tuple[int, int]] | None = None
    indexes: dict[tuple[frozenset[str], tuple[tuple[str, str], ...]], ScopeIndex] = field(
        default_factory=dict
    )
    units: dict[tuple[str, int, str], dict[str, CodeUnit]] = field(default_factory=dict)
    locations: dict[tuple[str, int, int | None], dict[str, CodeUnit]] = field(default_factory=dict)


_CURRENT: ContextVar[_Session | None] = ContextVar("_BINDING_INDEX", default=None)


def binding_scope[**P, T](function: Callable[P, T]) -> Callable[P, T]:
    """Release syntax indexes when a single-file parse finishes, including on error."""

    @wraps(function)
    def parse(*args: P.args, **kwargs: P.kwargs) -> T:
        token = _CURRENT.set(_Session())
        try:
            return function(*args, **kwargs)
        finally:
            _CURRENT.reset(token)

    return parse


def initialize_bindings(root: Node) -> None:
    session = _CURRENT.get()
    if session is not None and session.root != root:
        session.root = root
        session.errors = None
        session.indexes.clear()


def record_unit(unit: CodeUnit) -> None:
    session = _CURRENT.get()
    if session is not None:
        position = (unit.location.file_path, unit.location.start_line, unit.location.start_col)
        session.locations[position] = {unit.id: unit}
        key = (unit.location.file_path, unit.location.start_line, unit.name)
        # Moving an overload replaces the original identity at the same location.
        session.units[key] = {unit.id: unit}


def local_unit(graph: CodeGraph, declaration: Node, name: str, source: CodeUnit) -> CodeUnit | None:
    session = _CURRENT.get()
    key = (source.location.file_path, declaration.start_point.row + 1, name)
    if session is None:
        units = [
            u
            for u in graph.nodes.values()
            if (u.location.file_path, u.location.start_line, u.name) == key
        ]
    else:
        units = list(session.units.get(key, {}).values())
    units = [unit for unit in units if unit.kind == NodeKind.FUNCTION]
    return units[0] if len(units) == 1 else None


def scope_index(
    occurrence: Node, scopes: frozenset[str], declarations: tuple[tuple[str, str], ...]
) -> ScopeIndex:
    session = _CURRENT.get()
    key = (scopes, declarations)
    if session is not None and key in session.indexes:
        return session.indexes[key]
    root = session.root if session is not None else None
    if root is None:
        root = occurrence
        while root.parent is not None:
            root = root.parent
    index = ScopeIndex(root, scopes, declarations)
    if session is not None:
        session.indexes[key] = index
    return index


def declaration_names(node: Node, field_name: str) -> set[str]:
    patterns = node.children_by_field_name(field_name)
    if not patterns and node.type == "local_function_declaration":
        patterns = _local_function_names(node)
    if not patterns and node.type in {
        "variable_declaration",
        "function_declaration",
        "initialized_variable_definition",
        "initialized_identifier",
    }:
        patterns = [
            child
            for child in node.named_children
            if child.type in {"simple_identifier", "identifier"}
        ][:1]
    return set().union(*(pattern_names(pattern) | _leaf_name(pattern) for pattern in patterns))


def _local_function_names(node: Node) -> list[Node]:
    names = []
    for wrapper in node.named_children:
        parameters = wrapper.child_by_field_name("parameters")
        name = parameters.child_by_field_name("name") if parameters is not None else None
        if name is not None:
            names.append(name)
    return names


def _require_import(node: Node) -> bool:
    value = node.child_by_field_name("value")
    if node.type != "variable_declarator" or value is None or value.type != "call_expression":
        return False
    function = value.child_by_field_name("function")
    return function is not None and function.text == b"require"


def _leaf_name(node: Node) -> set[str]:
    return {_text(node)} if not node.named_children else set()


def _text(node: Node) -> str:
    return node.text.decode(errors="replace") if node.text else ""


def pattern_names(node: Node) -> set[str]:
    """Read declaration patterns without treating initializers or types as names."""
    if node.type in {
        "variable_identifier",
        "identifier",
        "simple_identifier",
        "field_identifier",
        "shorthand_property_identifier_pattern",
    }:
        return {node.text.decode()} if node.text is not None else set()
    if node.type in {"pair_pattern", "object_assignment_pattern"}:
        target = node.child_by_field_name("value") or node.child_by_field_name("left")
        return pattern_names(target) if target is not None else set()
    declarator = node.child_by_field_name("declarator")
    if declarator is not None:
        return pattern_names(declarator)
    if node.type in {
        "parenthesized_declarator",
        "pointer_declarator",
        "reference_declarator",
        "array_declarator",
        "tuple_pattern",
        "list_pattern",
        "pattern_list",
        "object_pattern",
        "array_pattern",
        "expression_list",
        "tuple_struct_pattern",
        "slice_pattern",
        "ref_pattern",
        "mut_pattern",
        "typed_parameter",
        "typed_default_parameter",
        "list_splat_pattern",
        "dictionary_splat_pattern",
        "pattern",
        "bind_list",
        "bind_var",
        "mut_bind_var",
        "comma_bind_list",
        "lambda_bindings",
        "lambda_binding",
        "rest_pattern",
        "assignment_pattern",
    }:
        children = node.named_children
        if node.type in {"typed_parameter", "typed_default_parameter", "assignment_pattern"}:
            children = children[:1]
        return set().union(*(pattern_names(child) for child in children))
    return set()


def units_at(node: Node, path: str) -> list[CodeUnit]:
    session = _CURRENT.get()
    if session is None:
        return []
    key = (path, node.start_point.row + 1, node.start_point.column)
    return list(session.locations.get(key, {}).values())


def inside_error(node: Node) -> bool | None:
    session = _CURRENT.get()
    if session is None or session.root is None:
        return None
    if session.errors is None:
        session.errors = _error_spans(session.root)
    position = bisect_right(session.errors, node.start_byte, key=lambda span: span[0])
    return position > 0 and node.end_byte <= session.errors[position - 1][1]


def _error_spans(root: Node) -> list[tuple[int, int]]:
    spans = []
    pending = [root] if root.has_error else []
    while pending:
        node = pending.pop()
        if node.type == "ERROR":
            spans.append((node.start_byte, node.end_byte))
        else:
            pending.extend(child for child in node.named_children if child.has_error)
    return sorted(spans)
