"""Parse-local indexes for lexical declarations and extracted callable owners."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import lru_cache, wraps

from tree_sitter import Language, Node, Query, QueryCursor

from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit, NodeKind
from trailmark.parsers._c_headers import function_prototype_name


@lru_cache(maxsize=128)
def _binding_query(
    language: Language, types: frozenset[str], tokens: frozenset[str] = frozenset()
) -> Query:
    patterns = " ".join(
        f"({name})" for name in sorted(types) if language.id_for_node_kind(name, True) is not None
    )
    literals = " ".join(
        f'"{name}"' for name in sorted(tokens) if language.id_for_node_kind(name, False) is not None
    )
    return Query(language, f"[{patterns} {literals}] @binding" if patterns or literals else "")


def syntax_nodes(
    root: Node, types: frozenset[str], *, tokens: frozenset[str] = frozenset()
) -> list[Node]:
    """Capture selected syntax with the active parser's exact grammar."""
    session = _CURRENT.get()
    if session is not None and session.language is not None:
        query = _binding_query(session.language, types, tokens)
        nodes = QueryCursor(query).captures(root).get("binding", [])
        sort_syntax(nodes)
        return nodes
    nodes = []
    pending = [root]
    while pending:
        node = pending.pop()
        if node.type in (types if node.is_named else tokens):
            nodes.append(node)
        pending.extend(reversed(node.children))
    return nodes


def _same_span_depth(node: Node) -> int:
    """Order ancestors before descendants even when they occupy the same span."""
    parent = node.parent
    depth = 0
    while parent is not None and (parent.start_byte, parent.end_byte) == (
        node.start_byte,
        node.end_byte,
    ):
        depth += 1
        parent = parent.parent
    return depth


def sort_syntax(nodes: list[Node]) -> None:
    """Sort captures without walking ancestry except for identical-span ties."""
    if len(nodes) < 2:
        return
    nodes.sort(key=lambda node: (node.start_byte, -node.end_byte))
    start = 0
    while start < len(nodes):
        end = start + 1
        span = (nodes[start].start_byte, nodes[start].end_byte)
        while end < len(nodes) and (nodes[end].start_byte, nodes[end].end_byte) == span:
            end += 1
        if end > start + 1:
            nodes[start:end] = sorted(nodes[start:end], key=_same_span_depth)
        start = end


@dataclass
class _Scope:
    parent: int | None
    declarations: dict[str, list[Node]] = field(default_factory=dict)
    latest: dict[str, Node] = field(default_factory=dict)
    unordered: bool = False
    export_to: int | None = None
    directives: dict[str, str] = field(default_factory=dict)
    callback_parameters: set[str] = field(default_factory=set)


class ScopeIndex:
    """Index a syntax tree once for a language's scope and declaration policy."""

    def __init__(
        self,
        root: Node,
        scopes: frozenset[str],
        fields: tuple[tuple[str, str], ...],
        *,
        language: Language | None = None,
    ) -> None:
        self.owners: dict[int, int | None] = {}
        self.scopes: dict[int, _Scope] = {}
        self.names: set[str] = set()
        self.callback_names: set[str] = set()
        self._class_scopes: set[int] = set()
        self._python = scopes == frozenset({"function_definition", "lambda"})
        self._c_declarations = "init_declarator" in dict(fields)
        self._cpp_declarations = "field_declaration" in dict(fields)
        self._skip_c_globals = root.type == "translation_unit" and dict(fields) == {
            "init_declarator": "declarator",
            "declaration": "declarator",
        }
        if language is None:
            self._build(root, scopes, dict(fields))
        else:
            self._build_captures(root, scopes, dict(fields), language)

    def _build_captures(
        self, root: Node, scopes: frozenset[str], fields: dict[str, str], language: Language
    ) -> None:
        types = scopes | fields.keys()
        if self._python:
            types |= {"class_definition", "global_statement", "nonlocal_statement"}
        captures = (
            QueryCursor(_binding_query(language, frozenset(types)))
            .captures(root)
            .get("binding", [])
        )
        sort_syntax(captures)
        enclosing: list[Node] = []
        skipped_until = -1
        for node in captures:
            if node.start_byte < skipped_until:
                continue
            while enclosing and node.start_byte >= enclosing[-1].end_byte:
                enclosing.pop()
            owner = enclosing[-1].id if enclosing else None
            if _skip_global_data(node, owner, self._skip_c_globals):
                skipped_until = node.end_byte
                continue
            indexed = self._index_node(node, owner, scopes, fields)
            if indexed == node.id:
                enclosing.append(node)
        self._sort_declarations()

    def _build(self, root: Node, scopes: frozenset[str], fields: dict[str, str]) -> None:
        leaf_types = scopes | fields.keys() | ({"identifier"} if "method" in scopes else set())
        pending: list[tuple[Node, int | None]] = [(root, None)]
        while pending:
            node, owner = pending.pop()
            if _skip_global_data(node, owner, self._skip_c_globals):
                continue
            owner = self._index_node(node, owner, scopes, fields)
            pending.extend(
                (child, owner)
                for child in reversed(node.named_children)
                if child.named_child_count or child.type in leaf_types
            )
        self._sort_declarations()

    def _sort_declarations(self) -> None:
        for scope in self.scopes.values():
            for nodes in scope.declarations.values():
                nodes.sort(key=self._visible_at)

    def _index_node(
        self,
        node: Node,
        owner: int | None,
        scopes: frozenset[str],
        fields: dict[str, str],
    ) -> int | None:
        # A function's name belongs to its enclosing scope, not its body.
        if node.type in fields and owner is not None:
            self._declaration(node, owner, fields)
        if node.type in scopes or self._python and node.type == "class_definition":
            owner = self._new_scope(node, owner)
        self.owners[node.id] = owner
        if self._python:
            self._directives(node, owner)
        return owner

    def _new_scope(self, node: Node, owner: int | None) -> int:
        if self._python and node.type in {"function_definition", "lambda"}:
            while owner in self._class_scopes:
                owner = self.scopes[owner].parent
        if self._python and node.type == "class_definition":
            self._class_scopes.add(node.id)
        parameters = _callback_parameters(node)
        self.callback_names.update(parameters)
        self.scopes[node.id] = _Scope(
            owner,
            callback_parameters=parameters,
            unordered=node.type in {"class_body", "enum_class_body", "enum_body"},
        )
        if node.type in {"function_expression", "function", "generator_function"}:
            name = node.child_by_field_name("name")
            if name is not None:
                self._remember(node.id, {_text(name)}, node)
        self._single_parameter(node)
        for parameter in _scope_parameters(node):
            self._remember(node.id, pattern_names(parameter), parameter)
        if (
            node.type == "class_body"
            and node.parent is not None
            and node.parent.type == "companion_object"
        ):
            self.scopes[node.id].export_to = owner
        return node.id

    def _single_parameter(self, node: Node) -> None:
        if node.type == "arrow_function":
            parameter = node.child_by_field_name("parameter")
            if parameter is not None:
                self._remember(node.id, pattern_names(parameter), parameter)

    def _directives(self, node: Node, owner: int | None) -> None:
        if owner is not None and node.type in {"global_statement", "nonlocal_statement"}:
            for name in node.named_children:
                self.scopes[owner].directives[_text(name)] = node.type

    def _declaration(self, node: Node, owner: int, fields: dict[str, str]) -> None:
        field_name = fields.get(node.type)
        if field_name is None:
            return
        if self._c_declarations and node.type in {"declaration", "field_declaration"}:
            for declarator in node.children_by_field_name("declarator"):
                prototype = function_prototype_name(declarator, cpp_block=self._cpp_declarations)
                if prototype is not None:
                    self._remember(owner, {prototype}, declarator)
                    continue
                self._remember(
                    owner, pattern_names(declarator) | _leaf_name(declarator), declarator
                )
            return
        names = declaration_names(node, field_name)
        if (
            node.type == "variable_declaration"
            and node.parent is not None
            and node.parent.type == "property_declaration"
        ):
            node = node.parent
        self._remember(owner, names, node)

    def _remember(self, owner: int, names: set[str], node: Node) -> None:
        scope = self.scopes[owner]
        if scope.export_to is not None:
            self._remember(scope.export_to, names, node)
        for name in names:
            scope.declarations.setdefault(name, []).append(node)
            scope.latest[name] = node
            self.names.add(name)

    def _visible_at(self, node: Node) -> int:
        if node.type == "for":
            pattern = node.child_by_field_name("pattern")
            return pattern.end_byte if pattern is not None else node.end_byte
        declarator = node.child_by_field_name("declarator") if self._c_declarations else None
        return declarator.end_byte if declarator is not None else node.end_byte

    def declaration(self, occurrence: Node, name: str, ordered: bool) -> Node | None:
        if name not in self.names:
            return None
        owner = self._owner(occurrence)
        initial_owner = owner
        while owner is not None:
            scope = self.scopes[owner]
            nodes = scope.declarations.get(name, [])
            ordered_here = not scope.unordered and (
                ordered or self._python and owner == initial_owner
            )
            position = (
                bisect_right(nodes, occurrence.start_byte, key=self._visible_at)
                if ordered_here
                else 0
            )
            if nodes and (not ordered_here or position):
                return nodes[position - 1] if ordered_here else scope.latest[name]
            if nodes and self._python and owner == initial_owner:
                return None
            owner = scope.parent
        return None

    def local_binding(self, occurrence: Node, name: str) -> bool:
        owner = self._owner(occurrence)
        return owner is not None and name in self.scopes[owner].declarations

    def directive(self, occurrence: Node, name: str) -> str | None:
        owner = self._owner(occurrence)
        return self.scopes[owner].directives.get(name) if owner is not None else None

    def callback_parameter(self, occurrence: Node, name: str) -> bool:
        if name not in self.callback_names:
            return False
        owner = self._owner(occurrence)
        while owner is not None:
            scope = self.scopes[owner]
            if name in scope.callback_parameters:
                return True
            owner = scope.parent
        return False

    def _owner(self, occurrence: Node) -> int | None:
        current = occurrence
        pending: list[int] = []
        while current.id not in self.owners:
            pending.append(current.id)
            parent = current.parent
            if parent is None:
                return None
            current = parent
        owner = self.owners[current.id]
        for node_id in pending:
            self.owners[node_id] = owner
        return owner


def _callback_parameters(node: Node) -> set[str]:
    if node.type not in {"lambda", "func_literal", "block", "do_block"}:
        return set()
    parameters = node.child_by_field_name("parameters")
    if parameters is None:
        return set()
    if node.type in {"lambda", "block", "do_block"}:
        return set().union(*(pattern_names(child) for child in parameters.named_children))
    return set().union(*(declaration_names(child, "name") for child in parameters.named_children))


def _scope_parameters(node: Node) -> list[Node]:
    """Index parameters in their declaring scope so captured values also shadow."""
    parameters = node.child_by_field_name("parameters")
    if parameters is not None:
        return parameters.named_children
    if node.type == "function_declaration":
        container = next(
            (c for c in node.named_children if c.type == "function_value_parameters"), None
        )
        return (
            container.named_children
            if container is not None
            else [c for c in node.named_children if c.type == "parameter"]
        )
    return []


@dataclass
class _Session:
    language: Language | None = None
    root: Node | None = None
    errors: list[tuple[int, int]] | None = None
    indexes: dict[tuple[frozenset[str], tuple[tuple[str, str], ...]], ScopeIndex] = field(
        default_factory=dict
    )
    units: dict[tuple[str, int, int | None, str], dict[str, CodeUnit]] = field(default_factory=dict)
    locations: dict[tuple[str, int, int | None], dict[str, CodeUnit]] = field(default_factory=dict)
    parameters: dict[str, frozenset[str]] = field(default_factory=dict)
    import_targets: dict[tuple[int, str, str], str | None] = field(default_factory=dict)


_CURRENT: ContextVar[_Session | None] = ContextVar("_BINDING_INDEX", default=None)


def binding_scope[**P, T](function: Callable[P, T]) -> Callable[P, T]:
    """Release syntax indexes when a single-file parse finishes, including on error."""

    @wraps(function)
    def parse(*args: P.args, **kwargs: P.kwargs) -> T:
        parser = getattr(args[0], "_parser", None) if args else None
        language = getattr(parser, "language", None)
        if args and any(hasattr(args[0], name) for name in ("_tsx_parser", "_verus_parser")):
            language = None
        token = _CURRENT.set(_Session(language=language))
        try:
            return function(*args, **kwargs)
        finally:
            _CURRENT.reset(token)

    return parse


def initialize_bindings(root: Node, *, language: Language | None = None) -> None:
    session = _CURRENT.get()
    if session is not None and (session.root != root or language is not None):
        session.root = root
        session.errors = None
        session.indexes.clear()
        if language is not None:
            session.language = language


def record_unit(unit: CodeUnit) -> None:
    session = _CURRENT.get()
    if session is not None:
        session.parameters.pop(unit.id, None)
        position = (unit.location.file_path, unit.location.start_line, unit.location.start_col)
        session.locations[position] = {unit.id: unit}
        key = (*position, unit.name)
        # Moving an overload replaces the original identity at the same location.
        session.units[key] = {unit.id: unit}


def call_parameter(source: CodeUnit | None, name: str) -> bool:
    """Reuse each immutable callable's parameter names during its file parse."""
    if source is None:
        return False
    session = _CURRENT.get()
    if session is None:
        return any(parameter.name == name for parameter in source.parameters)
    if source.id not in session.parameters:
        session.parameters[source.id] = frozenset(parameter.name for parameter in source.parameters)
    return name in session.parameters[source.id]


def local_unit(graph: CodeGraph, declaration: Node, name: str, source: CodeUnit) -> CodeUnit | None:
    session = _CURRENT.get()
    key = (
        source.location.file_path,
        declaration.start_point.row + 1,
        declaration.start_point.column,
        name,
    )
    if session is None:
        units = [
            u
            for u in graph.nodes.values()
            if (u.location.file_path, u.location.start_line, u.location.start_col, u.name) == key
        ]
    else:
        indexed = (
            session.locations.get(key[:3], {})
            if declaration.type in {"function_expression", "function", "generator_function"}
            else session.units.get(key, {})
        )
        units = list(indexed.values())
    units = [
        unit for unit in units if unit.kind in {NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS}
    ]
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
    index = ScopeIndex(
        root, scopes, declarations, language=session.language if session is not None else None
    )
    if session is not None:
        session.indexes[key] = index
    return index


def declaration_names(node: Node, field_name: str) -> set[str]:
    if node.type == "formal_parameters" and (
        node.parent is None or node.parent.child_by_field_name("body") is None
    ):
        return set()
    patterns = (
        node.named_children
        if node.type == "formal_parameters"
        else node.children_by_field_name(field_name)
    )
    if not patterns and node.type in {"local_function_declaration", "function_declaration"}:
        patterns = _local_function_names(node)
    if not patterns and node.type in {
        "variable_declaration",
        "function_declaration",
        "initialized_variable_definition",
        "initialized_identifier",
        "exception_variable",
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


def _leaf_name(node: Node) -> set[str]:
    return {_text(node)} if not node.named_children else set()


def _text(node: Node) -> str:
    return node.text.decode(errors="replace") if node.text else ""


def pattern_names(node: Node) -> set[str]:
    """Read declaration patterns without treating initializers or types as names."""
    names: set[str] = set()
    pending = [node]
    while pending:
        current = pending.pop()
        if current.type in _PATTERN_IDENTIFIERS:
            names.add(_text(current))
        else:
            pending.extend(_pattern_children(current))
    return names


_GLOBAL_C_DATA = frozenset({"declaration", "type_definition", "struct_specifier", "enum_specifier"})


def _skip_global_data(node: Node, owner: int | None, c_declarations: bool) -> bool:
    return c_declarations and owner is None and node.type in _GLOBAL_C_DATA


_PATTERN_IDENTIFIERS = frozenset(
    {
        "variable_identifier",
        "identifier",
        "simple_identifier",
        "field_identifier",
        "shorthand_property_identifier_pattern",
    }
)


_PATTERN_CONTAINERS = frozenset(
    {
        "parenthesized_declarator",
        "pointer_declarator",
        "reference_declarator",
        "array_declarator",
        "tuple_pattern",
        "list_pattern",
        "left_assignment_list",
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
        "default_parameter",
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
        "destructured_parameter",
    }
)


def _pattern_children(node: Node) -> list[Node]:
    if node.type in {
        "splat_parameter",
        "hash_splat_parameter",
        "block_parameter",
        "optional_parameter",
        "keyword_parameter",
        "required_parameter",
        "parameter",
    }:
        name = node.child_by_field_name("name") or node.child_by_field_name("pattern")
        if name is None:
            name = next((c for c in node.named_children if c.type in _PATTERN_IDENTIFIERS), None)
        return [name] if name is not None else []
    if node.type in {"pair_pattern", "object_assignment_pattern"}:
        target = node.child_by_field_name("value") or node.child_by_field_name("left")
        return [target] if target is not None else []
    declarator = node.child_by_field_name("declarator")
    if declarator is not None:
        return [declarator]
    if node.type in _PATTERN_CONTAINERS:
        children = node.named_children
        if node.type in {
            "typed_parameter",
            "typed_default_parameter",
            "default_parameter",
            "assignment_pattern",
        }:
            children = children[:1]
        return children
    return []


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


def cached_import_target(
    key: tuple[int, str, str], resolve: Callable[[], str | None]
) -> str | None:
    """Cache each declaration's import target for the lifetime of one parse."""
    session = _CURRENT.get()
    if session is None:
        return resolve()
    if key not in session.import_targets:
        session.import_targets[key] = resolve()
    return session.import_targets[key]
