"""Preserve exceptional syntax after frontends establish declaration ownership.

This pass records source evidence. It does not invent call edges for unwinding,
infer exception propagation, or decide which handler will run.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable, Iterable
from dataclasses import replace

from tree_sitter import Node

from trailmark.analysis._python_exceptions import PythonRaisedNames
from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.exceptions import ExceptionEvent, ExceptionEventKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit, NodeKind, SourceLocation, TypeRef
from trailmark.parsers._binding_index import syntax_nodes
from trailmark.parsers._common import make_location, node_text

_KINDS = {
    "vb": {
        "throw_statement": ExceptionEventKind.RAISE,
        "catch_block": ExceptionEventKind.HANDLER,
        "finally_block": ExceptionEventKind.CLEANUP,
        "using_statement": ExceptionEventKind.CLEANUP,
    },
    "powershell": {
        "flow_control_statement": ExceptionEventKind.RAISE,
        "catch_clause": ExceptionEventKind.HANDLER,
        "finally_clause": ExceptionEventKind.CLEANUP,
        "trap_statement": ExceptionEventKind.HANDLER,
    },
    "ruby": {"rescue": ExceptionEventKind.HANDLER, "ensure": ExceptionEventKind.CLEANUP},
    "julia": {
        "catch_clause": ExceptionEventKind.HANDLER,
        "finally_clause": ExceptionEventKind.CLEANUP,
    },
    "erlang": {
        "catch_clause": ExceptionEventKind.HANDLER,
        "catch_expr": ExceptionEventKind.HANDLER,
        "try_after": ExceptionEventKind.CLEANUP,
    },
    "perl": {"eval_expression": ExceptionEventKind.HANDLER},
    "go": {"defer_statement": ExceptionEventKind.CLEANUP},
    "rust": {"try_expression": ExceptionEventKind.PROPAGATE},
    "zig": {
        "Statement": ExceptionEventKind.CLEANUP,
        "UnaryExpr": ExceptionEventKind.PROPAGATE,
        "BinaryExpr": ExceptionEventKind.HANDLER,
    },
    "solidity": {
        "revert_statement": ExceptionEventKind.RAISE,
        "catch_clause": ExceptionEventKind.HANDLER,
    },
    "move": {"abort_expression": ExceptionEventKind.TERMINATE},
    "aiken": {
        "error_term": ExceptionEventKind.TERMINATE,
        "expect_assignment": ExceptionEventKind.TERMINATE,
    },
    "masm": {"assert_op": ExceptionEventKind.TERMINATE},
    "tact": {"catch_clause": ExceptionEventKind.HANDLER},
    "func": {"try_statement": ExceptionEventKind.HANDLER},
    "thrift": {"throws": ExceptionEventKind.DECLARES},
    "python": {
        "raise_statement": ExceptionEventKind.RAISE,
        "except_clause": ExceptionEventKind.HANDLER,
        "finally_clause": ExceptionEventKind.CLEANUP,
        "with_statement": ExceptionEventKind.CLEANUP,
    },
    "javascript": {
        "throw_statement": ExceptionEventKind.RAISE,
        "catch_clause": ExceptionEventKind.HANDLER,
        "finally_clause": ExceptionEventKind.CLEANUP,
    },
    "typescript": {
        "throw_statement": ExceptionEventKind.RAISE,
        "catch_clause": ExceptionEventKind.HANDLER,
        "finally_clause": ExceptionEventKind.CLEANUP,
    },
    "cpp": {
        "throw_statement": ExceptionEventKind.RAISE,
        "catch_clause": ExceptionEventKind.HANDLER,
        "noexcept": ExceptionEventKind.DECLARES,
        "throw_specifier": ExceptionEventKind.DECLARES,
    },
    "java": {
        "throw_statement": ExceptionEventKind.RAISE,
        "catch_clause": ExceptionEventKind.HANDLER,
        "finally_clause": ExceptionEventKind.CLEANUP,
        "resource_specification": ExceptionEventKind.CLEANUP,
        "throws": ExceptionEventKind.DECLARES,
    },
    "csharp": {
        "throw_statement": ExceptionEventKind.RAISE,
        "throw_expression": ExceptionEventKind.RAISE,
        "catch_clause": ExceptionEventKind.HANDLER,
        "finally_clause": ExceptionEventKind.CLEANUP,
        "using_statement": ExceptionEventKind.CLEANUP,
        "local_declaration_statement": ExceptionEventKind.CLEANUP,
    },
    "objc": {
        "throw_statement": ExceptionEventKind.RAISE,
        "catch_clause": ExceptionEventKind.HANDLER,
        "finally_clause": ExceptionEventKind.CLEANUP,
    },
    "kotlin": {
        "jump_expression": ExceptionEventKind.RAISE,
        "catch_block": ExceptionEventKind.HANDLER,
        "finally_block": ExceptionEventKind.CLEANUP,
    },
    "swift": {
        "control_transfer_statement": ExceptionEventKind.RAISE,
        "catch_block": ExceptionEventKind.HANDLER,
        "throws": ExceptionEventKind.DECLARES,
        "try_expression": ExceptionEventKind.PROPAGATE,
    },
    "dart": {
        "throw_expression": ExceptionEventKind.RAISE,
        "rethrow_expression": ExceptionEventKind.RETHROW,
        "catch_clause": ExceptionEventKind.HANDLER,
        "finally_clause": ExceptionEventKind.CLEANUP,
        "type_identifier": ExceptionEventKind.HANDLER,
    },
    "php": {
        "throw_expression": ExceptionEventKind.RAISE,
        "catch_clause": ExceptionEventKind.HANDLER,
        "finally_clause": ExceptionEventKind.CLEANUP,
    },
}
_CALLS = {
    "r": {
        "stop": "raise",
        "tryCatch": "handler",
        "withCallingHandlers": "handler",
        "on.exit": "cleanup",
    },
    "perl": {"die": "raise", "CORE::die": "raise"},
    "ruby": {
        "raise": "raise",
        "fail": "raise",
        "throw": "propagate",
        "catch": "handler",
        "Kernel.raise": "raise",
        "Kernel.fail": "raise",
    },
    "lua": {"error": "raise", "assert": "raise", "pcall": "handler", "xpcall": "handler"},
    "c": {
        "setjmp": "handler",
        "sigsetjmp": "handler",
        "longjmp": "propagate",
        "siglongjmp": "propagate",
        "abort": "terminate",
        "exit": "terminate",
        "_Exit": "terminate",
    },
    "bash": {"trap": "handler", "exit": "terminate"},
    "go": {"panic": "raise", "recover": "handler"},
    "rust": {
        "panic": "raise",
        "std::panic": "raise",
        "core::panic": "raise",
        "std::process::abort": "terminate",
    },
    "haskell": {
        "throw": "raise",
        "throwIO": "raise",
        "catch": "handler",
        "handle": "handler",
        "try": "handler",
        "bracket": "cleanup",
        "finally": "cleanup",
        "onException": "cleanup",
    },
    "julia": {"throw": "raise", "rethrow": "rethrow", "error": "raise"},
    "erlang": {
        "throw": "raise",
        "error": "raise",
        "exit": "raise",
        "erlang:throw": "raise",
        "erlang:error": "raise",
        "erlang:exit": "raise",
    },
    "circom": {"assert": "terminate"},
    "cairo": {"panic": "terminate", "panic_with_felt252": "terminate"},
    "tact": {"throw": "raise", "nativeThrow": "raise", "require": "raise"},
    "func": {"throw": "raise", "throw_if": "raise", "throw_unless": "raise"},
    "sway": {"revert": "terminate", "assert": "terminate", "require": "terminate"},
    "solidity": {"require": "raise", "assert": "raise"},
}
_CALL_NODES = frozenset(
    {
        "call",
        "call_expression",
        "function_call",
        "function_call_expression",
        "ambiguous_function_call_expression",
        "static_call_expression",
        "function_application",
        "command",
        "apply",
        "macro_invocation",
    }
)
_CALLABLES = frozenset({NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.PROCEDURE})
_IDENTIFIERS = frozenset({"identifier", "simple_identifier", "type_identifier", "name"})
_QUALIFIED = frozenset({"qualified_name", "relative_name", "navigation_expression"})
_NESTED_CALLABLES = {
    "go": frozenset({"func_literal"}),
    "rust": frozenset({"closure_expression", "async_block"}),
    "ruby": frozenset({"block", "do_block", "lambda"}),
}
# These frontends previously omitted throws or only accepted unqualified
# constructor names, so direct raise events replace their legacy type hints.
_TYPED_RAISES = frozenset(
    {
        "python",
        "javascript",
        "typescript",
        "cpp",
        "java",
        "csharp",
        "objc",
        "kotlin",
        "swift",
        "dart",
        "php",
        "vb",
        "solidity",
    }
)
_BARE_RETHROWS = frozenset({"python", "cpp", "csharp", "objc", "vb"})
_EXTRA_SYNTAX = {"swift": frozenset({"call_expression"}), "ruby": frozenset({"identifier"})}
# Call-based handlers that take the protected body as an argument. The callee
# identifies them, so edits inside that body are not handler changes.
_WRAPPERS = {
    "r": frozenset({"tryCatch", "withCallingHandlers"}),
    "lua": frozenset({"pcall", "xpcall"}),
    "ruby": frozenset({"catch"}),
    "haskell": frozenset({"catch", "handle", "try", "bracket", "finally", "onException"}),
}
# A callable declares exceptions only in its own signature: (declarator nodes
# between the clause and the declaration, declaration node types). Function
# types in parameters, return types, locals and lambdas carry their own contracts.
_SIGNATURES = {
    "cpp": (
        frozenset(
            {
                "function_declarator",
                "pointer_declarator",
                "reference_declarator",
                "parenthesized_declarator",
                "qualified_identifier",
                "pointer_type_declarator",
            }
        ),
        frozenset({"function_definition"}),
    ),
    "java": (frozenset(), frozenset({"method_declaration", "constructor_declaration"})),
    "swift": (
        frozenset(),
        frozenset({"function_declaration", "init_declaration", "protocol_function_declaration"}),
    ),
    "thrift": (frozenset(), frozenset({"function_definition"})),
}
_NEW_FIELDS = {"javascript": "constructor", "typescript": "constructor", "vb": "type"}
_CALLEE_FIELDS = ("function", "name", "method", "macro")
_RUBY_STATEMENTS = frozenset(
    {
        "program",
        "body_statement",
        "begin",
        "then",
        "else",
        "ensure",
        "do",
        "block_body",
        "parenthesized_statements",
    }
)
_RUBY_MODIFIERS = frozenset(
    {"if_modifier", "unless_modifier", "while_modifier", "until_modifier", "rescue_modifier"}
)
_SWIFT_TRY = {"try?": ExceptionEventKind.HANDLER, "try!": ExceptionEventKind.TERMINATE}
_CATCH_SKIPPED = frozenset({"block", "compound_statement", "catch_filter_clause"})
_CATCH_NAMES = frozenset({"type_identifier", "qualified_identifier"})

_Classification = tuple[ExceptionEventKind, EdgeConfidence]
_Refiner = Callable[[Node, _Classification | None], _Classification | None]
_Types = tuple[TypeRef, ...]


def _start(location: SourceLocation) -> tuple[int, int]:
    return location.start_line, location.start_col or 0


def _end(location: SourceLocation) -> tuple[int, int]:
    return location.end_line, location.end_col if location.end_col is not None else 2**63


class _Owners:
    """Resolve the smallest containing declaration without a scan per event."""

    def __init__(self, graph: CodeGraph, file_path: str) -> None:
        self.modules = [
            unit
            for unit in graph.nodes.values()
            if unit.location.file_path == file_path and unit.kind == NodeKind.MODULE
        ]
        self.units = sorted(
            (
                unit
                for unit in graph.nodes.values()
                if unit.location.file_path == file_path and unit.kind in _CALLABLES
            ),
            key=lambda unit: (_start(unit.location), _end(unit.location), unit.id),
        )
        self.starts = [_start(unit.location) for unit in self.units]
        self.ends: list[tuple[int, int]] = []
        maximum = (0, 0)
        for unit in self.units:
            maximum = max(maximum, _end(unit.location))
            self.ends.append(maximum)

    def owner(self, node: Node) -> CodeUnit | None:
        start = node.start_point.row + 1, node.start_point.column
        end = node.end_point.row + 1, node.end_point.column
        index = bisect_right(self.starts, start) - 1
        candidates: list[CodeUnit] = []
        while index >= 0 and self.ends[index] >= end:
            unit = self.units[index]
            if _end(unit.location) >= end:
                candidates.append(unit)
            index -= 1
        if not candidates:
            # Including the file-wide module in the prefix-maximum index would
            # force every event to scan all earlier sibling declarations.
            candidates = [
                unit
                for unit in self.modules
                if _start(unit.location) <= start and _end(unit.location) >= end
            ]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda unit: (
                unit.kind == NodeKind.MODULE,
                unit.location.end_line - unit.location.start_line,
                _end(unit.location)[1] - _start(unit.location)[1],
                unit.name in {"<chunk>", "<script>"},
                unit.id,
            ),
        )


def collect_exception_events(root: Node, file_path: str, graph: CodeGraph) -> None:
    """Attach ordered, source-located evidence to already extracted owners."""
    if graph.language not in _KINDS and graph.language not in _CALLS:
        return
    collector = _Collector(graph, file_path, root)
    events: dict[str, list[ExceptionEvent]] = {}
    for node in syntax_nodes(root, collector.selected()):
        classification = collector.classify(node)
        if classification is None:
            continue
        owner = collector.owners.owner(node)
        if owner is None or not collector.accepts(node, owner, classification):
            continue
        events.setdefault(owner.id, []).append(collector.event(node, classification))
    for identifier, collected in events.items():
        collected.sort(key=lambda event: (_start(event.location), _end(event.location)))
        unit = graph.nodes[identifier]
        graph.nodes[identifier] = replace(
            unit,
            exception_events=tuple(collected),
            exception_types=_exception_types(unit, collected, graph.language),
        )


class _Collector:
    """Classify, own and describe the exceptional syntax of one parsed file."""

    def __init__(self, graph: CodeGraph, file_path: str, root: Node) -> None:
        self.language = graph.language
        self.file_path = file_path
        self.kinds = _KINDS.get(graph.language, {})
        self.call_kinds = _CALLS.get(graph.language, {})
        self.owners = _Owners(graph, file_path)
        self.resolved = _resolved_call_sites(graph)
        self.python_names = PythonRaisedNames(root) if self.language == "python" else None

    def selected(self) -> frozenset[str]:
        calls = _CALL_NODES if self.call_kinds else frozenset()
        return frozenset(self.kinds) | calls | _EXTRA_SYNTAX.get(self.language, frozenset())

    def classify(self, node: Node) -> _Classification | None:
        kind = self.kinds.get(node.type)
        base = (kind, EdgeConfidence.CERTAIN) if kind is not None else None
        if node.type in _CALL_NODES and self.call_kinds:
            call_kind = self.call_kinds.get(_callee(node))
            if call_kind is not None:
                base = ExceptionEventKind(call_kind), EdgeConfidence.UNCERTAIN
        refine = _REFINERS.get(self.language)
        return refine(node, base) if refine is not None else base

    def accepts(self, node: Node, owner: CodeUnit, classification: _Classification) -> bool:
        kind, confidence = classification
        if _inside_unmodeled_callable(node, owner, self.language):
            return False
        if kind == ExceptionEventKind.DECLARES:
            return _declares_signature(node, owner, self.language)
        if confidence == EdgeConfidence.UNCERTAIN:
            # A known local callable or a callable parameter shadows the failure API.
            parameters = {parameter.name for parameter in owner.parameters}
            return (
                make_location(node, self.file_path) not in self.resolved
                and _callee(node) not in parameters
            )
        return True

    def event(self, node: Node, classification: _Classification) -> ExceptionEvent:
        kind, confidence = classification
        expression, types = _payload(node, classification, self.language)
        if kind == ExceptionEventKind.RAISE and self.python_names is not None:
            operand = next(iter(node.named_children), None)
            name = self.python_names.type_name(operand) if operand is not None else None
            types = (TypeRef(name=name),) if name is not None else types
        bare = kind == ExceptionEventKind.RAISE and expression is None
        if bare and self.language in _BARE_RETHROWS:
            kind = ExceptionEventKind.RETHROW
        event = ExceptionEvent(
            kind=kind,
            location=make_location(node, self.file_path),
            syntax="defer" if _swift_defer(node) else node.type,
            expression=expression,
            types=types,
            protected_region=_protected_region(node, self.file_path, kind),
            confidence=confidence,
        )
        reshape = _RESHAPERS.get((self.language, node.type))
        return reshape(node, event, self.file_path) if reshape is not None else event


def _resolved_call_sites(graph: CodeGraph) -> frozenset[SourceLocation]:
    """Call sites bound to a local callable, or deliberately blocked from binding."""
    return frozenset(
        edge.location
        for edge in graph.edges
        if edge.kind == EdgeKind.CALLS
        and edge.location is not None
        and (
            (edge.target_id in graph.nodes and graph.nodes[edge.target_id].kind in _CALLABLES)
            or dict(edge.attributes).get("binding_blocked")
        )
    )


def _exception_types(unit: CodeUnit, events: list[ExceptionEvent], language: str) -> _Types:
    """Derive legacy hints from direct raises; module and class bodies are not raisers."""
    if language not in _TYPED_RAISES or unit.kind not in _CALLABLES:
        return unit.exception_types
    raised = (
        ref for event in events if event.kind == ExceptionEventKind.RAISE for ref in event.types
    )
    return tuple(dict.fromkeys(raised))


def _payload(
    node: Node, classification: _Classification, language: str
) -> tuple[str | None, _Types]:
    """Return the event's operand or header and its syntactic type hints."""
    kind, confidence = classification
    if confidence == EdgeConfidence.UNCERTAIN and _callee(node) in _WRAPPERS.get(language, ()):
        return _callee(node), ()
    if kind == ExceptionEventKind.HANDLER:
        return _header(node), _handler_types(node, language)
    if kind == ExceptionEventKind.DECLARES:
        return node_text(node), _declared_types(node, language)
    if confidence == EdgeConfidence.UNCERTAIN or kind in {
        ExceptionEventKind.PROPAGATE,
        ExceptionEventKind.TERMINATE,
    }:
        return node_text(node), ()
    if kind == ExceptionEventKind.RAISE:
        return _operand(node), _raised_types(node, language)
    return None, ()


def _declares_signature(node: Node, owner: CodeUnit, language: str) -> bool:
    """Whether a throws/noexcept clause belongs to the owner's own signature."""
    if owner.kind not in _CALLABLES:
        return False
    passthrough, declarations = _SIGNATURES.get(language, (frozenset(), frozenset()))
    parent = node.parent
    while parent is not None and parent.type in passthrough:
        parent = parent.parent
    return (
        parent is not None
        and parent.type in declarations
        and (parent.end_point.row + 1, parent.end_point.column) == _end(owner.location)
        and (language != "cpp" or node.parent == _cpp_callable_declarator(parent))
    )


def _cpp_callable_declarator(declaration: Node) -> Node | None:
    """The function declarator nearest the name, excluding returned function types."""
    current = declaration.child_by_field_name("declarator")
    function = None
    while current is not None:
        if current.type == "function_declarator":
            function = current
        if current.type in {"parenthesized_declarator", "reference_declarator"}:
            current = next(iter(current.named_children), None)
        elif current.type == "qualified_identifier":
            current = current.child_by_field_name("name")
        else:
            current = current.child_by_field_name("declarator")
    return function


def _inside_unmodeled_callable(node: Node, owner: CodeUnit, language: str) -> bool:
    """Do not assign an unextracted closure's effects to its creator."""
    boundaries = _NESTED_CALLABLES.get(language)
    if not boundaries:
        return False
    ancestor = node.parent
    while ancestor is not None:
        if ancestor.type in boundaries:
            start = ancestor.start_point.row + 1, ancestor.start_point.column
            end = ancestor.end_point.row + 1, ancestor.end_point.column
            if not (start <= _start(owner.location) and _end(owner.location) <= end):
                return True
        ancestor = ancestor.parent
    return False


def _callee(node: Node) -> str:
    if node.type == "identifier":
        return node_text(node)
    fields = (node.child_by_field_name(field) for field in _CALLEE_FIELDS)
    target = next((child for child in fields if child is not None), None)
    if target is None and node.named_children:
        target = node.named_children[0]
    name = node_text(target) if target is not None else ""
    if receiver := node.child_by_field_name("receiver"):
        return node_text(receiver) + "." + name
    return _remote_callee(node, name)


def _remote_callee(node: Node, name: str) -> str:
    """Qualify an Erlang remote call with the module held by its parent."""
    if node.parent is not None and node.parent.type == "remote":
        module = node.parent.child_by_field_name("module")
        if module is not None:
            return node_text(module).rstrip(":") + ":" + name
    return name


def _ruby_refine(node: Node, base: _Classification | None) -> _Classification | None:
    """Bare ``raise``/``fail`` re-raises ``$!``; method names and receivers do not."""
    if node.type != "identifier":
        return base
    if node_text(node) not in {"raise", "fail"} or not _ruby_statement(node):
        return None
    return ExceptionEventKind.RETHROW, EdgeConfidence.UNCERTAIN


def _ruby_statement(node: Node) -> bool:
    parent = node.parent
    if parent is None:
        return False
    if parent.type in _RUBY_MODIFIERS:
        return parent.child_by_field_name("condition") != node
    return parent.type in _RUBY_STATEMENTS


def _swift_refine(node: Node, base: _Classification | None) -> _Classification | None:
    if _swift_defer(node):
        return ExceptionEventKind.CLEANUP, EdgeConfidence.CERTAIN
    if base is None or node.type != "try_expression":
        return _throw_only(node, base)
    operator = next((child for child in node.named_children if child.type == "try_operator"), None)
    if operator is None:
        return base
    return _SWIFT_TRY.get(node_text(operator), base[0]), base[1]


def _throw_only(node: Node, base: _Classification | None) -> _Classification | None:
    """Kotlin, Swift and PowerShell share one jump node for throw, return and break."""
    if base is None or base[0] != ExceptionEventKind.RAISE:
        return base
    return base if any(node_text(child).lower() == "throw" for child in node.children[:1]) else None


def _zig_refine(node: Node, base: _Classification | None) -> _Classification | None:
    return base if base is not None and _zig_event(node) else None


def _csharp_refine(node: Node, base: _Classification | None) -> _Classification | None:
    if node.type != "local_declaration_statement":
        return base
    return base if any(node_text(child) == "using" for child in node.children[:2]) else None


def _dart_refine(node: Node, base: _Classification | None) -> _Classification | None:
    if node.type != "type_identifier":
        return base
    return base if _dart_on_only(node) else None


def _zig_event(node: Node) -> bool:
    if node.type == "Statement":
        return bool(node.children) and node_text(node.children[0]) in {"defer", "errdefer"}
    if node.type == "UnaryExpr":
        return any(
            child.type == "PrefixOp" and node_text(child) == "try" for child in node.named_children
        )
    if node.type == "BinaryExpr":
        operator = node.child_by_field_name("operator")
        return operator is not None and any(
            node_text(child) == "catch" for child in operator.children
        )
    return False


def _declared_types(node: Node, language: str) -> tuple[TypeRef, ...]:
    if node.type == "noexcept":
        return ()
    if language != "thrift":
        return tuple(TypeRef(name=node_text(child)) for child in node.named_children)
    result = []
    pending = [node]
    while pending:
        child = pending.pop()
        if child.type == "parameter":
            ref = next((part for part in child.named_children if part.type == "type"), None)
            if ref is not None:
                result.append(TypeRef(name=node_text(ref)))
        else:
            pending.extend(reversed(child.named_children))
    return tuple(result)


def _swift_defer(node: Node) -> bool:
    return (
        node.type == "call_expression"
        and bool(node.named_children)
        and node_text(node.named_children[0]) == "defer"
    )


def _dart_on_only(node: Node) -> bool:
    previous, following = node.prev_sibling, node.next_named_sibling
    return (
        node.parent is not None
        and node.parent.type == "try_statement"
        and previous is not None
        and node_text(previous) == "on"
        and following is not None
        and following.type == "block"
    )


def _span(begin: Node, end: Node, file_path: str) -> SourceLocation:
    return SourceLocation(
        file_path,
        begin.start_point.row + 1,
        end.end_point.row + 1,
        begin.start_point.column,
        end.end_point.column,
    )


def _operand(node: Node) -> str | None:
    children = [
        child for child in node.named_children if child.type not in {"throw_keyword", "comment"}
    ]
    if not children:
        return None
    raw = node.text or b""
    start = children[0].start_byte - node.start_byte
    end = children[-1].end_byte - node.start_byte
    return raw[start:end].decode("utf-8", errors="replace")


def _raised_types(node: Node, language: str) -> _Types:
    children = [child for child in node.named_children if child.type != "throw_keyword"]
    if not children:
        return ()
    operand = children[0]
    if operand.type == "expression" and language == "vb" and operand.named_children:
        operand = operand.named_children[0]
    special = _RAISED_NAMES.get(language)
    name = special(node, operand) if special is not None else None
    if name is None:
        target = _constructed_type(operand, language)
        name = node_text(target) if target is not None else None
    return (TypeRef(name=name),) if name else ()


def _constructed_type(operand: Node, language: str) -> Node | None:
    """Constructor-like syntax names a type hint; other operands stay expressions."""
    if operand.type == "object_creation_expression":
        target = operand.child_by_field_name("type")
        if target is not None:
            return target
        names = _IDENTIFIERS | _QUALIFIED
        return next((child for child in operand.named_children if child.type in names), None)
    if operand.type in {"call_expression", "call"}:
        target = operand.child_by_field_name("function")
        if target is None and operand.named_children:
            return operand.named_children[0]
        return target
    field = _NEW_FIELDS.get(language)
    if operand.type == "new_expression" and field is not None:
        return operand.child_by_field_name(field)
    return None


def _swift_raised(node: Node, operand: Node) -> str | None:
    if operand.type == "navigation_expression":
        # A thrown member is an enum case or static value of its containing type.
        return node_text(operand).rsplit(".", 1)[0] or None
    if operand.type != "call_expression" or not operand.named_children:
        return None
    callee = operand.named_children[0]
    if callee.type != "navigation_expression":
        return None
    container, _, member = node_text(callee).rpartition(".")
    # Lowercase members are payload cases or factories; capitalized ones are nested types.
    return container if member[:1].islower() else node_text(callee)


def _dart_raised(node: Node, operand: Node) -> str | None:
    if operand.type == "new_expression":
        return node_text(operand).removeprefix("new").strip().split("(", 1)[0]
    following = [child for child in node.named_children if child.type != "throw_keyword"][1:]
    if any(child.type == "selector" and node_text(child).startswith("(") for child in following):
        return (_operand(node) or "").split("(", 1)[0]
    return None


def _solidity_raised(node: Node, operand: Node) -> str | None:
    """Only ``revert Error(...)`` names an error; ``revert("reason")`` does not."""
    error = node.child_by_field_name("error")
    if error is None or not any(child.type == "revert_arguments" for child in node.children):
        return None
    return node_text(error)


def _handler_types(node: Node, language: str) -> _Types:
    extract = _HANDLER_TYPES.get(language)
    return extract(node) if extract is not None else _type_ref(node.child_by_field_name("type"))


def _type_ref(node: Node | None) -> _Types:
    return (TypeRef(name=node_text(node)),) if node is not None else ()


def _type_refs(nodes: Iterable[Node]) -> _Types:
    return tuple(TypeRef(name=node_text(child)) for child in nodes)


def _no_handler_types(node: Node) -> _Types:
    """A TypeScript catch annotation is not an exception filter."""
    return ()


def _php_handler(node: Node) -> _Types:
    target = node.child_by_field_name("type")
    return _type_refs(target.named_children) if target is not None else ()


def _python_handler(node: Node) -> _Types:
    target = node.child_by_field_name("value")
    if target is not None and target.type == "as_pattern":
        target = target.named_children[0] if target.named_children else None
    if target is not None and target.type == "tuple":
        return _type_refs(target.named_children)
    return _type_ref(target)


def _kotlin_handler(node: Node) -> _Types:
    return _type_ref(
        next((child for child in node.named_children if child.type == "user_type"), None)
    )


def _dart_handler(node: Node) -> _Types:
    if node.type == "type_identifier":
        return _type_ref(node)
    previous = node.prev_named_sibling
    return _type_ref(
        previous if previous is not None and previous.type == "type_identifier" else None
    )


def _ruby_handler(node: Node) -> _Types:
    exceptions = node.child_by_field_name("exceptions")
    return _type_refs(exceptions.named_children) if exceptions is not None else ()


def _erlang_handler(node: Node) -> _Types:
    return _type_ref(node.child_by_field_name("class"))


def _solidity_handler(node: Node) -> _Types:
    return _type_ref(
        next((child for child in node.named_children if child.type == "identifier"), None)
    )


def _catch_declaration(node: Node) -> _Types:
    pending = list(reversed(node.named_children))
    while pending:
        child = pending.pop()
        if child.type in _CATCH_SKIPPED:
            continue
        if child.type == "catch_type":
            return _type_refs(child.named_children)
        if child.type in {"parameter_declaration", "catch_declaration"}:
            return _type_ref(child.child_by_field_name("type"))
        if child.type == "type_name":
            names = (ref for ref in child.named_children if ref.type in _CATCH_NAMES)
            return _type_ref(next(names, None))
        pending.extend(reversed(child.named_children))
    return _type_ref(node.child_by_field_name("type"))


def _header(node: Node) -> str:
    body = node.child_by_field_name("body")
    if body is None:
        body = next(
            (
                child
                for child in node.named_children
                if child.type
                in {
                    "block",
                    "compound_statement",
                    "statements",
                    "statement_block",
                    "then",
                    "statement",
                }
            ),
            None,
        )
    raw = node.text or b""
    return (
        raw[: body.start_byte - node.start_byte].decode("utf-8", errors="replace").strip()
        if body is not None
        else node_text(node)
    )


def _protected_region(
    node: Node,
    file_path: str,
    kind: ExceptionEventKind,
) -> SourceLocation | None:
    if kind not in {ExceptionEventKind.HANDLER, ExceptionEventKind.CLEANUP}:
        return None
    if node.type == "try_expression":
        expression = node.child_by_field_name("expr")
        if expression is not None:
            return make_location(expression, file_path)
    parent = node.parent
    if parent is not None and parent.type == "catch_clauses":
        parent = parent.parent
    if parent is None or parent.type not in {
        "try_statement",
        "try_expression",
        "do_statement",
        "try_with_resources_statement",
        "try_expr",
        "begin",
    }:
        return None
    body = parent.child_by_field_name("body")
    if parent.child_by_field_name("attempt") is not None:
        body = parent.child_by_field_name("attempt")
    if body is None:
        body = next(
            (
                child
                for child in parent.named_children
                if child.type in {"statements", "block", "compound_statement", "statement_block"}
            ),
            None,
        )
    return make_location(body, file_path) if body is not None else None


def _dart_on_clause(node: Node, event: ExceptionEvent, file_path: str) -> ExceptionEvent:
    """An on-only Dart handler spans ``on Type { ... }`` around its type name."""
    begin, end = node.prev_sibling, node.next_named_sibling
    location = _span(begin, end, file_path) if begin is not None and end is not None else None
    return replace(
        event,
        syntax="on_clause",
        expression="on " + node_text(node),
        location=location if location is not None else event.location,
    )


def _func_catch(node: Node, event: ExceptionEvent, file_path: str) -> ExceptionEvent:
    """FunC try/catch has no handler node, so the catch keyword starts the handler."""
    catch = next((child for child in node.children if node_text(child) == "catch"), None)
    if catch is None:
        return event
    before = [child for child in node.children if child.end_byte <= catch.start_byte]
    begin = next((child for child in before if node_text(child) == "{"), None)
    end = next((child for child in reversed(before) if node_text(child) == "}"), None)
    raw = (node.text or b"")[catch.start_byte - node.start_byte :]
    return replace(
        event,
        syntax="catch",
        location=_span(catch, node, file_path),
        protected_region=(
            _span(begin, end, file_path)
            if begin is not None and end is not None
            else event.protected_region
        ),
        expression=raw.decode("utf-8", errors="replace").split("{", 1)[0].strip(),
    )


# Per-frontend hooks. Frontends without an entry use the shared rules above.
_REFINERS: dict[str, _Refiner] = {
    "ruby": _ruby_refine,
    "swift": _swift_refine,
    "kotlin": _throw_only,
    "powershell": _throw_only,
    "zig": _zig_refine,
    "csharp": _csharp_refine,
    "dart": _dart_refine,
}
_RAISED_NAMES: dict[str, Callable[[Node, Node], str | None]] = {
    "swift": _swift_raised,
    "dart": _dart_raised,
    "solidity": _solidity_raised,
}
_HANDLER_TYPES: dict[str, Callable[[Node], _Types]] = {
    "javascript": _no_handler_types,
    "typescript": _no_handler_types,
    "php": _php_handler,
    "python": _python_handler,
    "kotlin": _kotlin_handler,
    "dart": _dart_handler,
    "ruby": _ruby_handler,
    "erlang": _erlang_handler,
    "solidity": _solidity_handler,
    "java": _catch_declaration,
    "csharp": _catch_declaration,
    "cpp": _catch_declaration,
    "objc": _catch_declaration,
}
_RESHAPERS: dict[tuple[str, str], Callable[[Node, ExceptionEvent, str], ExceptionEvent]] = {
    ("dart", "type_identifier"): _dart_on_clause,
    ("func", "try_statement"): _func_catch,
}
