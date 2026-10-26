"""Retain callables passed to, or stored for, other code without treating them as calls.

A reference records that a callable value reaches a consumer. It does not say
the consumer invokes it, when, or with which inputs, so every observation keeps
``consumer_semantics`` and ``invocation_context`` unresolved. Consumer names are
never interpreted: ``register(h)`` and ``renamed(h)`` produce the same fact.
Resolved references add ``EdgeKind.REFERENCES`` edges from the enclosing unit
(or the module, for file-level code) and leave ``CALLS`` reachability unchanged.
Sources are re-parsed with tree-sitter and never executed.

Python
    Positions: call arguments (positional and keyword) and element values of
    list, tuple, set, and dict displays, nested to any depth. Names resolve
    through function, lambda, comprehension, and class scopes, honouring
    parameters, assignments, loop/with/except/walrus/match targets, ``del``
    and ``type`` targets, local imports, and ``global``/``nonlocal``. A walrus
    in a comprehension binds in the enclosing function, from the start of the
    comprehension. Aliases (``target = h``), imports of
    local modules (absolute, relative, and module attribute access),
    ``Class.method``, and ``self.method``/``cls.method`` inside a method are
    followed. Conditional or textually earlier rebinding, a write through
    ``global``/``nonlocal`` from another scope, and a ``from m import *``
    after the binding (or anywhere, for conditional bindings) leave the
    reference ambiguous rather than resolved. A ``del`` leaves no value.

PHP
    First-class callable syntax (``f(...)``, ``Type::m(...)``, ``$this->m(...)``)
    anywhere; array callables (``['Type', 'm']``, ``[Type::class, 'm']``,
    ``[$this, 'm']``); and quoted strings (``'f'``, ``'NS\\f'``, ``'Type::m'``) in
    argument or array-value positions. Code names use the file's namespace and
    ``use`` imports, with the global-function fallback. Quoted strings are
    fully qualified, as at runtime, and must match a declared function or
    method. A matching declaration in more than one file is ambiguous.

Unsupported, and so never resolved (some are recorded as unresolved):
    * Python: classes passed as values; methods inherited by ``self``;
      decorator return values (the decorated definition is the target);
      references stored through a variable that holds a collection
      (``routes = [h]; register(routes)`` records ``h`` at the display only);
      names provided only by ``from m import *``; ``getattr``, ``globals()``,
      ``exec``, and other computed names or writes; ``return h`` and bare
      assignments outside a collection or call.
    * PHP: ``$f(...)``, ``$obj->m(...)``, ``$var::m(...)``, and
      ``[$obj, 'm']`` (recorded as unresolved, the last only when some class
      declares ``m``); ``Closure::bind`` and anonymous classes; inherited
      methods; heredoc, nowdoc, and interpolated strings; ``use const``.
    * JavaScript and TypeScript are not analysed.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from tree_sitter import Node, Parser
from tree_sitter_language_pack import get_language

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.entrypoints import EntrypointEvidence
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import Attribute, NodeKind, NodeOrigin
from trailmark.parsers._common import make_location
from trailmark.parsers.php._syntax import parse_php

_UNRESOLVED = ("consumer_semantics", "invocation_context")
_DISPATCH = "receiver_dispatch"
_MAX_DEPTH = 8
_CALLABLE_KINDS = frozenset({NodeKind.FUNCTION, NodeKind.METHOD})
_CLASS_KINDS = frozenset({NodeKind.CLASS, NodeKind.INTERFACE, NodeKind.TRAIT, NodeKind.ENUM})
_PHP_SUFFIXES = frozenset({".php", ".phtml"})


def discover_callable_references(graph: CodeGraph) -> None:
    """Add reference edges and evidence for callables passed as values.

    Repeated discovery is idempotent. No entrypoints or tags are added.

    Args:
        graph: Parsed graph whose module nodes name readable source files.
    """
    index = _GraphIndex(graph)
    python = _PythonProject(index)
    php: _PhpSymbols | None = None
    for module_id, path in sorted(index.modules.items()):
        suffix = Path(path).suffix.lower()
        if suffix == ".py":
            module = python.module(module_id)
            if module is not None:
                module.discover()
        elif suffix in _PHP_SUFFIXES:
            root = _parse(path, "php")
            php = php or _PhpSymbols(index)
            if root is not None:
                _PhpFile(php, module_id, path, root).discover()


@dataclass(frozen=True)
class _Target:
    """A graph node, a Python module path, or a callable without a node.

    A default instance is an opaque value: bound, but not known to be callable.
    """

    node_id: str | None = None
    module: str | None = None
    callable: bool = False
    dispatch: bool = False


_OPAQUE = _Target()
_UNKNOWN_CALLABLE = _Target(callable=True)


@dataclass(frozen=True)
class _Occurrence:
    language: str
    node: Node
    path: str
    source_id: str
    details: tuple[Attribute, ...]


def _parse(path: str, language: str) -> Node | None:
    try:
        source = Path(path).read_bytes()
    except OSError:
        return None
    parser = Parser(get_language(language))
    tree = parse_php(parser, source) if language == "php" else parser.parse(source)
    return tree.root_node


def _text(node: Node | None) -> str:
    return (node.text or b"").decode("utf-8", errors="replace") if node is not None else ""


def _walk(root: Node) -> Iterator[Node]:
    pending = [root]
    while pending:
        node = pending.pop()
        yield node
        pending.extend(reversed(node.named_children))


def _is_field(parent: Node, name: str, child: Node) -> bool:
    value = parent.child_by_field_name(name)
    return value is not None and value.id == child.id


def _position(parent: Node, child: Node) -> int:
    siblings = [c for c in parent.named_children if c.type != "comment"]
    return next((i for i, c in enumerate(siblings) if c.id == child.id), 0)


def _context(
    node: Node,
    step: Callable[[Node, Node], str | None],
    consumer: Callable[[Node, Node], tuple[str, str, str]],
) -> tuple[str, str, str]:
    """Climb through containers to the consumer: ``(kind, consumer, path)``."""
    path = ""
    child = node
    while (parent := child.parent) is not None:
        segment = step(parent, child)
        if segment is None:
            kind, name, segment = consumer(parent, child)
            return kind, name, segment + path
        path = segment + path
        child = parent
    return "module", "", path


def _details(node: Node, form: str, context: tuple[str, str, str]) -> tuple[Attribute, ...]:
    kind, consumer, path = context
    return (
        ("consumer_kind", kind),
        ("consumer", consumer),
        ("path", path),
        ("expression", _text(node)),
        ("form", form),
    )


class _GraphIndex:
    """Graph lookups shared by languages, and idempotent recording."""

    def __init__(self, graph: CodeGraph) -> None:
        self.graph = graph
        self.edges = set(graph.edges)
        self.modules = {
            unit.id: unit.location.file_path
            for unit in graph.nodes.values()
            if unit.kind == NodeKind.MODULE
        }
        located: dict[tuple[str, int, int | None], list[str]] = {}
        for unit in graph.nodes.values():
            if unit.origin == NodeOrigin.SOURCE and unit.kind in _CALLABLE_KINDS | _CLASS_KINDS:
                key = (unit.location.file_path, unit.location.start_line, unit.location.start_col)
                located.setdefault(key, []).append(unit.id)
        self.located = {key: ids[0] for key, ids in located.items() if len(ids) == 1}
        self.children: dict[str, list[str]] = {}
        self.container: dict[str, str] = {}
        self.bases: dict[str, list[str]] = {}
        for edge in graph.edges:
            if edge.kind == EdgeKind.CONTAINS and edge.target_id in graph.nodes:
                self.children.setdefault(edge.source_id, []).append(edge.target_id)
                self.container[edge.target_id] = edge.source_id
            elif edge.kind == EdgeKind.INHERITS and edge.target_id in graph.nodes:
                self.bases.setdefault(edge.source_id, []).append(edge.target_id)

    def unit_at(self, path: str, syntax: Node) -> str | None:
        return self.located.get((path, syntax.start_point.row + 1, syntax.start_point.column))

    def kind(self, node_id: str | None) -> NodeKind | None:
        unit = self.graph.nodes.get(node_id) if node_id is not None else None
        return unit.kind if unit is not None else None

    def is_callable(self, node_id: str | None) -> bool:
        return self.kind(node_id) in _CALLABLE_KINDS

    def members(self, container: str, name: str, *, fold: bool = False) -> list[str]:
        """Return callables declared directly in ``container`` under ``name``."""
        wanted = name.lower() if fold else name
        result = []
        for child in self.children.get(container, []):
            unit = self.graph.nodes[child]
            actual = unit.name.lower() if fold else unit.name
            if actual == wanted and unit.kind in _CALLABLE_KINDS:
                result.append(child)
        return result

    def owner(self, node: Node, path: str, module_id: str, types: frozenset[str]) -> str:
        """Return the unit whose body contains ``node``, or the module."""
        child = node
        while (parent := child.parent) is not None:
            if parent.type in types and _is_field(parent, "body", child):
                unit = self.unit_at(path, parent)
                if unit is not None:
                    return unit
            child = parent
        return module_id

    def record(self, occurrence: _Occurrence, targets: set[_Target]) -> None:
        """Store one observation per possible target; add an edge only when resolved."""
        nodes = sorted({t.node_id for t in targets if t.node_id and self.is_callable(t.node_id)})
        if not nodes and not any(t.callable for t in targets):
            return
        location = make_location(occurrence.node, occurrence.path)
        dispatch = any(t.dispatch for t in targets)
        binding = "ambiguous" if nodes else "unresolved"
        if len(targets) == 1 and nodes:
            binding = "resolved"
            self._edge(occurrence, nodes[0], dispatch)
        for node_id in nodes or [None]:
            self.graph.add_entrypoint_evidence(
                EntrypointEvidence(
                    node_id=node_id,
                    basis="reference",
                    rule=f"{occurrence.language}.callable_reference",
                    location=location,
                    source_id=occurrence.source_id,
                    symbol=_text(occurrence.node),
                    binding=binding,
                    details=occurrence.details,
                    unresolved=(*_UNRESOLVED, _DISPATCH) if dispatch else _UNRESOLVED,
                )
            )

    def _edge(self, occurrence: _Occurrence, target: str, dispatch: bool) -> None:
        local = self.graph.nodes[target].location.file_path == occurrence.path
        edge = CodeEdge(
            source_id=occurrence.source_id,
            target_id=target,
            kind=EdgeKind.REFERENCES,
            confidence=EdgeConfidence.CERTAIN
            if local and not dispatch
            else EdgeConfidence.INFERRED,
            location=make_location(occurrence.node, occurrence.path),
            attributes=occurrence.details,
        )
        if edge not in self.edges:
            self.edges.add(edge)
            self.graph.edges.append(edge)


# --------------------------------------------------------------------------- Python

# Scope node -> whether its body runs later than the enclosing statement.
_PY_SCOPES = {
    "function_definition": True,
    "lambda": True,
    "generator_expression": True,
    "class_definition": False,
    "list_comprehension": False,
    "set_comprehension": False,
    "dictionary_comprehension": False,
}
_PY_BODY_SCOPES = frozenset({"function_definition", "class_definition", "lambda"})
_PY_COMPREHENSIONS = frozenset(
    {"list_comprehension", "set_comprehension", "dictionary_comprehension"}
    | {"generator_expression"}
)
# Syntax that can hold a value passed to a call or stored in a display.
_PY_VALUE_PARENTS = frozenset(
    {
        "argument_list",
        "keyword_argument",
        "list",
        "tuple",
        "set",
        "expression_list",
        "pair",
        "parenthesized_expression",
    }
)
_PY_OWNERS = frozenset({"function_definition", "class_definition"})
_PY_CONDITIONAL = frozenset(
    {
        "if_statement",
        "elif_clause",
        "else_clause",
        "for_statement",
        "while_statement",
        "try_statement",
        "except_clause",
        "with_statement",
        "match_statement",
        "case_clause",
        "conditional_expression",
        "boolean_operator",
    }
    # A comprehension may run its clauses zero times.
    | _PY_COMPREHENSIONS
)
_PY_TARGET_CONTAINERS = frozenset(
    {
        "pattern_list",
        "tuple_pattern",
        "list_pattern",
        "tuple",
        "list",
        "list_splat_pattern",
        "list_splat",
        "parenthesized_expression",
        "expression_list",
        "as_pattern_target",
    }
)


@dataclass
class _PyScope:
    node: Node
    parent: _PyScope | None = None
    bindings: dict[str, list[tuple[int, Node]]] = field(default_factory=dict)
    globals: set[str] = field(default_factory=set)
    nonlocals: set[str] = field(default_factory=set)
    # ``from m import *`` points, and names written here from inner scopes.
    stars: list[int] = field(default_factory=list)
    remote: set[str] = field(default_factory=set)

    def bind(self, name: str, point: int, site: Node) -> None:
        self.bindings.setdefault(name, []).append((point, site))


def _target_names(node: Node | None) -> list[str]:
    """Names stored by an assignment-like target; attributes and subscripts bind none."""
    names = []
    pending = [node] if node is not None else []
    while pending:
        current = pending.pop()
        if current.type == "identifier":
            names.append(_text(current))
        elif current.type in _PY_TARGET_CONTAINERS:
            pending.extend(current.named_children)
    return names


def _bind_definition(node: Node) -> list[tuple[str, int]]:
    return [(_text(node.child_by_field_name("name")), node.end_byte)]


def _bind_assignment(node: Node) -> list[tuple[str, int]]:
    return [(n, node.end_byte) for n in _target_names(node.child_by_field_name("left"))]


def _bind_loop(node: Node) -> list[tuple[str, int]]:
    left = node.child_by_field_name("left")
    return [(n, left.end_byte) for n in _target_names(left)] if left is not None else []


def _bind_alias(node: Node) -> list[tuple[str, int]]:
    return [(n, node.end_byte) for n in _target_names(node)]


def _bind_pattern(node: Node) -> list[tuple[str, int]]:
    # Over-approximate captures: an extra local only prevents resolution.
    return [(_text(n), node.end_byte) for n in _walk(node) if n.type == "identifier"]


def _bind_delete(node: Node) -> list[tuple[str, int]]:
    return [(n, node.end_byte) for target in node.named_children for n in _target_names(target)]


def _bind_type_alias(node: Node) -> list[tuple[str, int]]:
    left = node.child_by_field_name("left")
    return _bind_pattern(left) if left is not None else []


def _bind_import(node: Node) -> list[tuple[str, int]]:
    names = []
    for imported in node.children_by_field_name("name"):
        alias = imported.child_by_field_name("alias")
        if alias is not None:
            names.append(_text(alias))
        elif node.type == "import_statement":
            names.append(_text(imported).split(".", 1)[0])
        else:
            names.append(_text(imported))
    return [(name, node.end_byte) for name in names]


_PY_BINDERS: dict[str, Callable[[Node], list[tuple[str, int]]]] = {
    "function_definition": _bind_definition,
    "class_definition": _bind_definition,
    "named_expression": _bind_definition,
    "assignment": _bind_assignment,
    "augmented_assignment": _bind_assignment,
    "for_statement": _bind_loop,
    "for_in_clause": _bind_loop,
    "as_pattern_target": _bind_alias,
    "case_pattern": _bind_pattern,
    "delete_statement": _bind_delete,
    "type_alias_statement": _bind_type_alias,
    "import_statement": _bind_import,
    "import_from_statement": _bind_import,
}


def _parameter_names(node: Node) -> list[str]:
    parameters = node.child_by_field_name("parameters")
    names = []
    for child in parameters.named_children if parameters is not None else []:
        name = child if child.type == "identifier" else child.child_by_field_name("name")
        if name is None:
            name = next((c for c in child.named_children if c.type == "identifier"), None)
        if name is not None:
            names.append(_text(name))
    return names


def _inside_scope(scope: Node, child: Node) -> bool:
    return scope.type not in _PY_BODY_SCOPES or _is_field(scope, "body", child)


def _python_step(parent: Node, child: Node) -> str | None:
    if parent.type in {"parenthesized_expression", "dictionary"}:
        return ""
    if parent.type in {"list", "tuple", "set", "expression_list"}:
        return f"[{_position(parent, child)}]"
    if parent.type == "pair" and _is_field(parent, "value", child):
        return f"[{_text(parent.child_by_field_name('key'))}]"
    return None


def _python_call(arguments: Node | None) -> str | None:
    call = arguments.parent if arguments is not None else None
    if arguments is None or arguments.type != "argument_list" or call is None:
        return None
    return _text(call.child_by_field_name("function")) if call.type == "call" else None


def _python_consumer(parent: Node, child: Node) -> tuple[str, str, str]:
    callee = _python_call(parent)
    if callee is not None:
        return "call", callee, f"args[{_position(parent, child)}]"
    callee = _python_call(parent.parent)
    if parent.type == "keyword_argument" and _is_field(parent, "value", child) and callee:
        name = _text(parent.child_by_field_name("name"))
        return "call", callee, f"kwargs[{name}]"
    if parent.type == "assignment" and _is_field(parent, "right", child):
        return "assignment", _text(parent.child_by_field_name("left")), ""
    return parent.type, "", ""


def _dotted(expr: Node) -> list[Node] | None:
    parts = []
    while expr.type == "attribute":
        attribute = expr.child_by_field_name("attribute")
        obj = expr.child_by_field_name("object")
        if attribute is None or obj is None:
            return None
        parts.append(attribute)
        expr = obj
    if expr.type != "identifier":
        return None
    parts.append(expr)
    return parts[::-1]


def _join(*parts: str) -> str:
    return ".".join(part for part in parts if part)


def _without_root(identifier: str) -> str:
    """Remove the identity prefix assigned to explicitly selected source roots."""
    return identifier.split("::", 1)[-1]


def _python_import_name(module_id: str, path: str) -> str:
    """Undo filename expansion used to distinguish colliding polyglot modules."""
    if not module_id.endswith(r"\.py"):
        return module_id
    name = module_id[:-4]
    if Path(path).name == "__init__.py":
        prefix, separator, local = name.rpartition("::")
        package = local.rpartition(".")[0] or Path(path).parent.name
        return prefix + separator + package
    return name


class _PythonProject:
    """Python modules in the graph, parsed on demand for cross-module bindings."""

    def __init__(self, index: _GraphIndex) -> None:
        self.index = index
        self.paths = {m: p for m, p in index.modules.items() if Path(p).suffix.lower() == ".py"}
        self.names = {m: _python_import_name(m, p) for m, p in self.paths.items()}
        self._modules: dict[str, _PythonModule | None] = {}
        self._matches: dict[str, list[str]] = {}

    def module(self, module_id: str) -> _PythonModule | None:
        if module_id not in self._modules:
            path = self.paths.get(module_id)
            root = _parse(path, "python") if path is not None else None
            self._modules[module_id] = (
                _PythonModule(self, module_id, path, root)
                if path is not None and root is not None
                else None
            )
        return self._modules[module_id]

    def match(self, dotted: str) -> list[str]:
        """Return graph modules for an import path; a root-relative suffix also matches."""
        if dotted not in self._matches:
            exact = [
                m
                for m, name in self.names.items()
                if name == dotted or _without_root(name) == dotted
            ]
            suffix = "." + dotted
            self._matches[dotted] = exact or sorted(
                m for m, name in self.names.items() if _without_root(name).endswith(suffix)
            )
        return self._matches[dotted] if dotted else []

    def member(self, target: _Target, name: str, depth: int) -> set[_Target]:
        if target.module is not None:
            return self.module_member(target.module, name, depth)
        if target.node_id is not None and self.index.kind(target.node_id) in _CLASS_KINDS:
            methods = self.index.members(target.node_id, name)
            return {_Target(node_id=m, callable=True) for m in methods} or {_OPAQUE}
        return {_OPAQUE}

    def module_member(self, dotted: str, name: str, depth: int) -> set[_Target]:
        modules = self.match(dotted)
        result: set[_Target] = {_OPAQUE} if len(modules) > 1 else set()
        for module_id in modules:
            module = self.module(module_id)
            if module is not None and name in module.root_scope.bindings:
                result |= module.select(module.root_scope, name, None, depth + 1)
        if result - {_OPAQUE}:
            return result
        if self.match(_join(dotted, name)):
            return {_Target(module=_join(dotted, name))}
        return {_OPAQUE}


class _PythonModule:
    """Lexical bindings of one Python file."""

    def __init__(self, project: _PythonProject, module_id: str, path: str, root: Node) -> None:
        self.project = project
        self.index = project.index
        self.module_id = module_id
        self.path = path
        self.root = root
        self.root_scope = _PyScope(root)
        self.scopes = {root.id: self.root_scope}
        self._build()

    def _build(self) -> None:
        pending = [(self.root, self.root_scope)]
        while pending:
            node, scope = pending.pop()
            self._bind_node(node, scope)
            inner = scope
            if node.type in _PY_SCOPES:
                inner = self.scopes.setdefault(node.id, _PyScope(node, scope))
                self._bind_parameters(node, inner)
            for child in reversed(node.named_children):
                pending.append((child, inner if _inside_scope(node, child) else scope))
        for scope in self.scopes.values():
            self._mark_remote(scope)
            for sites in scope.bindings.values():
                sites.sort(key=lambda site: site[0])

    @staticmethod
    def _bind_node(node: Node, scope: _PyScope) -> None:
        binder = _PY_BINDERS.get(node.type)
        owner, start = scope, None
        # A comprehension's walrus binds in the enclosing function. Clauses run
        # before the element, so it is visible from the comprehension's start.
        while node.type == "named_expression" and owner.node.type in _PY_COMPREHENSIONS:
            if owner.parent is None:
                break
            owner, start = owner.parent, owner.node.start_byte
        for name, point in binder(node) if binder is not None else []:
            owner.bind(name, point if start is None else start, node)
        if node.type in {"global_statement", "nonlocal_statement"}:
            names = {_text(n) for n in node.named_children}
            (scope.globals if node.type == "global_statement" else scope.nonlocals).update(names)
        if node.type == "import_from_statement" and any(
            child.type == "wildcard_import" for child in node.named_children
        ):
            scope.stars.append(node.end_byte)

    def _mark_remote(self, scope: _PyScope) -> None:
        """Record names this scope writes in another through ``global``/``nonlocal``."""
        if scope is not self.root_scope:
            self.root_scope.remote.update(scope.globals & scope.bindings.keys())
        for name in scope.nonlocals & scope.bindings.keys():
            owner = scope.parent
            while owner is not None and owner is not self.root_scope:
                binds = name in owner.bindings and name not in owner.nonlocals
                if binds and owner.node.type != "class_definition":
                    owner.remote.add(name)
                    break
                owner = owner.parent

    @staticmethod
    def _bind_parameters(node: Node, scope: _PyScope) -> None:
        """Parameter sites are opaque values bound before the body runs."""
        parameters = node.child_by_field_name("parameters")
        if parameters is not None:
            for name in _parameter_names(node):
                scope.bind(name, node.start_byte, parameters)

    def discover(self) -> None:
        for node in _walk(self.root):
            parent = node.parent
            if node.type not in {"identifier", "attribute"} or parent is None:
                continue
            if parent.type not in _PY_VALUE_PARENTS:
                continue
            context = _context(node, _python_step, _python_consumer)
            if not context[2]:
                continue
            form = "name" if node.type == "identifier" else "attribute"
            source = self.index.owner(node, self.path, self.module_id, _PY_OWNERS)
            occurrence = _Occurrence(
                "python", node, self.path, source, _details(node, form, context)
            )
            self.index.record(occurrence, self.targets(node, 0))

    def _chain(self, node: Node) -> list[_PyScope]:
        chain = []
        child = node
        while (parent := child.parent) is not None:
            scope = self.scopes.get(parent.id)
            if scope is not None and _inside_scope(parent, child):
                chain.append(scope)
            child = parent
        return chain

    def lookup(self, name: str, occurrence: Node, depth: int) -> set[_Target]:
        """Resolve ``name`` as Python would at ``occurrence``."""
        immediate = True
        for position, scope in enumerate(self._chain(occurrence)):
            if position and scope.node.type == "class_definition":
                continue
            if name in scope.globals:
                return self.select(self.root_scope, name, None, depth)
            if name in scope.bindings and name not in scope.nonlocals:
                limit = occurrence.start_byte if immediate else None
                return self.select(scope, name, limit, depth)
            immediate = immediate and not _PY_SCOPES.get(scope.node.type, False)
        return {_OPAQUE}

    def select(self, scope: _PyScope, name: str, limit: int | None, depth: int) -> set[_Target]:
        """Use the last binding when definite; otherwise every binding is a candidate."""
        sites = scope.bindings.get(name, [])
        if limit is not None:
            sites = [site for site in sites if site[0] <= limit]
        if not sites:
            return {_OPAQUE}
        definite = self._definite(sites[-1][1], scope.node)
        if definite:
            sites = sites[-1:]
        result: set[_Target] = set()
        for _, site in sites:
            result |= self._site_targets(site, name, depth)
        # A later star import, or a write from another scope, may replace the value.
        stars = [point for point in scope.stars if limit is None or point <= limit]
        if name in scope.remote or (stars and (not definite or stars[-1] > sites[-1][0])):
            result.add(_OPAQUE)
        return result

    @staticmethod
    def _definite(site: Node, scope: Node) -> bool:
        current = site.parent
        while current is not None and current.id != scope.id:
            if current.type in _PY_CONDITIONAL:
                return False
            current = current.parent
        return True

    def _site_targets(self, site: Node, name: str, depth: int) -> set[_Target]:
        if depth > _MAX_DEPTH:
            return {_OPAQUE}
        if site.type in {"function_definition", "class_definition"}:
            return {self._definition(site)}
        if site.type == "assignment":
            return self._alias(site, depth)
        if site.type == "import_from_statement":
            return self._from_import(site, name, depth)
        if site.type == "import_statement":
            return self._module_import(site, name)
        return {_OPAQUE}

    def _definition(self, site: Node) -> _Target:
        """A definition without a graph unit is still a callable, but unresolved."""
        unit = self.index.unit_at(self.path, site)
        if unit is None:
            return _UNKNOWN_CALLABLE if site.type == "function_definition" else _OPAQUE
        return _Target(node_id=unit, callable=self.index.is_callable(unit))

    def _alias(self, site: Node, depth: int) -> set[_Target]:
        left = site.child_by_field_name("left")
        value = site.child_by_field_name("right")
        while value is not None and value.type == "assignment":
            value = value.child_by_field_name("right")
        if left is None or left.type != "identifier" or value is None:
            return {_OPAQUE}
        return self.targets(value, depth + 1)

    def _module_import(self, site: Node, name: str) -> set[_Target]:
        for imported in site.children_by_field_name("name"):
            alias = imported.child_by_field_name("alias")
            if alias is not None and _text(alias) == name:
                return {_Target(module=_text(imported.child_by_field_name("name")))}
            if alias is None and _text(imported).split(".", 1)[0] == name:
                return {_Target(module=name)}
        return {_OPAQUE}

    def _from_import(self, site: Node, name: str, depth: int) -> set[_Target]:
        base = self._import_base(site.child_by_field_name("module_name"))
        for imported in site.children_by_field_name("name"):
            alias = imported.child_by_field_name("alias")
            original = imported.child_by_field_name("name") if alias is not None else imported
            if _text(alias if alias is not None else imported) == name and base is not None:
                return self.project.module_member(base, _text(original), depth)
        return {_OPAQUE}

    def _import_base(self, module: Node | None) -> str | None:
        if module is None or module.type != "relative_import":
            return _text(module) if module is not None else None
        prefix = next((c for c in module.named_children if c.type == "import_prefix"), None)
        rest = next((c for c in module.named_children if c.type == "dotted_name"), None)
        level = _text(prefix).count(".")
        parts = self.project.names[self.module_id].split(".")
        drop = level - 1 if Path(self.path).name == "__init__.py" else level
        if drop > len(parts):
            return None
        return _join(*parts[: len(parts) - drop], _text(rest))

    def targets(self, expr: Node, depth: int) -> set[_Target]:
        """Resolve a name or attribute chain to its possible values."""
        parts = _dotted(expr) if expr.type in {"identifier", "attribute"} else None
        if parts is None or depth > _MAX_DEPTH:
            return {_OPAQUE}
        receiver = self._receiver(parts) if len(parts) == 2 else None
        if receiver is not None:
            return receiver
        current = self.lookup(_text(parts[0]), parts[0], depth)
        for part in parts[1:]:
            current = set().union(
                *(self.project.member(target, _text(part), depth + 1) for target in current)
            )
        return current

    def _receiver(self, parts: list[Node]) -> set[_Target] | None:
        """Bind ``self.name`` in a method to the enclosing class's own method."""
        head, attribute = parts
        function, class_id = self._method_of(head)
        if function is None or class_id is None:
            return None
        names = _parameter_names(function)
        local = self.scopes[function.id].bindings.get(_text(head), [])
        if not names or names[0] != _text(head) or len(local) != 1:
            return None
        methods = self.index.members(class_id, _text(attribute))
        return {_Target(node_id=m, callable=True, dispatch=True) for m in methods} or {_OPAQUE}

    def _method_of(self, node: Node) -> tuple[Node | None, str | None]:
        current = node.parent
        while current is not None and current.type != "function_definition":
            current = current.parent
        if current is None:
            return None, None
        owner = current.parent
        if owner is not None and owner.type == "decorated_definition":
            if any(_text(d).strip() == "@staticmethod" for d in owner.named_children):
                return None, None
            owner = owner.parent
        cls = owner.parent if owner is not None and owner.type == "block" else None
        if cls is None or cls.type != "class_definition":
            return None, None
        return current, self.index.unit_at(self.path, cls)


# --------------------------------------------------------------------------- PHP

_PHP_OWNERS = frozenset(
    {
        "function_definition",
        "method_declaration",
        "anonymous_function",
        "arrow_function",
        "class_declaration",
        "interface_declaration",
        "trait_declaration",
        "enum_declaration",
    }
)
_PHP_CLASSES = frozenset(
    {"class_declaration", "interface_declaration", "trait_declaration", "enum_declaration"}
)
_PHP_CALLS = frozenset(
    {
        "function_call_expression",
        "member_call_expression",
        "nullsafe_member_call_expression",
        "scoped_call_expression",
    }
)
_PHP_IDENTIFIER = r"[^\W\d]\w*"
_PHP_NAME = re.compile(rf"\\?{_PHP_IDENTIFIER}(?:\\{_PHP_IDENTIFIER})*(?:::{_PHP_IDENTIFIER})?")


@dataclass
class _PhpNamespace:
    start: int
    name: str
    classes: dict[str, str] = field(default_factory=dict)
    functions: dict[str, str] = field(default_factory=dict)


def _php_key(qualified: str) -> str:
    return qualified.lstrip("\\").replace("\\", ".").lower()


def _php_qualify(namespace: str, name: str) -> str:
    return f"{namespace}\\{name}" if namespace else name


def _php_imports(statement: Node, namespace: _PhpNamespace) -> None:
    if statement.type != "namespace_use_declaration":
        return
    if any(child.type == "const" for child in statement.children):
        return
    functions = any(child.type == "function" for child in statement.children)
    table = namespace.functions if functions else namespace.classes
    group = statement.child_by_field_name("body")
    prefix = next((c for c in statement.named_children if c.type == "namespace_name"), None)
    stem = _text(prefix) + "\\" if group is not None and prefix is not None else ""
    clauses = (group if group is not None else statement).named_children
    for clause in (c for c in clauses if c.type == "namespace_use_clause"):
        alias = clause.child_by_field_name("alias")
        name = next((c for c in clause.named_children if alias is None or c.id != alias.id), None)
        if name is None:
            continue
        qualified = stem + _text(name).lstrip("\\")
        local = _text(alias) if alias is not None else qualified.rsplit("\\", 1)[-1]
        table[local.lower()] = qualified


def _php_namespaces(root: Node) -> list[_PhpNamespace]:
    """Namespace and ``use`` tables in effect from each starting byte."""
    namespaces = [_PhpNamespace(0, "")]
    for statement in root.named_children:
        if statement.type != "namespace_definition":
            _php_imports(statement, namespaces[-1])
            continue
        namespaces.append(
            _PhpNamespace(statement.start_byte, _text(statement.child_by_field_name("name")))
        )
        body = statement.child_by_field_name("body")
        if body is not None:
            for inner in body.named_children:
                _php_imports(inner, namespaces[-1])
            namespaces.append(_PhpNamespace(statement.end_byte, ""))
    return namespaces


def _php_literal(node: Node) -> str | None:
    """Decode a plain quoted string; interpolation and other escapes are not names."""
    if node.type == "string":
        return _text(node)[1:-1].replace("\\\\", "\\").replace("\\'", "'")
    if node.type != "encapsed_string":
        return None
    parts = []
    for child in node.named_children:
        if child.type == "string_content":
            parts.append(_text(child))
        elif child.type == "escape_sequence" and _text(child) == "\\\\":
            parts.append("\\")
        else:
            return None
    return "".join(parts)


def _php_callee(call: Node) -> str:
    name = _text(call.child_by_field_name("name"))
    if call.type in {"member_call_expression", "nullsafe_member_call_expression"}:
        return f"{_text(call.child_by_field_name('object'))}->{name}"
    if call.type == "scoped_call_expression":
        return f"{_text(call.child_by_field_name('scope'))}::{name}"
    if call.type == "object_creation_expression":
        named = call.named_children
        return "new " + _text(named[0]) if named else "new"
    return _text(call.child_by_field_name("function"))


def _php_step(parent: Node, child: Node) -> str | None:
    if parent.type in {"parenthesized_expression", "array_creation_expression"}:
        return ""
    if parent.type != "array_element_initializer" or parent.parent is None:
        return None
    named = parent.named_children
    if len(named) == 2 and named[0].id == child.id:
        return None
    key = _text(named[0]) if len(named) == 2 else str(_position(parent.parent, parent))
    return f"[{key}]"


def _php_consumer(parent: Node, child: Node) -> tuple[str, str, str]:
    arguments = parent.parent
    call = arguments.parent if arguments is not None else None
    if parent.type == "argument" and arguments is not None and call is not None:
        name = parent.child_by_field_name("name")
        key = _text(name) if name is not None else str(_position(arguments, parent))
        return "call", _php_callee(call), f"args[{key}]"
    if parent.type == "assignment_expression" and _is_field(parent, "right", child):
        return "assignment", _text(parent.child_by_field_name("left")), ""
    return parent.type, "", ""


def _acquisition(node: Node) -> bool:
    arguments = node.child_by_field_name("arguments")
    return arguments is not None and any(
        child.type == "variadic_placeholder" for child in arguments.named_children
    )


class _PhpSymbols:
    """Namespace-qualified PHP functions and classes from every PHP file in the graph."""

    def __init__(self, index: _GraphIndex) -> None:
        self.index = index
        self.functions: dict[str, list[str]] = {}
        self.classes: dict[str, list[str]] = {}
        self.method_names: set[str] = set()
        nodes = index.graph.nodes
        for unit in nodes.values():
            if Path(unit.location.file_path).suffix.lower() not in _PHP_SUFFIXES:
                continue
            if unit.kind == NodeKind.METHOD:
                self.method_names.add(unit.name.lower())
            container = nodes.get(index.container.get(unit.id, ""))
            if container is not None and container.kind not in {
                NodeKind.MODULE,
                NodeKind.NAMESPACE,
            }:
                continue
            key = _php_key(_without_root(unit.id).split(":", 1)[-1])
            if unit.kind == NodeKind.FUNCTION:
                self.functions.setdefault(key, []).append(unit.id)
            elif unit.kind in _CLASS_KINDS:
                self.classes.setdefault(key, []).append(unit.id)


class _PhpFile:
    """Resolve PHP callable values with the file's namespace and imports."""

    def __init__(self, symbols: _PhpSymbols, module_id: str, path: str, root: Node) -> None:
        self.index = symbols.index
        self.functions = symbols.functions
        self.classes = symbols.classes
        self.method_names = symbols.method_names
        self.module_id = module_id
        self.path = path
        self.root = root
        self.namespaces = _php_namespaces(root)
        self.starts = [namespace.start for namespace in self.namespaces]

    def discover(self) -> None:
        consumed: set[int] = set()
        for node in _walk(self.root):
            if node.type in _PHP_CALLS and _acquisition(node):
                self._record(node, "first_class_callable", self._acquired(node))
            elif node.type == "array_creation_expression":
                targets = self._array(node)
                if targets is not None:
                    consumed.update(child.id for child in _walk(node))
                    self._record(node, "array", targets)
            elif node.type in {"string", "encapsed_string"} and node.id not in consumed:
                self._string(node)

    def _record(
        self,
        node: Node,
        form: str,
        targets: set[_Target],
        context: tuple[str, str, str] | None = None,
    ) -> None:
        context = context or _context(node, _php_step, _php_consumer)
        source = self.index.owner(node, self.path, self.module_id, _PHP_OWNERS)
        occurrence = _Occurrence("php", node, self.path, source, _details(node, form, context))
        self.index.record(occurrence, targets)

    def _string(self, node: Node) -> None:
        """Quoted names are fully qualified at runtime; only declared targets count."""
        value = _php_literal(node)
        if value is None or not _PHP_NAME.fullmatch(value):
            return
        context = _context(node, _php_step, _php_consumer)
        if not context[2]:
            return
        name, _, method = value.lstrip("\\").partition("::")
        if method:
            targets = self._methods(self.classes.get(_php_key(name), []), method, dispatch=False)
        else:
            ids = self.functions.get(_php_key(name), [])
            targets = {_Target(node_id=i, callable=True) for i in ids}
        if targets:
            self._record(node, "string", targets, context)

    def _namespace(self, node: Node) -> _PhpNamespace:
        return self.namespaces[bisect_right(self.starts, node.start_byte) - 1]

    def _class_name(self, name: str, node: Node) -> str:
        namespace = self._namespace(node)
        if name.startswith("\\"):
            return name[1:]
        head, separator, rest = name.partition("\\")
        if head.lower() in namespace.classes:
            return namespace.classes[head.lower()] + separator + rest
        if head.lower() == "namespace" and separator:
            return _php_qualify(namespace.name, rest)
        return _php_qualify(namespace.name, name)

    def _function_ids(self, name: str, node: Node) -> list[str]:
        namespace = self._namespace(node)
        if name.startswith("\\") or "\\" in name:
            return self.functions.get(_php_key(self._class_name(name, node)), [])
        if name.lower() in namespace.functions:
            return self.functions.get(_php_key(namespace.functions[name.lower()]), [])
        local = self.functions.get(_php_key(_php_qualify(namespace.name, name)), [])
        return local or self.functions.get(_php_key(name), [])

    def _methods(self, classes: list[str], method: str, *, dispatch: bool) -> set[_Target]:
        return {
            _Target(node_id=m, callable=True, dispatch=dispatch)
            for class_id in classes
            for m in self.index.members(class_id, method, fold=True)
        }

    def _enclosing_class(self, node: Node) -> str | None:
        current = node.parent
        while current is not None and current.type != "anonymous_class":
            if current.type in _PHP_CLASSES:
                return self.index.unit_at(self.path, current)
            current = current.parent
        return None

    def _scope_classes(self, scope: Node, node: Node) -> tuple[list[str], bool] | None:
        """Classes named by a scope (``Type``, ``self``, ``static``, ``parent``)."""
        if scope.type in {"name", "qualified_name"}:
            return self.classes.get(_php_key(self._class_name(_text(scope), node)), []), False
        if scope.type != "relative_scope":
            return None
        enclosing = self._enclosing_class(node)
        if enclosing is None:
            return [], False
        word = _text(scope).lower()
        classes = self.index.bases.get(enclosing, []) if word == "parent" else [enclosing]
        return classes, word == "static"

    def _acquired(self, node: Node) -> set[_Target]:
        name = _text(node.child_by_field_name("name"))
        if node.type == "function_call_expression":
            function = node.child_by_field_name("function")
            if function is None or function.type not in {"name", "qualified_name"}:
                return {_UNKNOWN_CALLABLE}
            ids = self._function_ids(_text(function), node)
            return {_Target(node_id=i, callable=True) for i in ids} or {_UNKNOWN_CALLABLE}
        if node.type == "scoped_call_expression":
            scope = node.child_by_field_name("scope")
            found = self._scope_classes(scope, node) if scope is not None else None
        else:
            receiver = _text(node.child_by_field_name("object"))
            enclosing = self._enclosing_class(node) if receiver == "$this" else None
            found = ([enclosing], True) if enclosing is not None else None
        if found is None:
            return {_Target(callable=True, dispatch=True)}
        return self._methods(found[0], name, dispatch=found[1]) or {_UNKNOWN_CALLABLE}

    def _array(self, node: Node) -> set[_Target] | None:
        """Recognize ``[receiver, 'method']``; data arrays fall through to strings."""
        elements = node.named_children
        values = [element.named_children for element in elements]
        if len(elements) != 2 or any(
            element.type != "array_element_initializer" or len(value) != 1
            for element, value in zip(elements, values, strict=True)
        ):
            return None
        receiver, method_node = values[0][0], values[1][0]
        method = _php_literal(method_node)
        if method is None or not re.fullmatch(_PHP_IDENTIFIER, method):
            return None
        literal = _php_literal(receiver)
        if literal is not None:
            classes = self.classes.get(_php_key(literal), [])
            return self._methods(classes, method, dispatch=False) or None
        if receiver.type == "class_constant_access_expression":
            parts = receiver.named_children
            found = self._scope_classes(parts[0], node) if _text(parts[-1]) == "class" else None
            if found is not None:
                return self._methods(found[0], method, dispatch=found[1]) or {_UNKNOWN_CALLABLE}
        if receiver.type == "variable_name" and method.lower() in self.method_names:
            enclosing = self._enclosing_class(node) if _text(receiver) == "$this" else None
            targets = self._methods([enclosing] if enclosing else [], method, dispatch=True)
            return targets or {_Target(callable=True, dispatch=True)}
        return None
