"""Preserve exceptional syntax after frontends establish declaration ownership.

This pass records source evidence. It does not invent call edges for unwinding,
infer exception propagation, or decide which handler will run.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import replace

from tree_sitter import Node

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
                unit.id,
            ),
        )


def collect_exception_events(root: Node, file_path: str, graph: CodeGraph) -> None:
    """Attach ordered, source-located evidence to already extracted owners."""
    kinds = _KINDS.get(graph.language, {})
    call_kinds = _CALLS.get(graph.language, {})
    if not kinds and not call_kinds:
        return
    owners = _Owners(graph, file_path)
    resolved_calls: set[SourceLocation] = {
        edge.location
        for edge in graph.edges
        if edge.kind == EdgeKind.CALLS
        and edge.location is not None
        and (
            (edge.target_id in graph.nodes and graph.nodes[edge.target_id].kind in _CALLABLES)
            or dict(edge.attributes).get("binding_blocked")
        )
    }
    events: dict[str, list[ExceptionEvent]] = {}
    selected = frozenset(kinds) | (_CALL_NODES if call_kinds else frozenset())
    if graph.language == "swift":
        selected |= {"call_expression"}
    if graph.language == "ruby":
        selected |= {"identifier"}
    for node in syntax_nodes(root, selected):
        kind = kinds.get(node.type)
        confidence = EdgeConfidence.CERTAIN
        if (
            graph.language == "ruby"
            and node.type == "identifier"
            and node_text(node)
            in {
                "raise",
                "fail",
            }
            and node.parent is not None
            and node.parent.type in {"body_statement", "then", "begin"}
        ):
            kind = ExceptionEventKind.RAISE
            confidence = EdgeConfidence.UNCERTAIN
        if node.type in _CALL_NODES and call_kinds:
            call_kind = call_kinds.get(_callee(node))
            if call_kind is not None:
                kind = ExceptionEventKind(call_kind)
                confidence = EdgeConfidence.UNCERTAIN
        if graph.language == "swift" and _swift_defer(node):
            kind = ExceptionEventKind.CLEANUP
        if graph.language == "swift" and node.type == "try_expression":
            operator = next(
                (child for child in node.named_children if child.type == "try_operator"), None
            )
            if operator is not None:
                kind = {
                    "try?": ExceptionEventKind.HANDLER,
                    "try!": ExceptionEventKind.TERMINATE,
                }.get(node_text(operator), kind)
        if kind is None:
            continue
        if (
            kind == ExceptionEventKind.RAISE
            and graph.language in {"kotlin", "swift", "powershell"}
            and not any(node_text(child).lower() == "throw" for child in node.children[:1])
        ):
            continue
        if graph.language == "zig" and not _zig_event(node):
            continue
        if node.type == "local_declaration_statement" and not any(
            node_text(child) == "using" for child in node.children[:2]
        ):
            continue
        if graph.language == "dart" and node.type == "type_identifier" and not _dart_on_only(node):
            continue
        owner = owners.owner(node)
        if owner is None or _inside_unmodeled_callable(node, owner, graph.language):
            continue
        if confidence == EdgeConfidence.UNCERTAIN and (
            make_location(node, file_path) in resolved_calls
            or _callee(node) in {parameter.name for parameter in owner.parameters}
        ):
            continue
        expression = _operand(node) if kind == ExceptionEventKind.RAISE else None
        if confidence == EdgeConfidence.UNCERTAIN or kind in {
            ExceptionEventKind.PROPAGATE,
            ExceptionEventKind.TERMINATE,
        }:
            expression = node_text(node)
        types = ()
        if kind == ExceptionEventKind.RAISE:
            types = (
                _raised_types(node, graph.language) if confidence == EdgeConfidence.CERTAIN else ()
            )
            if expression is None and graph.language in {"python", "cpp", "csharp", "objc", "vb"}:
                kind = ExceptionEventKind.RETHROW
        elif kind == ExceptionEventKind.HANDLER:
            types = _handler_types(node, graph.language)
            expression = _header(node)
        elif kind == ExceptionEventKind.DECLARES:
            types = _declared_types(node, graph.language)
            expression = node_text(node)
        location = make_location(node, file_path)
        protected = _protected_region(node, file_path, kind)
        syntax = "defer" if _swift_defer(node) else node.type
        if graph.language == "dart" and node.type == "type_identifier":
            syntax = "on_clause"
            expression = "on " + node_text(node)
            begin, end = node.prev_sibling, node.next_named_sibling
            if begin is not None and end is not None:
                location = _span(begin, end, file_path)
        if graph.language == "func" and node.type == "try_statement":
            catch = next((child for child in node.children if node_text(child) == "catch"), None)
            if catch is not None:
                syntax = "catch"
                location = _span(catch, node, file_path)
                before = [child for child in node.children if child.end_byte <= catch.start_byte]
                begin = next((child for child in before if node_text(child) == "{"), None)
                end = next((child for child in reversed(before) if node_text(child) == "}"), None)
                if begin is not None and end is not None:
                    protected = _span(begin, end, file_path)
                raw = node.text or b""
                start = catch.start_byte - node.start_byte
                expression = raw[start:].decode("utf-8", errors="replace").split("{", 1)[0].strip()
        events.setdefault(owner.id, []).append(
            ExceptionEvent(
                kind=kind,
                location=location,
                syntax=syntax,
                expression=expression,
                types=types,
                protected_region=protected,
                confidence=confidence,
            )
        )
    for identifier, collected in events.items():
        collected.sort(key=lambda event: (_start(event.location), _end(event.location)))
        unit = graph.nodes[identifier]
        # These frontends previously omitted throws or only accepted unqualified
        # constructor names. Unknown operands stay in events without becoming types.
        raised = tuple(
            dict.fromkeys(
                ref
                for event in collected
                if event.kind == ExceptionEventKind.RAISE
                for ref in event.types
            )
        )
        updated_types = (
            raised
            if graph.language
            in {
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
            else unit.exception_types
        )
        graph.nodes[identifier] = replace(
            unit, exception_events=tuple(collected), exception_types=updated_types
        )


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
    target = node.child_by_field_name("function")
    if target is None:
        target = node.child_by_field_name("name")
    if target is None:
        target = node.child_by_field_name("method")
    if target is None:
        target = node.child_by_field_name("macro")
    if target is None and node.named_children:
        target = node.named_children[0]
    name = node_text(target) if target is not None else ""
    if receiver := node.child_by_field_name("receiver"):
        return node_text(receiver) + "." + name
    if node.parent is not None and node.parent.type == "remote":
        module = node.parent.child_by_field_name("module")
        if module is not None:
            return node_text(module).rstrip(":") + ":" + name
    return name


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


def _raised_types(node: Node, language: str) -> tuple[TypeRef, ...]:
    children = [child for child in node.named_children if child.type != "throw_keyword"]
    if not children:
        return ()
    expression = children[0]
    if expression.type == "expression" and language == "vb" and expression.named_children:
        expression = expression.named_children[0]
    if expression.type == "object_creation_expression":
        target = expression.child_by_field_name("type")
        if target is None:
            target = next(
                (c for c in expression.named_children if c.type in _IDENTIFIERS | _QUALIFIED), None
            )
    elif expression.type in {"call_expression", "call"}:
        target = expression.child_by_field_name("function")
        if target is None and expression.named_children:
            target = expression.named_children[0]
    elif expression.type == "new_expression" and language in {"javascript", "typescript"}:
        target = expression.child_by_field_name("constructor")
    elif language == "dart" and expression.type == "new_expression":
        name = node_text(expression).removeprefix("new").strip().split("(", 1)[0]
        return (TypeRef(name=name),)
    elif language == "vb" and expression.type == "new_expression":
        target = expression.child_by_field_name("type")
    elif language == "solidity":
        target = node.child_by_field_name("error")
    elif language == "dart" and any(
        c.type == "selector" and node_text(c).startswith("(") for c in children[1:]
    ):
        name = (_operand(node) or "").split("(", 1)[0]
        return (TypeRef(name=name),)
    elif language == "swift" and expression.type == "navigation_expression":
        name = node_text(expression).rsplit(".", 1)[0]
        return (TypeRef(name=name),) if name else ()
    else:
        return ()
    return (TypeRef(name=node_text(target)),) if target is not None else ()


def _handler_types(node: Node, language: str) -> tuple[TypeRef, ...]:
    if language in {"javascript", "typescript"}:
        return ()
    target = node.child_by_field_name("type")
    if language == "php" and target is not None:
        return tuple(TypeRef(name=node_text(child)) for child in target.named_children)
    if language == "python":
        target = node.child_by_field_name("value")
        if target is not None and target.type == "as_pattern":
            target = target.named_children[0] if target.named_children else None
        if target is not None and target.type == "tuple":
            return tuple(TypeRef(name=node_text(child)) for child in target.named_children)
    if language == "kotlin":
        target = next((child for child in node.named_children if child.type == "user_type"), None)
    if language == "dart":
        if node.type == "type_identifier":
            return (TypeRef(name=node_text(node)),)
        previous = node.prev_named_sibling
        target = previous if previous is not None and previous.type == "type_identifier" else None
    if language == "ruby":
        exceptions = node.child_by_field_name("exceptions")
        return (
            tuple(TypeRef(name=node_text(child)) for child in exceptions.named_children)
            if exceptions is not None
            else ()
        )
    if language == "erlang":
        category = node.child_by_field_name("class")
        return (TypeRef(name=node_text(category)),) if category is not None else ()
    if language == "solidity":
        selector = next(
            (child for child in node.named_children if child.type == "identifier"), None
        )
        return (TypeRef(name=node_text(selector)),) if selector is not None else ()
    if language in {"java", "csharp", "cpp", "objc"}:
        pending = list(reversed(node.named_children))
        while pending:
            child = pending.pop()
            if child.type in {"block", "compound_statement", "catch_filter_clause"}:
                continue
            if child.type == "catch_type":
                return tuple(TypeRef(name=node_text(ref)) for ref in child.named_children)
            if child.type in {"parameter_declaration", "catch_declaration"}:
                target = child.child_by_field_name("type")
                break
            if child.type == "type_name":
                target = next(
                    (
                        ref
                        for ref in child.named_children
                        if ref.type in {"type_identifier", "qualified_identifier"}
                    ),
                    None,
                )
                break
            pending.extend(reversed(child.named_children))
    return (TypeRef(name=node_text(target)),) if target is not None else ()


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
