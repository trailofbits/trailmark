"""Retain operand syntax while each frontend's tree is available; never infer values."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

from tree_sitter import Node

from trailmark.models.constraints import CallArgument, CallSite, ParameterBinding
from trailmark.models.edges import EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit, NodeKind, SourceLocation
from trailmark.parsers._common import make_location

_LISTS = frozenset(
    {
        "arguments",
        "argument_list",
        "value_arguments",
        "actual_parameters",
        "call_arguments",
        "function_arguments",
        "arg_list",
        "arglist",
        "expression_list",
        "command_elements",
        "expr_args",
        "fn_args",
        "list_expression",
    }
)
_APPLICATIONS = frozenset(
    {
        "application",
        "application_expression",
        "function_application",
        "apply_expression",
        "app",
        "fexpr",
        "suffix_expression",
    }
)
_UNPACKED = frozenset(
    {
        "list_splat",
        "dictionary_splat",
        "spread_element",
        "spread_argument",
        "variadic_unpacking",
        "splat_argument",
        "hash_splat_argument",
        "variadic_argument",
    }
)
_LABELLED = frozenset({"keyword_argument", "named_argument", "value_argument", "argument"})
_IMPLICIT = frozenset(
    {
        "masm",
        "func",
        "haskell",
        "lean",
        "fstar",
        "rocq",
        "whyml",
        "tlaplus",
        "tamarin",
        "proverif",
        "r",
        "bash",
        "powershell",
        "perl",
        "ruby",
        "lua",
        "julia",
    }
)


def _operands(node: Node) -> tuple[Node, list[Node], bool]:
    """Return the invocation, explicit operand nodes, and whether arity is known."""
    if (
        node.type in _LISTS
        and node.parent is not None
        and node.parent.child_by_field_name("arguments") == node
    ):
        node = node.parent
    while (
        node.parent is not None
        and node.parent.type in _APPLICATIONS
        and node.parent.named_children
        and node.parent.named_children[0] == node
    ):
        node = node.parent
    invocation = node
    if (
        node.type == "expression_statement"
        and len(node.named_children) == 1
        and node.named_children[0].type
        in {"function_call_expression", "call_expression", "method_call_expression"}
    ):
        _, arguments, known = _operands(node.named_children[0])
        return invocation, arguments, known
    if node.type == "message_expression":
        return (
            node,
            [
                child
                for index, child in enumerate(node.children)
                if child.is_named
                and node.field_name_for_child(index) not in {"receiver", "method"}
                and "comment" not in child.type
            ],
            True,
        )
    if node.type == "remote":
        inner = node.child_by_field_name("fun")
        if inner is not None:
            node = inner
    container = (
        node.child_by_field_name("arguments")
        or node.child_by_field_name("args")
        or node.child_by_field_name("func_arguments")
    )
    if container is None:
        container = next((child for child in node.named_children if child.type in _LISTS), None)
    if container is None:
        wrappers = [
            child for child in node.named_children if child.type in {"call_suffix", "argument_part"}
        ]
        while wrappers and container is None:
            wrapper = wrappers.pop()
            container = next(
                (child for child in wrapper.named_children if child.type in _LISTS), None
            )
            wrappers.extend(
                child
                for child in wrapper.named_children
                if child.type in {"call_suffix", "argument_part"}
            )
    if container is not None:
        if node.type in {"function_application", "method_call"} and container.type in {
            "unit_literal",
            "parenthesized_expression",
            "tensor_expression",
        }:
            return (
                invocation,
                [child for child in container.named_children if "comment" not in child.type],
                True,
            )
        if container.type not in _LISTS:
            operands = []
            for field in ("arguments", "args", "func_arguments"):
                operands.extend(node.children_by_field_name(field))
            return invocation, operands, True
        return (
            invocation,
            [child for child in container.named_children if "comment" not in child.type],
            True,
        )
    arguments = list(node.children_by_field_name("argument"))
    if node.type == "apply":
        arguments = []
        current = node
        while current.type == "apply":
            arguments[0:0] = current.children_by_field_name("argument")
            current = current.children_by_field_name("function")[0]
        return invocation, arguments, True
    if arguments:
        return node, arguments, True
    if node.type in _APPLICATIONS:
        parts = list(node.named_children)
        if len(parts) >= 2:
            return node, parts[1:], True
    # Some grammars put the operands directly between parenthesis tokens.
    opening = next((index for index, child in enumerate(node.children) if child.type == "("), None)
    closing = next((index for index, child in enumerate(node.children) if child.type == ")"), None)
    if opening is not None and closing is not None and opening < closing:
        return (
            node,
            [
                child
                for child in node.children[opening + 1 : closing]
                if child.is_named and "comment" not in child.type
            ],
            True,
        )
    return node, [], False


def _source_text(data: bytes) -> tuple[str, str | None]:
    try:
        return data.decode(), None
    except UnicodeDecodeError:
        return data.decode(errors="backslashreplace"), data.hex()


def _argument(node: Node, index: int, path: str, data: bytes) -> CallArgument:
    kind = "positional"
    name = None
    value = node
    if node.type in _UNPACKED or any(child.type in _UNPACKED for child in node.named_children):
        kind = "unpacked"
    elif (
        node.type == "pair"
        and (key := node.child_by_field_name("key")) is not None
        and key.type == "hash_key_symbol"
        and (operand := node.child_by_field_name("value")) is not None
    ):
        kind, name, value = "named", _source_text(data[key.start_byte : key.end_byte])[0], operand
    elif node.type in _LABELLED:
        label = node.child_by_field_name("name") or node.child_by_field_name("label")
        operand = node.child_by_field_name("value")
        separator = next(
            (index for index, child in enumerate(node.children) if child.type in {":", "="}), None
        )
        if separator is not None:
            label = label or next(
                (
                    child
                    for child in node.children[:separator]
                    if child.is_named and "comment" not in child.type
                ),
                None,
            )
            operand = operand or next(
                (
                    child
                    for child in node.children[separator + 1 :]
                    if child.is_named and "comment" not in child.type
                ),
                None,
            )
        if label is None:
            wrapper = next(
                (
                    child
                    for child in node.named_children
                    if child.type in {"label", "name_colon", "value_argument_label"}
                ),
                None,
            )
            if wrapper is not None:
                label = wrapper.named_children[0] if wrapper.named_children else wrapper
                operand = operand or next(
                    (
                        child
                        for child in node.named_children
                        if child != wrapper and "comment" not in child.type
                    ),
                    None,
                )
        if label is not None and operand is not None:
            kind, name, value = (
                "named",
                _source_text(data[label.start_byte : label.end_byte])[0],
                operand,
            )
    expression, raw = _source_text(data[value.start_byte : value.end_byte])
    return CallArgument(
        index,
        expression,
        make_location(value, path),
        kind,
        name,
        raw,
    )


def _receiver(node: Node, path: str, data: bytes) -> CallArgument | None:
    receiver = (
        node.child_by_field_name("object")
        or node.child_by_field_name("receiver")
        or node.child_by_field_name("invocant")
    )
    if receiver is None:
        callee = node.child_by_field_name("function")
        if callee is None and node.named_children:
            first = node.named_children[0]
            if first.type == "navigation_expression":
                callee = first
        if callee is not None:
            fields = {
                "member_access_expression": "expression",
                "selector_expression": "operand",
                "field_expression": "argument",
                "navigation_expression": "target",
            }
            receiver = (
                callee.child_by_field_name("object")
                or callee.child_by_field_name("value")
                or callee.child_by_field_name(fields.get(callee.type, "object"))
            )
            if receiver is None and callee.type == "navigation_expression":
                receiver = callee.named_children[0]
    return _argument(receiver, 0, path, data) if receiver is not None else None


def _dart_prefix(node: Node, path: str, data: bytes) -> tuple[int, CallArgument | None]:
    """Recover a Dart invocation prefix split across sibling selectors."""
    selectors = {"selector", "unconditional_assignable_selector"}
    prefix = []
    previous = node.prev_sibling
    while previous is not None:
        if "comment" in previous.type:
            pass
        elif previous.type in selectors:
            prefix.insert(0, previous)
        elif previous.is_named or previous.type in {"this", "super"}:
            prefix.insert(0, previous)
            break
        else:
            break
        previous = previous.prev_sibling
    if not prefix or prefix[0].type in selectors:
        return node.start_byte, None
    receiver = None
    if prefix[-1].type in selectors and not any(
        child.type == "argument_part" for child in prefix[-1].named_children
    ):
        first, last = prefix[0], prefix[-2]
        text, raw = _source_text(data[first.start_byte : last.end_byte])
        receiver = CallArgument(
            0,
            text,
            SourceLocation(
                path,
                first.start_point.row + 1,
                last.end_point.row + 1,
                first.start_point.column,
                last.end_point.column,
            ),
            expression_bytes=raw,
        )
    return prefix[0].start_byte, receiver


def _signature(
    node: Node, unit: CodeUnit, data: bytes, language: str
) -> tuple[ParameterBinding, ...] | None:
    parameters = node.child_by_field_name("parameters")
    if parameters is None:
        return None
    slots = []
    keyword_only = False
    for parameter in parameters.named_children:
        if parameter.type == "positional_separator":
            slots = [replace(slot, kind="positional_only") for slot in slots]
            continue
        if parameter.type == "keyword_separator":
            keyword_only = True
            continue
        name = (
            parameter if parameter.type == "identifier" else parameter.child_by_field_name("name")
        )
        if name is None:
            name = next(
                (
                    child
                    for child in parameter.named_children
                    if child.type
                    in {
                        "identifier",
                        "variable_name",
                        "list_splat_pattern",
                        "dictionary_splat_pattern",
                    }
                ),
                None,
            )
        if name is None:
            continue
        raw_name = data[name.start_byte : name.end_byte].decode().lstrip("*$")
        if raw_name not in {slot.name for slot in unit.parameters}:
            continue
        raw = data[parameter.start_byte : parameter.end_byte].lstrip()
        if raw.startswith(b"**"):
            mode = "var_keyword"
        elif raw.startswith(b"*") or parameter.type == "variadic_parameter":
            mode = "var_positional"
            keyword_only = True
        else:
            mode = "keyword_only" if keyword_only else "positional_or_keyword"
        formal = next(slot for slot in unit.parameters if slot.name == raw_name)
        preserving = mode not in {"var_positional", "var_keyword"} and (
            language == "python" or formal.type_ref is None or formal.type_ref.name == "mixed"
        )
        if node.parent is not None and node.parent.type == "decorated_definition":
            if mode not in {"var_positional", "var_keyword"}:
                mode = "unknown"
            preserving = False
        slots.append(ParameterBinding(raw_name, mode, preserving))
    return (
        tuple(slots)
        if tuple(slot.name for slot in slots) == tuple(slot.name for slot in unit.parameters)
        else None
    )


def collect_call_sites(
    root: Node, path: str, graph: CodeGraph, *, source: bytes | None = None
) -> None:
    """Keep every located call, with explicit gaps where syntax or binding is unavailable."""
    data = (
        source
        if source is not None
        else (root.text if root.start_byte == 0 else Path(path).read_bytes())
    )
    if data is None:
        data = Path(path).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    previous = graph.source_digests.get(path)
    if previous is not None and previous != digest:
        raise ValueError("Conflicting source bytes during call-site collection")
    graph.source_digests[path] = digest
    edges = [
        edge
        for edge in graph.edges
        if (
            edge.kind == EdgeKind.CALLS
            or (
                edge.kind == EdgeKind.REFERENCES
                and (reference := graph._parse_state.reference(edge)) is not None
                and reference.application
            )
        )
        and (
            edge.location is not None
            and edge.location.file_path == path
            or edge.location is None
            and graph.nodes.get(edge.source_id) is not None
            and graph.nodes[edge.source_id].location.file_path == path
        )
    ]

    def key(location: SourceLocation) -> tuple[int, int | None, int, int | None]:
        return location.start_line - 1, location.start_col, location.end_line - 1, location.end_col

    declarations = {
        key(unit.location): unit
        for unit in graph.nodes.values()
        if graph.language in {"python", "php"}
        and unit.location.file_path == path
        and unit.kind in {NodeKind.FUNCTION, NodeKind.METHOD}
    }
    if not edges and not declarations:
        return
    wanted = {key(edge.location) for edge in edges if edge.location is not None}
    syntax: dict[tuple[int, int | None, int, int | None], Node] = {}
    pending = [root]
    while pending:
        node = pending.pop()
        start, end = node.start_point, node.end_point
        position = start.row, start.column, end.row, end.column
        if position in declarations:
            unit = declarations[position]
            profile = _signature(node, unit, data, graph.language)
            if profile is not None:
                graph.parameter_bindings[unit.id] = profile
        if position in wanted and (
            position not in syntax
            or len(node.named_children) > len(syntax[position].named_children)
        ):
            syntax[position] = node
        pending.extend(node.named_children)
    language = "c_sharp" if graph.language == "csharp" else graph.language
    for edge in edges:
        gaps = []
        deferred = bool(dict(edge.attributes).get("deferred"))
        execution = "deferred" if deferred else "call"
        location = edge.location or graph.nodes[edge.source_id].location
        node = syntax.get(key(location))
        arguments = ()
        receiver = None
        text = ""
        raw = None
        if node is None:
            gaps.append("call_syntax_unavailable")
            execution = "deferred" if deferred else "unknown"
        else:
            if language == "dart":
                while node.parent is not None and node.parent.type in {"argument_part", "selector"}:
                    node = node.parent
            invocation, operands, known = _operands(node)
            start = invocation.start_byte
            receiver = _receiver(invocation, path, data)
            if language == "dart" and invocation.type == "selector":
                start, receiver = _dart_prefix(invocation, path, data)
                if start == invocation.start_byte:
                    gaps.append("call_prefix_unavailable")
            text, raw = _source_text(data[start : invocation.end_byte])
            if raw is not None:
                gaps.append("source_encoding_unverified")
            arguments = tuple(
                _argument(operand, index, path, data) for index, operand in enumerate(operands)
            )
            if not known:
                gaps.append("argument_syntax_unavailable")
        if language in _IMPLICIT:
            gaps.append("parameter_mapping_unverified")
        if any(argument.kind == "unpacked" for argument in arguments):
            gaps.append("unpacked_argument_mapping_unknown")
        if root.has_error:
            gaps.append("source_parse_errors")
        site = CallSite(
            edge.source_id,
            language,
            location,
            digest,
            text,
            arguments,
            receiver,
            execution,
            tuple(gaps),
            raw,
        )
        if edge.kind == EdgeKind.REFERENCES:
            graph._parse_state.call_sites[site.id] = site
        else:
            graph.add_call_site(site)


def finalize_call_sites(graph: CodeGraph) -> None:
    """Publish staged formal applications only after linking establishes their call role."""
    calls = {(edge.source_id, edge.location) for edge in graph.edges if edge.kind == EdgeKind.CALLS}
    for site in graph._parse_state.call_sites.values():
        if (site.caller_id, site.location) in calls:
            graph.add_call_site(replace(site, execution="call"))
    graph._parse_state.call_sites.clear()
