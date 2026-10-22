"""Julia declarations and syntax-based call graphs without dispatch inference."""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path

from tree_sitter import Node, Parser
from tree_sitter_language_pack import get_language

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import BranchInfo, CodeUnit, NodeKind, Parameter, TypeRef
from trailmark.parsers._common import (
    add_contains_edge,
    add_module_node,
    make_location,
    module_id_from_path,
    node_text,
    parse_directory,
)
from trailmark.parsers._simple import first_child_type

_LOG = logging.getLogger(__name__)
_CONTAINERS = {
    "module_definition": NodeKind.NAMESPACE,
    "struct_definition": NodeKind.STRUCT,
    "abstract_definition": NodeKind.INTERFACE,
    "primitive_definition": NodeKind.STRUCT,
}
_BRANCHES = {
    "if_statement",
    "elseif_clause",
    "for_statement",
    "while_statement",
    "catch_clause",
    "ternary_expression",
}


@dataclass(frozen=True)
class _Function:
    name: str
    parameters: tuple[Parameter, ...]
    body: Node | None
    defaults: tuple[Node, ...] = ()
    return_type: TypeRef | None = None
    binding_names: tuple[str, ...] = ()
    receiver: Parameter | None = None


def _parameters(
    items: list[Node],
) -> tuple[tuple[Parameter, ...], tuple[Node, ...], tuple[str, ...]]:
    result = []
    defaults = []
    bindings = []
    for item in items:
        default = None
        if item.type == "assignment":
            default = item.named_children[-1]
            defaults.append(default)
            item = item.named_children[0]
        bindings.extend(_bound_names(item))
        variadic = item.type == "splat_expression"
        if variadic and item.named_children:
            item = item.named_children[0]
        annotation = None
        if item.type == "unary_typed_expression" and item.named_children:
            result.append(Parameter("", TypeRef(node_text(item.named_children[0]))))
            continue
        if item.type == "typed_expression" and len(item.named_children) > 1:
            annotation = TypeRef(node_text(item.named_children[-1]))
            item = item.named_children[0]
        result.append(
            Parameter(
                node_text(item) + ("..." if variadic else ""),
                annotation,
                node_text(default) if default is not None else None,
            )
        )
    return tuple(result), tuple(defaults), tuple(bindings)


def _definition(node: Node) -> _Function | None:
    name = ""
    signature = None
    body = None
    if node.type == "function_definition":
        wrapper = first_child_type(node, {"signature"})
        signature = (
            wrapper.named_children[0] if wrapper is not None and wrapper.named_children else None
        )
        body = first_child_type(node, {"block"})
        if signature is not None and signature.type == "argument_list":
            params, defaults, bindings = _parameters(signature.named_children)
            return _Function(
                f"<lambda>@{node.start_byte}", params, body, defaults, binding_names=bindings
            )
    elif node.type == "assignment" and len(node.named_children) >= 2:
        signature = node.named_children[0]
        body = node.named_children[-1]
        if signature.type == "identifier" and body.type == "function_definition":
            definition = _definition(body)
            return replace(definition, name=node_text(signature)) if definition else None
        if signature.type == "identifier" and body.type == "arrow_function_expression":
            name = node_text(signature)
            signature = body
    elif node.type == "do_clause":
        body = first_child_type(node, {"block"})
        params, defaults, bindings = _parameters([c for c in node.named_children if c != body])
        return _Function(
            f"<lambda>@{node.start_byte}", params, body, defaults, binding_names=bindings
        )
    elif node.type == "arrow_function_expression":
        signature = node
    else:
        return None
    if signature is None:
        return None
    if signature.type == "arrow_function_expression":
        arguments = signature.named_children[0]
        items = arguments.named_children if arguments.type == "argument_list" else [arguments]
        params, defaults, bindings = _parameters(items)
        return _Function(
            name or f"<lambda>@{node.start_byte}",
            params,
            signature.named_children[-1],
            defaults,
            binding_names=bindings,
        )
    return_type = None
    while (
        signature.type in {"typed_expression", "where_expression", "parenthesized_expression"}
        and signature.named_children
    ):
        if signature.type == "typed_expression" and len(signature.named_children) > 1:
            return_type = TypeRef(node_text(signature.named_children[-1]))
        signature = signature.named_children[0]
    if signature.type != "call_expression" or not signature.named_children:
        return None
    callee = signature.named_children[0]
    receiver = None
    receiver_node = callee
    while receiver_node.type == "parenthesized_expression" and receiver_node.named_children:
        receiver_node = receiver_node.named_children[0]
    if receiver_node.type == "typed_expression" and len(receiver_node.named_children) == 2:
        receiver = Parameter(
            node_text(receiver_node.named_children[0]),
            TypeRef(node_text(receiver_node.named_children[1])),
        )
    elif callee.type not in {"identifier", "operator", "field_expression"}:
        return None
    arguments = first_child_type(signature, {"argument_list"})
    params, defaults, bindings = _parameters(arguments.named_children if arguments else [])
    if receiver is not None and receiver.type_ref is not None:
        return _Function(
            f"{receiver.type_ref.name}.__call__",
            (receiver, *params),
            body,
            defaults,
            return_type,
            (receiver.name, *bindings),
            receiver,
        )
    return _Function(node_text(callee), params, body, defaults, return_type, bindings)


def _transparent(node: Node) -> bool:
    macro = first_child_type(node, {"macro_identifier"})
    return macro is not None and node_text(macro) in {"@inline", "@noinline"}


def _owned(roots: list[Node]) -> Iterator[Node]:
    stack = list(reversed(roots))
    while stack:
        node = stack.pop()
        if node.type in {"quote_expression", "quote_statement"}:
            # Only interpolation at this quotation level runs while constructing Expr data.
            pending = [(child, 1) for child in reversed(node.named_children)]
            while pending:
                quoted, depth = pending.pop()
                if quoted.type in {"quote_expression", "quote_statement"}:
                    depth += 1
                elif quoted.type == "interpolation_expression":
                    depth -= 1
                    if depth == 0:
                        yield from _owned(quoted.named_children)
                        continue
                pending.extend((child, depth) for child in reversed(quoted.named_children))
            continue
        yield node
        if (
            node.type in _CONTAINERS
            or node.type == "macro_definition"
            or _definition(node) is not None
        ):
            continue
        if node.type == "macrocall_expression" and not _transparent(node):
            continue
        stack.extend(reversed(node.named_children))


def _definite(node: Node, roots: list[Node]) -> bool:
    """Only unconditional definitions can establish a unique local binding."""
    parent = node.parent
    while parent is not None and parent not in roots:
        if parent.type not in {"block", "source_file", "macro_argument_list"} and not (
            parent.type == "macrocall_expression" and _transparent(parent)
        ):
            return False
        parent = parent.parent
    return True


def _bound_names(node: Node) -> Iterator[str]:
    if node.type == "identifier":
        yield node_text(node)
    elif node.type in {"typed_expression", "splat_expression"} and node.named_children:
        yield from _bound_names(node.named_children[0])
    elif node.type in {"open_tuple", "tuple_expression", "argument_list"}:
        for child in node.named_children:
            yield from _bound_names(child)


class JuliaParser:
    """Parse Julia source without macro expansion or multiple-dispatch guessing."""

    @property
    def language(self) -> str:
        return "julia"

    def __init__(self) -> None:
        self._parser = Parser(get_language("julia"))

    def parse_directory(self, dir_path: str) -> CodeGraph:
        return parse_directory(self.parse_file, "julia", dir_path, (".jl",))

    def parse_file(self, file_path: str) -> CodeGraph:
        root = self._parser.parse(Path(file_path).read_bytes()).root_node
        if root.has_error:
            _LOG.warning("Julia syntax errors in %s; graph may be incomplete", file_path)
        module = module_id_from_path(file_path)
        graph = CodeGraph(language=self.language, root_path=file_path)
        add_module_node(root, file_path, module, graph)
        bindings: dict[tuple[str, str], list[str | None]] = defaultdict(list)
        parents: dict[str, str] = {}
        definition_ends: dict[str, int] = {}
        calls: list[tuple[str, str, Node]] = []
        includes: list[tuple[str, str, str, Node]] = []
        writes: list[tuple[str, str]] = []
        mutated = False

        def child_id(owner: str, name: str) -> str:
            return f"{owner}:{name}" if owner == module else f"{owner}.{name}"

        def dependency(name: str, site: Node) -> None:
            if name and name not in graph.dependencies:
                graph.dependencies.append(name)
                graph.edges.append(
                    CodeEdge(
                        module, name, EdgeKind.IMPORTS, location=make_location(site, file_path)
                    )
                )

        def scope(roots: list[Node], owner: str, caller: str | None) -> None:
            nonlocal mutated
            nodes = list(_owned(roots))
            definitions = [
                (node, definition)
                for node in nodes
                if (definition := _definition(node)) is not None
            ]
            counts = Counter(definition.name for _, definition in definitions)
            for node in nodes:
                if node.type not in _CONTAINERS:
                    continue
                name_node = node.child_by_field_name("name")
                if name_node is None:
                    head = first_child_type(node, {"type_head"})
                    if head is not None and head.named_children:
                        name_node = head.named_children[0]
                        if name_node.type == "parametrized_type_expression":
                            name_node = name_node.named_children[0]
                if name_node is None:
                    continue
                name = node_text(name_node)
                ident = child_id(owner, name)
                if ident in graph.nodes:
                    ident += f"@{node.start_byte}"
                graph.nodes[ident] = CodeUnit(
                    id=ident,
                    name=name,
                    kind=_CONTAINERS[node.type],
                    location=make_location(node, file_path),
                )
                add_contains_edge(graph, owner, ident)
                # Julia modules do not inherit the enclosing module's bindings.
                parents[ident] = "" if node.type == "module_definition" else owner
                bindings[owner, name].append(None)
                block = first_child_type(node, {"block"})
                if node.type == "module_definition":
                    script = child_id(ident, "<script>")
                    graph.nodes[script] = CodeUnit(
                        id=script,
                        name="<script>",
                        kind=NodeKind.FUNCTION,
                        location=make_location(node, file_path),
                    )
                    add_contains_edge(graph, ident, script)
                    if block is not None:
                        scope([block], ident, script)
                elif block is not None:
                    scope([block], ident, None)
            for node, definition in definitions:
                ident = child_id(owner, definition.name)
                if counts[definition.name] > 1 or ident in graph.nodes:
                    ident += f"@{node.start_point.row + 1}_{node.start_point.column}"
                graph.nodes[ident] = CodeUnit(
                    id=ident,
                    name=definition.name.rsplit(".", 1)[-1],
                    kind=NodeKind.METHOD if definition.receiver is not None else NodeKind.FUNCTION,
                    location=make_location(node, file_path),
                    attributes=(("julia_receiver_type", definition.receiver.type_ref.name),)
                    if definition.receiver is not None and definition.receiver.type_ref is not None
                    else (),
                    parameters=definition.parameters,
                    return_type=definition.return_type,
                )
                receiver_owner = owner
                if definition.receiver is not None and definition.receiver.type_ref is not None:
                    candidate = child_id(owner, definition.receiver.type_ref.name)
                    if candidate in graph.nodes:
                        receiver_owner = candidate
                add_contains_edge(graph, receiver_owner, ident)
                parents[ident] = owner
                definition_ends[ident] = node.end_byte
                if definition.receiver is None and not definition.name.startswith("<lambda>"):
                    bindings[owner, definition.name].append(ident)
                    if not _definite(node, roots):
                        bindings[owner, definition.name].append(None)
                for bound_name in definition.binding_names:
                    bindings[ident, bound_name].append(None)
                bodies = list(definition.defaults)
                if definition.body is not None:
                    bodies.append(definition.body)
                scope(bodies, ident, ident)
            branches = []
            for node in nodes:
                if (
                    _definition(node) is not None
                    or node.type in _CONTAINERS
                    or node.type == "macro_definition"
                ):
                    continue
                if node.type == "macrocall_expression" and not _transparent(node):
                    mutated = True
                    _LOG.warning(
                        "Julia macro expansion skipped in %s at line %s",
                        file_path,
                        node.start_point.row + 1,
                    )
                if node.type in _BRANCHES:
                    condition = node.child_by_field_name("condition")
                    branches.append(
                        BranchInfo(
                            make_location(node, file_path),
                            node_text(condition) if condition else node.type,
                        )
                    )
                if (
                    node.type in {"assignment", "compound_assignment_expression"}
                    and node.named_children
                    and (node.parent is None or node.parent.type != "argument_list")
                ):
                    for name in _bound_names(node.named_children[0]):
                        bindings[owner, name].append(None)
                        writes.append((owner, name))
                if node.type in {"for_binding", "let_binding"} and node.named_children:
                    for name in _bound_names(node.named_children[0]):
                        bindings[owner, name].append(None)
                if node.type == "local_statement":
                    for child in node.named_children:
                        for name in _bound_names(child):
                            bindings[owner, name].append(None)
                if node.type in {"catch_clause", "let_statement"}:
                    for child in node.named_children:
                        if child.type == "identifier":
                            bindings[owner, node_text(child)].append(None)
                if node.type in {"global_statement", "global_declaration"}:
                    mutated = True
                if node.type in {"using_statement", "import_statement"}:
                    for imported in node.named_children:
                        if imported.type == "selected_import" and imported.named_children:
                            dependency(node_text(imported.named_children[0]), node)
                            for name_node in imported.named_children[1:]:
                                if name_node.type == "import_alias":
                                    name_node = name_node.named_children[-1]
                                bindings[owner, node_text(name_node)].append(None)
                        elif imported.type == "import_alias" and imported.named_children:
                            dependency(node_text(imported.named_children[0]), node)
                            bindings[owner, node_text(imported.named_children[-1])].append(None)
                        else:
                            dependency(node_text(imported), node)
                if (
                    node.type in {"call_expression", "broadcast_call_expression"}
                    and node.named_children
                ):
                    callee = node.named_children[0]
                    raw = node_text(callee)
                    if raw in {
                        "include",
                        "include_string",
                        "eval",
                        "Core.eval",
                        "Base.eval",
                        "Base.include",
                    }:
                        mutated = True
                    if raw in {"include", "Base.include"}:
                        args = first_child_type(node, {"argument_list"})
                        string = (
                            args.named_children[-1]
                            if args is not None and args.named_children
                            else None
                        )
                        if (
                            string is not None
                            and string.type == "string_literal"
                            and all(
                                c.type == "content" and not c.named_children
                                for c in string.named_children
                            )
                        ):
                            includes.append(
                                (
                                    owner,
                                    raw,
                                    "".join(node_text(c) for c in string.named_children),
                                    node,
                                )
                            )
                    if caller is not None:
                        calls.append((caller, owner, node))
            if caller is not None:
                graph.nodes[caller] = replace(
                    graph.nodes[caller],
                    branches=tuple(branches),
                    cyclomatic_complexity=1 + len(branches),
                )

        script = f"{module}:<script>"
        graph.nodes[script] = CodeUnit(
            id=script,
            name="<script>",
            kind=NodeKind.FUNCTION,
            location=make_location(root, file_path),
        )
        add_contains_edge(graph, module, script)
        scope([root], module, script)
        for owner, raw, path, node in includes:
            current = owner
            shadowed = False
            while current:
                if bindings.get((current, raw.split(".", 1)[0])):
                    shadowed = True
                    break
                current = parents.get(current, "")
            if not shadowed:
                dependency(path, node)
        # Assignment in a closure can replace an enclosing function's local binding.
        for owner, name in writes:
            current = parents.get(owner, "")
            while current and graph.nodes[current].kind == NodeKind.FUNCTION:
                if bindings.get((current, name)):
                    bindings[current, name].append(None)
                current = parents.get(current, "")
        for caller, owner, call in calls:
            callee = call.named_children[0]
            raw = node_text(callee)
            candidates: list[str | None] = []
            if callee.type in {"identifier", "operator"}:
                current = owner
                while current:
                    candidates = bindings.get((current, raw), [])
                    if candidates:
                        break
                    current = parents.get(current, "")
            target = (
                candidates[0]
                if not mutated
                and not root.has_error
                and call.type == "call_expression"
                and len(candidates) == 1
                else None
            )
            if (
                target
                and graph.nodes[caller].name == "<script>"
                and definition_ends[target] > call.start_byte
            ):
                target = None
            graph.edges.append(
                CodeEdge(
                    caller,
                    target or f"{caller}.<dynamic>.{raw}",
                    EdgeKind.CALLS,
                    confidence=EdgeConfidence.INFERRED if target else EdgeConfidence.UNCERTAIN,
                    location=make_location(call, file_path),
                )
            )
        return graph
