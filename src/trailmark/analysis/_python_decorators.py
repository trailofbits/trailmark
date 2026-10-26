"""Lexical binding evidence for Python function decorators.

Each decorator callee is resolved through imports, assignments, parameters,
and local definitions using tree-sitter syntax only; target code is never
imported or executed. Results are evidence, not proof of registrar behavior:
a ``''`` or slash-prefixed first positional string is a route-shaped
heuristic. Names with competing, conditional, or externally rebound
bindings resolve as ``ambiguous`` and never inherit a global's provenance;
a ``from m import *`` after a definition, or before a deferred lookup, is
such a competing binding.

A local identity decorator (``lambda f: f``) is only proven non-registering
when nothing in the file can replace it: no attribute store of its name, no
``setattr``/``vars``/``globals``/``exec``-style dynamic writes, and for a
method, a class without bases, ``__new__``, or ``__getattribute__``.
Otherwise the receiver is ``ambiguous``. Writes from other files are not seen.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from tree_sitter import Node, Parser
from tree_sitter_language_pack import get_language

from trailmark.models.nodes import SourceLocation

ReceiverBinding = Literal[
    "import_module",
    "import_name",
    "call_result",
    "parameter",
    "enclosing_local",
    "unbound",
    "ambiguous",
]
Disposition = Literal["path_decorator", "non_path_decorator", "unknown"]

_MAX_DEPTH = 64
_MAX_ARGUMENT = 120
_SCOPES = frozenset({"function_definition", "class_definition"})
_SKIPPED = frozenset(
    {"lambda", "list_comprehension", "set_comprehension", "dictionary_comprehension"}
    | {"generator_expression"}
)
_UNRESOLVED = frozenset({"parameter", "unbound", "ambiguous"})
_IMPORTS: dict[str, ReceiverBinding] = {
    "import_module": "import_module",
    "import_name": "import_name",
}
_KEYWORD_ARGUMENTS = frozenset({"keyword_argument", "dictionary_splat"})
_STRINGS = frozenset({"string", "concatenated_string"})
_NON_STRING_LITERALS = frozenset(
    {"integer", "float", "true", "false", "none", "list", "tuple", "dictionary", "set"}
)
# Statements whose identifier targets rebind names to values we do not model.
_OPAQUE_TARGETS = {
    "for_statement": "left",
    "augmented_assignment": "left",
    "named_expression": "name",
    "type_alias_statement": "left",
}
# Statement field holding targets whose attribute stores can replace a member.
_STORE_FIELDS = {
    "assignment": "left",
    "augmented_assignment": "left",
    "for_statement": "left",
    "for_in_clause": "left",
}
# Names that write attributes or globals without a visible attribute store.
_DYNAMIC_NAMES = frozenset(
    {"setattr", "delattr", "vars", "globals", "exec", "eval", "__dict__", "__setattr__"}
    | {"__delattr__"}
)
# Class members that can make instance or attribute lookup return something else.
_DYNAMIC_MEMBERS = ("__new__", "__getattribute__")
_NOT_STRING = object()


@dataclass(frozen=True)
class DecoratorFact:
    """Binding evidence for one decorator on a function definition."""

    handler_line: int
    handler_col: int
    location: SourceLocation
    consumer_path: str | None
    receiver_binding: ReceiverBinding
    method: str | None
    path: str | None
    arguments: tuple[str, ...]
    disposition: Disposition
    reason: str
    member_access: bool = False


@dataclass(frozen=True)
class _Binding:
    # import_module | import_name | assign | parameter | definition | opaque | remote
    kind: str
    start: int
    direct: bool
    target: Node | str | None = None


_OPAQUE = _Binding("opaque", -1, False)


@dataclass
class _Scope:
    body: Node
    parent: _Scope | None
    is_class: bool
    bindings: dict[str, list[_Binding]] = field(default_factory=dict)
    declared_global: set[str] = field(default_factory=set)
    declared_nonlocal: set[str] = field(default_factory=set)
    star_imports: list[int] = field(default_factory=list)

    def bind(self, name: str, kind: str, node: Node, target: Node | str | None) -> None:
        binding = _Binding(kind, node.start_byte, _statement_block(node) == self.body, target)
        self.bindings.setdefault(name, []).append(binding)


@dataclass(frozen=True)
class _Value:
    binding: ReceiverBinding
    path: str | None = None
    node: Node | None = None
    method: str | None = None
    member_access: bool = False


def python_decorators(path: str) -> list[DecoratorFact]:
    """Collect decorator binding evidence for every function in a Python file.

    Args:
        path: Python source file. Unreadable files yield no facts.

    Returns:
        Facts in source order, one per decorator on a function definition.
    """
    try:
        source = Path(path).read_bytes()
    except OSError:
        return []
    root = Parser(get_language("python")).parse(source).root_node
    return _Analyzer(path, root).facts()


class _Analyzer:
    def __init__(self, path: str, root: Node) -> None:
        self._path = path
        self._root = root
        self._scopes = _build_scopes(root)
        self._module = self._scopes[root.id]
        self._stored, self._dynamic = _attribute_writes(root)

    def facts(self) -> list[DecoratorFact]:
        facts: list[DecoratorFact] = []
        pending = [self._root]
        while pending:
            node = pending.pop()
            pending.extend(reversed(node.named_children))
            definition = node.child_by_field_name("definition")
            if node.type != "decorated_definition" or definition is None:
                continue
            if definition.type != "function_definition":
                continue
            scope = self._owner(node)
            position = node.start_byte if _statement_block(node) == scope.body else None
            for decorator in node.named_children:
                expression = _first_named(decorator) if decorator.type == "decorator" else None
                if expression is not None:
                    facts.append(self._fact(decorator, expression, definition, scope, position))
        return facts

    def _owner(self, node: Node) -> _Scope:
        parent = node.parent
        while parent is not None and parent.id not in self._scopes:
            parent = parent.parent
        return self._module if parent is None else self._scopes[parent.id]

    def _fact(
        self, decorator: Node, expression: Node, handler: Node, scope: _Scope, position: int | None
    ) -> DecoratorFact:
        call = expression if expression.type == "call" else None
        callee_node = call.child_by_field_name("function") if call is not None else expression
        callee = (
            self._evaluate(callee_node, scope, position, 0)
            if callee_node is not None
            else _Value("ambiguous")
        )
        node = callee.node
        if node is not None and _is_identity_factory(node) and not self._unreplaced(node, callee):
            callee = replace(callee, binding="ambiguous", node=None, path=None)
        arguments = _arguments(call) if call is not None else []
        path, disposition, reason = self._disposition(call, callee, arguments, scope, position)
        return DecoratorFact(
            handler_line=handler.start_point.row + 1,
            handler_col=handler.start_point.column,
            location=SourceLocation(
                file_path=self._path,
                start_line=decorator.start_point.row + 1,
                end_line=decorator.end_point.row + 1,
                start_col=decorator.start_point.column,
                end_col=decorator.end_point.column,
            ),
            consumer_path=None if callee.binding in _UNRESOLVED else callee.path,
            receiver_binding=callee.binding,
            method=callee.method,
            path=path,
            arguments=tuple(_plain(argument) for argument in arguments),
            disposition=disposition,
            reason=reason,
            member_access=callee.member_access,
        )

    def _disposition(
        self,
        call: Node | None,
        callee: _Value,
        arguments: list[Node],
        scope: _Scope,
        position: int | None,
    ) -> tuple[str | None, Disposition, str]:
        if call is None:
            return None, "non_path_decorator", "not_called"
        positional = [node for node in arguments if node.type not in _KEYWORD_ARGUMENTS]
        literal = self._literal(positional[0], scope, position, 0) if positional else None
        path = literal[0] if literal is not None and isinstance(literal[0], str) else None
        if callee.node is not None and _is_identity_factory(callee.node):
            return path, "non_path_decorator", "local_nonregistering_decorator"
        disposition, reason = _argument_shape(bool(arguments), bool(positional), literal)
        if disposition == "path_decorator" and callee.binding == "ambiguous":
            return path, "unknown", "ambiguous_binding"
        return path, disposition, reason

    def _unreplaced(self, function: Node, callee: _Value) -> bool:
        """Whether nothing visible in the file can replace a local identity decorator."""
        if self._dynamic:
            return False
        names = {_text(function.child_by_field_name("name") or function), callee.method}
        block = function.parent
        owner = block.parent if block is not None and block.type == "block" else None
        if owner is not None and owner.type == "class_definition":
            scope = self._scopes.get(owner.id)
            if scope is None or not _plain_class(owner, scope):
                return False
            names.add(_text(owner.child_by_field_name("name") or owner))
        return not names & self._stored

    def _literal(
        self, node: Node, scope: _Scope, position: int | None, depth: int
    ) -> tuple[object, bool] | None:
        """Return ``(value, via_constant)`` for literal or constant-bound arguments."""
        if node.type in _NON_STRING_LITERALS:
            return _NOT_STRING, depth > 0
        if node.type in _STRINGS:
            value = _string_value(node)
            return None if value is None else (value, depth > 0)
        if node.type != "identifier" or depth >= _MAX_DEPTH:
            return None
        binding, owner = self._lookup(_text(node), scope, position)
        if binding is None or owner is None or binding.kind != "assign":
            return None
        if not isinstance(binding.target, Node):
            return None
        start = binding.start if binding.direct else None
        return self._literal(binding.target, owner, start, depth + 1)

    def _evaluate(self, node: Node, scope: _Scope, position: int | None, depth: int) -> _Value:
        if depth >= _MAX_DEPTH:
            return _Value("ambiguous")
        if node.type == "identifier":
            return self._resolve(_text(node), scope, position, depth)
        if node.type == "parenthesized_expression" and node.named_child_count == 1:
            return self._evaluate(node.named_children[0], scope, position, depth + 1)
        inner = node.child_by_field_name("object" if node.type == "attribute" else "function")
        if node.type not in {"attribute", "call"} or inner is None:
            return _Value("ambiguous")
        value = self._evaluate(inner, scope, position, depth + 1)
        if node.type == "call":
            return _called(value)
        attribute = node.child_by_field_name("attribute")
        return self._member(value, _text(attribute) if attribute is not None else "")

    def _resolve(self, name: str, scope: _Scope, position: int | None, depth: int) -> _Value:
        binding, owner = self._lookup(name, scope, position)
        if binding is None:
            return _Value("unbound", method=name)
        target = binding.target
        if binding.kind in _IMPORTS and isinstance(target, str):
            return _Value(_IMPORTS[binding.kind], target, method=target.rsplit(".", 1)[-1])
        if binding.kind == "parameter":
            return _Value("parameter", method=name)
        if binding.kind == "definition" and isinstance(target, Node):
            node = None if _is_decorated(target) else target
            return _Value("enclosing_local", _qualname(target), node, name)
        if binding.kind == "assign" and isinstance(target, Node) and owner is not None:
            start = binding.start if binding.direct else None
            value = self._evaluate(target, owner, start, depth + 1)
            return value if value.method is not None else replace(value, method=name)
        return _Value("ambiguous", method=name)

    def _lookup(
        self, name: str, scope: _Scope, position: int | None
    ) -> tuple[_Binding | None, _Scope | None]:
        current: _Scope | None = self._module if name in scope.declared_global else scope
        if current is not scope:
            position = None
        while current is not None:
            found = current.bindings.get(name)
            if found:
                binding = _select(found, position)
                return (_OPAQUE if _star_shadows(current, binding, position) else binding), current
            if current.star_imports:
                return _OPAQUE, current
            current = _enclosing(current)
            position = None
        return None, None

    def _member(self, value: _Value, attribute: str) -> _Value:
        if value.binding in _UNRESOLVED:
            return replace(value, method=attribute, member_access=True)
        # Only a single undecorated def in a local class body proves the member.
        scope = self._scopes.get(value.node.id) if value.node is not None else None
        found = scope.bindings.get(attribute, []) if scope is not None and scope.is_class else []
        target = found[0].target if len(found) == 1 and found[0].kind == "definition" else None
        method = target if isinstance(target, Node) and not _is_decorated(target) else None
        if method is not None and method.type != "function_definition":
            method = None
        return _Value(value.binding, f"{value.path}.{attribute}", method, attribute, True)


def _argument_shape(
    has_arguments: bool, has_positional: bool, literal: tuple[object, bool] | None
) -> tuple[Disposition, str]:
    """Classify the first positional argument; route shape is only a heuristic."""
    if not has_arguments:
        return "non_path_decorator", "no_arguments"
    if not has_positional:
        return "unknown", "no_positional_argument"
    if literal is None:
        return "unknown", "nonliteral_argument"
    value, via_constant = literal
    if not isinstance(value, str) or (value and not value.startswith("/")):
        return "non_path_decorator", "non_path_argument"
    return "path_decorator", "constant_path" if via_constant else "literal_path"


def _called(value: _Value) -> _Value:
    if value.binding in _UNRESOLVED:
        return value
    binding: ReceiverBinding = (
        "call_result" if value.binding in {"import_module", "import_name"} else value.binding
    )
    node = value.node if value.node is not None and value.node.type == "class_definition" else None
    return _Value(binding, f"{value.path}()", node, value.method)


def _select(found: list[_Binding], position: int | None) -> _Binding:
    """Pick the binding visible at ``position``; competing or remote ones are opaque."""
    if any(binding.kind == "remote" for binding in found):
        return _OPAQUE
    if len(found) == 1:
        return found[0]
    if position is not None:
        prior = [binding for binding in found if binding.start < position]
        if prior and prior[-1].direct:
            return prior[-1]
    return _OPAQUE


def _star_shadows(scope: _Scope, binding: _Binding, position: int | None) -> bool:
    """Whether a star import may have replaced ``binding`` by the time it is read."""
    if not scope.star_imports or binding is _OPAQUE:
        return False
    if position is None:
        return True
    earlier = [start for start in scope.star_imports if start < position]
    if not earlier:
        return False
    return not (binding.direct and earlier[-1] < binding.start < position)


def _plain_class(definition: Node, scope: _Scope) -> bool:
    """A class whose instances and members cannot be swapped by bases or hooks."""
    if _is_decorated(definition) or any(name in scope.bindings for name in _DYNAMIC_MEMBERS):
        return False
    bases = definition.child_by_field_name("superclasses")
    named = bases.named_children if bases is not None else []
    return all(node.type == "identifier" and _text(node) == "object" for node in named)


def _attribute_writes(root: Node) -> tuple[frozenset[str], bool]:
    """Attribute names stored anywhere, and whether dynamic writes may occur."""
    stored: set[str] = set()
    dynamic = False
    pending = [root]
    while pending:
        node = pending.pop()
        pending.extend(node.named_children)
        if node.type == "identifier" and _text(node) in _DYNAMIC_NAMES:
            dynamic = True
        target = node if node.type in {"as_pattern_target", "delete_statement"} else None
        if node.type in _STORE_FIELDS:
            target = node.child_by_field_name(_STORE_FIELDS[node.type])
        for child in _walk(target) if target is not None else []:
            attribute = child.child_by_field_name("attribute")
            if child.type == "attribute" and attribute is not None:
                stored.add(_text(attribute))
    # Reassigning ``__class__`` swaps every member of an instance at once.
    return frozenset(stored), dynamic or "__class__" in stored


def _enclosing(scope: _Scope) -> _Scope | None:
    # Class bodies are not visible to code nested inside them.
    parent = scope.parent
    while parent is not None and parent.is_class:
        parent = parent.parent
    return parent


def _build_scopes(root: Node) -> dict[int, _Scope]:
    module = _Scope(root, None, is_class=False)
    scopes = {root.id: module}
    pending = [(root, module)]
    while pending:
        node, scope = pending.pop()
        for child in node.named_children:
            if child.type in _SKIPPED:
                continue
            body = child.child_by_field_name("body")
            if child.type in _SCOPES and body is not None:
                scopes[child.id] = _enter(child, body, scope)
                pending.append((body, scopes[child.id]))
                continue
            _record(child, scope)
            pending.append((child, scope))
    for scope in scopes.values():
        _hoist_declarations(scope, module)
        for found in scope.bindings.values():
            found.sort(key=lambda binding: binding.start)
    return scopes


def _enter(definition: Node, body: Node, outer: _Scope) -> _Scope:
    statement = definition.parent if _is_decorated(definition) else definition
    name = definition.child_by_field_name("name")
    if name is not None and statement is not None:
        outer.bind(_text(name), "definition", statement, definition)
    is_class = definition.type == "class_definition"
    scope = _Scope(body, outer, is_class=is_class)
    parameters = definition.child_by_field_name("parameters")
    for parameter in parameters.named_children if parameters is not None else []:
        target = parameter.child_by_field_name("name") or _first_named(parameter) or parameter
        for identifier in _identifiers(target):
            scope.bind(_text(identifier), "parameter", parameter, None)
    return scope


def _record(node: Node, scope: _Scope) -> None:
    if node.type == "import_statement":
        _record_import(node, scope)
    elif node.type == "import_from_statement":
        _record_from_import(node, scope)
    elif node.type == "assignment":
        _record_assignment(node, scope)
    elif node.type == "global_statement":
        scope.declared_global.update(_text(name) for name in node.named_children)
    elif node.type == "nonlocal_statement":
        scope.declared_nonlocal.update(_text(name) for name in node.named_children)
    else:
        _record_opaque(node, scope)


def _record_import(node: Node, scope: _Scope) -> None:
    for name in node.children_by_field_name("name"):
        alias = name.child_by_field_name("alias")
        dotted = name.child_by_field_name("name") or name
        if alias is not None:
            scope.bind(_text(alias), "import_module", node, _text(dotted))
        elif dotted.named_child_count:
            # ``import a.b`` binds ``a``.
            first = _text(dotted.named_children[0])
            scope.bind(first, "import_module", node, first)


def _record_assignment(node: Node, scope: _Scope) -> None:
    left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
    # ``a = b = value``: the nested assignment records ``b`` when visited.
    while right is not None and right.type == "assignment":
        right = right.child_by_field_name("right")
    if left is not None and left.type == "identifier" and right is not None:
        scope.bind(_text(left), "assign", node, right)
    elif left is not None:
        _bind_opaque(left, node, scope)


def _record_from_import(node: Node, scope: _Scope) -> None:
    module_name = node.child_by_field_name("module_name")
    module = _text(module_name) if module_name is not None else ""
    prefix = module if module.endswith(".") else f"{module}."
    for name in node.children_by_field_name("name"):
        alias = name.child_by_field_name("alias")
        imported = _text(name.child_by_field_name("name") or name)
        bound = _text(alias) if alias is not None else imported
        scope.bind(bound, "import_name", node, f"{prefix}{imported}")
    if any(child.type == "wildcard_import" for child in node.named_children):
        scope.star_imports.append(node.start_byte)


def _record_opaque(node: Node, scope: _Scope) -> None:
    if node.type in _OPAQUE_TARGETS:
        target = node.child_by_field_name(_OPAQUE_TARGETS[node.type])
        if target is not None:
            _bind_opaque(target, node, scope)
    elif node.type in {"as_pattern_target", "delete_statement"}:
        _bind_opaque(node, node, scope)
    elif node.type == "case_clause":
        for pattern in node.named_children:
            if pattern.type == "case_pattern":
                _bind_opaque(pattern, node, scope)


def _bind_opaque(target: Node, statement: Node, scope: _Scope) -> None:
    for identifier in _identifiers(target):
        scope.bind(_text(identifier), "opaque", statement, None)


def _hoist_declarations(scope: _Scope, module: _Scope) -> None:
    """Move ``global``/``nonlocal`` rebindings to their owner as remote writes."""
    for name in scope.declared_global | scope.declared_nonlocal:
        if not scope.bindings.pop(name, None):
            continue
        owner = module if name in scope.declared_global else _nonlocal_owner(scope, name)
        if owner is not None:
            owner.bindings.setdefault(name, []).append(_Binding("remote", -1, False))


def _nonlocal_owner(scope: _Scope, name: str) -> _Scope | None:
    owner = _enclosing(scope)
    while owner is not None and owner.parent is not None:
        if name in owner.bindings:
            return owner
        owner = _enclosing(owner)
    return None


def _is_identity_factory(function: Node) -> bool:
    """Prove a factory returns ``lambda f: f`` or a local ``def g(f): return f``."""
    if function.type != "function_definition":
        return False
    statements = _statements(function)
    returned = _returned(statements[-1]) if statements else None
    if len(statements) == 1 and returned is not None and returned.type == "lambda":
        return _returns_parameter(returned)
    if len(statements) != 2 or returned is None or statements[0].type != "function_definition":
        return False
    inner = statements[0]
    name = inner.child_by_field_name("name")
    if name is None or returned.type != "identifier" or _text(returned) != _text(name):
        return False
    body = _statements(inner)
    return len(body) == 1 and _returns_parameter(inner, _returned(body[0]))


def _returns_parameter(function: Node, returned: Node | None = None) -> bool:
    parameters = function.child_by_field_name("parameters")
    names = parameters.named_children if parameters is not None else []
    result = function.child_by_field_name("body") if returned is None else returned
    if len(names) != 1 or names[0].type != "identifier" or result is None:
        return False
    return result.type == "identifier" and _text(result) == _text(names[0])


def _statements(function: Node) -> list[Node]:
    body = function.child_by_field_name("body")
    statements = [node for node in (body.named_children if body else []) if node.type != "comment"]
    if statements and statements[0].type == "string":
        return statements[1:]
    return statements


def _returned(statement: Node) -> Node | None:
    if statement.type != "return_statement":
        return None
    return _first_named(statement)


def _arguments(call: Node) -> list[Node]:
    arguments = call.child_by_field_name("arguments")
    if arguments is None:
        return []
    if arguments.type != "argument_list":
        return [arguments]
    return [node for node in arguments.named_children if node.type != "comment"]


def _string_value(node: Node) -> str | None:
    try:
        value = ast.literal_eval(_text(node))
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return None
    return value if isinstance(value, str) else None


def _walk(root: Node) -> list[Node]:
    found: list[Node] = []
    pending = [root]
    while pending:
        node = pending.pop()
        found.append(node)
        pending.extend(node.named_children)
    return found


def _identifiers(target: Node) -> list[Node]:
    """Name targets in a binding pattern, excluding attribute and subscript stores."""
    found: list[Node] = []
    pending = [target]
    while pending:
        node = pending.pop()
        if node.type == "identifier":
            found.append(node)
        elif node.type not in {"attribute", "subscript"}:
            pending.extend(node.named_children)
    return found


def _statement_block(node: Node) -> Node | None:
    parent = node.parent
    while parent is not None and parent.type not in {"block", "module"}:
        parent = parent.parent
    return parent


def _qualname(definition: Node) -> str:
    names: list[str] = []
    node: Node | None = definition
    while node is not None:
        name = node.child_by_field_name("name") if node.type in _SCOPES else None
        if name is not None:
            names.append(_text(name))
        node = node.parent
    return ".".join(reversed(names))


def _is_decorated(definition: Node) -> bool:
    parent = definition.parent
    return parent is not None and parent.type == "decorated_definition"


def _first_named(node: Node) -> Node | None:
    return next((child for child in node.named_children if child.type != "comment"), None)


def _plain(node: Node) -> str:
    text = " ".join(_text(node).split())
    return text if len(text) <= _MAX_ARGUMENT else f"{text[: _MAX_ARGUMENT - 3]}..."


def _text(node: Node) -> str:
    return node.text.decode("utf-8", "replace") if node.text is not None else ""
