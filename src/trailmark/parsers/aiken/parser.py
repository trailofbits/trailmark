"""Aiken functions, validator handlers, and lexical direct calls."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

from tree_sitter import Node, Parser
from tree_sitter_language_pack import get_language

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import BranchInfo, CodeUnit, NodeKind, Parameter, TypeParameter, TypeRef
from trailmark.parsers._common import (
    add_contains_edge,
    add_module_node,
    compute_complexity,
    make_location,
    module_id_from_path,
    node_text,
    parse_directory,
)
from trailmark.parsers._simple import descendants, first_child_type

_LOG = logging.getLogger(__name__)
_HANDLERS = frozenset({"mint", "spend", "withdraw", "publish", "vote", "propose", "else"})


class AikenParser:
    """Parse `.ak` declarations without evaluating Plutus or higher-order calls."""

    @property
    def language(self) -> str:
        return "aiken"

    def __init__(self) -> None:
        self._parser = Parser(get_language("aiken"))

    def parse_file(self, file_path: str) -> CodeGraph:
        root = self._parser.parse(Path(file_path).read_bytes()).root_node
        if root.has_error:
            _LOG.warning("Aiken syntax errors in %s; graph may be incomplete", file_path)
        module = module_id_from_path(file_path)
        graph = CodeGraph(language="aiken", root_path=file_path)
        add_module_node(root, file_path, module, graph)
        extractor = _Extractor(graph, module, file_path)
        extractor.declarations(root, module)
        extractor.calls()
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        return parse_directory(self.parse_file, "aiken", dir_path, (".ak",))


def _parameters(node: Node) -> tuple[Parameter, ...]:
    params = first_child_type(node, {"function_arguments"})
    result: list[Parameter] = []
    if params is not None:
        for item in params.named_children:
            names = [child for child in item.named_children if child.type == "identifier"]
            name = names[-1] if names else None
            type_node = first_child_type(item, {"type_definition", "function_type", "tuple_type"})
            if name is not None:
                result.append(
                    Parameter(node_text(name), TypeRef(node_text(type_node)) if type_node else None)
                )
    return tuple(result)


def _owned_nodes(root: Node) -> Iterator[Node]:
    stack = list(reversed(root.named_children))
    while stack:
        child = stack.pop()
        yield child
        if child.type != "function":
            stack.extend(reversed(child.named_children))


class _Extractor:
    def __init__(self, graph: CodeGraph, module: str, path: str) -> None:
        self.graph = graph
        self.module = module
        self.path = path
        self.bindings: dict[str, dict[str, str | None]] = {module: {}}
        self.parents: dict[str, str] = {}
        self.bodies: list[tuple[str, list[Node]]] = []

    def child_id(self, owner: str, name: str) -> str:
        return f"{owner}:{name}" if owner == self.module else f"{owner}.{name}"

    def declarations(self, root: Node, owner: str) -> None:
        for node in root.named_children:
            if node.type == "import":
                self.import_node(node)
            elif node.type in {"function", "test", "validator_hook", "validator_fallback"}:
                self.function(node, owner)
            elif node.type == "validator":
                name = first_child_type(node, {"identifier"})
                if name is None:
                    continue
                ident = self.child_id(owner, node_text(name))
                params = _parameters(node)
                self.graph.nodes[ident] = CodeUnit(
                    id=ident,
                    name=node_text(name),
                    kind=NodeKind.CONTRACT,
                    location=make_location(node, self.path),
                    parameters=params,
                    attributes=(("aiken_validator", True),),
                )
                self.bindings[owner][node_text(name)] = ident
                self.bindings[ident] = dict.fromkeys(p.name for p in params)
                self.parents[ident] = owner
                add_contains_edge(self.graph, owner, ident)
                self.declarations(node, ident)
            elif node.type in {"type_struct", "type_enum", "type_alias"}:
                definition = next(
                    (n for n in descendants(node) if n.type == "type_definition"), None
                )
                name = (
                    first_child_type(definition, {"type_identifier"})
                    if definition is not None
                    else None
                )
                if name is None or definition is None:
                    continue
                ident = self.child_id(owner, node_text(name))
                generics = []
                for child in definition.named_children:
                    if child.type == "type_argument":
                        generics.append(TypeParameter(node_text(child)))
                self.graph.nodes[ident] = CodeUnit(
                    id=ident,
                    name=node_text(name),
                    kind=NodeKind.ENUM if node.type == "type_enum" else NodeKind.STRUCT,
                    location=make_location(node, self.path),
                    type_parameters=tuple(generics),
                    attributes=(("aiken_type_kind", node.type.removeprefix("type_")),),
                )
                add_contains_edge(self.graph, owner, ident)

    def import_node(self, node: Node) -> None:
        module = node.child_by_field_name("module")
        if module is None:
            return
        target = node_text(module)
        self.graph.dependencies.append(target)
        self.graph.edges.append(
            CodeEdge(self.module, target, EdgeKind.IMPORTS, location=make_location(node, self.path))
        )
        alias = first_child_type(node, {"identifier"})
        self.bindings[self.module][node_text(alias) if alias else target.rsplit("/", 1)[-1]] = None
        unqualified = node.child_by_field_name("unqualified")
        if unqualified is not None:
            for item in unqualified.named_children:
                name = item.child_by_field_name("alias") or item.child_by_field_name("name")
                if name is not None:
                    self.bindings[self.module][node_text(name)] = None

    def function(self, node: Node, owner: str, anonymous_name: str | None = None) -> None:
        name_node = first_child_type(node, {"identifier"})
        name = anonymous_name or (node_text(name_node) if name_node else "else")
        ident = self.child_id(owner, name)
        params = _parameters(node)
        bodies = [n for n in node.named_children if n.type == "expression"]
        owned = [n for body in bodies for n in _owned_nodes(body)]
        branches = [
            BranchInfo(make_location(n, self.path), node_text(n).split("{", 1)[0])
            for n in owned
            if n.type in {"if", "when_case", "and", "or"}
        ]
        is_handler = node.type in {"validator_hook", "validator_fallback"} and name in _HANDLERS
        result = first_child_type(node, {"type_definition", "function_type", "tuple_type"})
        attributes = (
            (("aiken_handler", name),)
            if is_handler
            else (("aiken_test", True),)
            if node.type == "test"
            else ()
        )
        self.graph.nodes[ident] = CodeUnit(
            id=ident,
            name=name,
            kind=NodeKind.METHOD
            if self.graph.nodes[owner].kind == NodeKind.CONTRACT
            else NodeKind.FUNCTION,
            location=make_location(node, self.path),
            parameters=params,
            return_type=TypeRef(node_text(result))
            if result
            else TypeRef("Bool")
            if is_handler
            else None,
            cyclomatic_complexity=compute_complexity(branches),
            branches=tuple(branches),
            attributes=attributes,
        )
        add_contains_edge(self.graph, owner, ident)
        if anonymous_name is None:
            self.bindings[owner][name] = ident
        self.parents[ident] = owner
        self.bindings[ident] = dict.fromkeys(p.name for p in params)
        for child in owned:
            if (
                child.type in {"let_assignment", "expect_assignment", "when_case"}
                and child.named_children
            ):
                pattern = child.named_children[0]
                for binding in [pattern, *descendants(pattern)]:
                    if binding.type == "identifier":
                        self.bindings[ident][node_text(binding)] = None
        self.bodies.append((ident, bodies))
        for child in owned:
            if child.type == "function":
                self.function(child, ident, f"<lambda>@{child.start_byte}")

    def resolve(self, source: str, raw: str) -> tuple[str | None, bool]:
        first, *rest = raw.split(".")
        scope = source
        while True:
            if first in self.bindings[scope]:
                bound = self.bindings[scope][first]
                target = ".".join([bound, *rest]) if bound else None
                unit = self.graph.nodes.get(target) if target else None
                return (
                    target if unit and unit.kind in {NodeKind.FUNCTION, NodeKind.METHOD} else None,
                    True,
                )
            if scope == self.module:
                return None, False
            scope = self.parents[scope]

    def calls(self) -> None:
        for ident, bodies in self.bodies:
            for body in bodies:
                for node in [body, *_owned_nodes(body)]:
                    callee = None
                    if node.type == "call" and node.named_children:
                        callee = node.named_children[0]
                        arguments = first_child_type(node, {"call_arguments"})
                        capture = arguments is not None and any(
                            node_text(argument).strip() == "_"
                            for argument in arguments.named_children
                        )
                        parent = node.parent
                        while parent is not None and parent.type == "expression":
                            parent = parent.parent
                        if capture and (parent is None or parent.type != "pipeline"):
                            continue
                    elif node.type == "pipeline" and node.named_children:
                        rhs = node.named_children[-1]
                        while rhs.type == "expression" and len(rhs.named_children) == 1:
                            rhs = rhs.named_children[0]
                        if rhs.type in {"identifier", "field_access"}:
                            callee = rhs
                    if callee is None or callee.type not in {"identifier", "field_access"}:
                        continue
                    raw = node_text(callee)
                    target, bound = self.resolve(ident, raw)
                    unresolved = f"{ident}.<dynamic>.{raw}" if bound and target is None else raw
                    self.graph.edges.append(
                        CodeEdge(
                            ident,
                            target or unresolved,
                            EdgeKind.CALLS,
                            confidence=EdgeConfidence.CERTAIN
                            if target
                            else EdgeConfidence.UNCERTAIN,
                            location=make_location(node, self.path),
                        )
                    )
