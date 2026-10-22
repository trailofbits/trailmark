"""VB.NET declarations, overloads and conservative method binding."""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import replace
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
_TYPES = {
    "namespace_block": NodeKind.NAMESPACE,
    "module_block": NodeKind.CLASS,
    "class_block": NodeKind.CLASS,
    "structure_block": NodeKind.STRUCT,
    "interface_block": NodeKind.INTERFACE,
    "enum_block": NodeKind.ENUM,
}
_BRANCHES = {
    "if_statement",
    "elseif_clause",
    "for_statement",
    "for_each_statement",
    "while_statement",
    "do_statement",
    "case_clause",
    "catch_block",
    "conditional_expression",
}


def _name(node: Node) -> str:
    return node_text(node).strip("[]")


def _parameters(node: Node) -> tuple[Parameter, ...]:
    parameters = node.child_by_field_name("parameters")
    items = parameters.named_children if parameters is not None else node.named_children
    result = []
    for item in items:
        if item.type not in {"parameter", "lambda_parameter"}:
            continue
        name = item.child_by_field_name("name")
        clause = first_child_type(item, {"as_clause"})
        annotation = clause.child_by_field_name("type") if clause is not None else None
        default = item.child_by_field_name("default_value")
        if name is not None:
            result.append(
                Parameter(
                    _name(name),
                    TypeRef(node_text(annotation)) if annotation else None,
                    node_text(default) if default else None,
                )
            )
    return tuple(result)


class VisualBasicParser:
    """Parse VB.NET source; no VB6/VBA, compiler binding or generated code."""

    @property
    def language(self) -> str:
        return "vb"

    def __init__(self) -> None:
        self._parser = Parser(get_language("vb"))

    def parse_directory(self, dir_path: str) -> CodeGraph:
        return parse_directory(self.parse_file, "vb", dir_path, (".vb",))

    def parse_file(self, file_path: str) -> CodeGraph:
        root = self._parser.parse(Path(file_path).read_bytes()).root_node
        if root.has_error:
            _LOG.warning("VB.NET syntax errors in %s; graph may be incomplete", file_path)
        module = module_id_from_path(file_path)
        graph = CodeGraph(language=self.language, root_path=file_path)
        add_module_node(root, file_path, module, graph)
        declarations: list[tuple[Node, str, str]] = []
        methods: dict[tuple[str, str], list[str]] = defaultdict(list)
        containers: dict[str, set[str]] = defaultdict(set)
        calls: list[tuple[str, str, Node, Node, set[str], bool]] = []

        def child_id(owner: str, name: str) -> str:
            return f"{owner}:{name}" if owner == module else f"{owner}.{name}"

        def collect(node: Node, owner: str) -> None:
            if node.type in _TYPES:
                name_node = node.child_by_field_name("name")
                if name_node is None:
                    return
                name = _name(name_node)
                ident = child_id(owner, name)
                if ident in graph.nodes:
                    ident += f"@{node.start_byte}"
                graph.nodes[ident] = CodeUnit(
                    id=ident,
                    name=name,
                    kind=_TYPES[node.type],
                    location=make_location(node, file_path),
                    attributes=(("vb_module", node.type == "module_block"),),
                )
                add_contains_edge(graph, owner, ident)
                if node.type != "namespace_block":
                    containers[name.casefold()].add(ident)
                    containers[ident.split(":", 1)[-1].casefold()].add(ident)
                for child in node.named_children:
                    collect(child, ident)
                return
            if node.type in {"method_declaration", "constructor_declaration"}:
                name_node = node.child_by_field_name("name")
                declarations.append((node, owner, _name(name_node) if name_node else "New"))
                return
            if node.type == "property_declaration":
                name_node = node.child_by_field_name("name")
                if name_node is not None:
                    for accessor in node.named_children:
                        if accessor.type in {"get_accessor", "set_accessor"}:
                            declarations.append(
                                (
                                    accessor,
                                    owner,
                                    f"{_name(name_node)}.{accessor.type.split('_')[0]}",
                                )
                            )
                return
            if node.type == "imports_statement":
                namespace = node.child_by_field_name("namespace")
                if namespace is not None:
                    dependency = node_text(namespace)
                    if dependency not in graph.dependencies:
                        graph.dependencies.append(dependency)
                        graph.edges.append(
                            CodeEdge(
                                module,
                                dependency,
                                EdgeKind.IMPORTS,
                                location=make_location(node, file_path),
                            )
                        )
                return
            for child in node.named_children:
                collect(child, owner)

        collect(root, module)
        counts = Counter((owner, name.casefold()) for _, owner, name in declarations)

        def function(
            node: Node,
            ident: str,
            owner: str,
            name: str,
            kind: NodeKind = NodeKind.METHOD,
            inherited_shadows: set[str] | None = None,
            inherited_static: bool = False,
        ) -> None:
            params = _parameters(node)
            annotation = node.child_by_field_name("return_type")
            return_type = TypeRef(node_text(annotation)) if annotation is not None else None
            modifiers = node.child_by_field_name("modifiers")
            flags = node_text(modifiers).casefold().split() if modifiers is not None else []
            is_static = (
                inherited_static
                or bool(dict(graph.nodes[owner].attributes).get("vb_module"))
                or "shared" in flags
            )
            valid_args = (
                not params
                or len(params) == 1
                and params[0].type_ref is not None
                and params[0].type_ref.name.replace(" ", "").casefold()
                in {"string()", "system.string()"}
            )
            valid_return = return_type is None or return_type.name.casefold() in {
                "integer",
                "system.int32",
            }
            main = (
                kind == NodeKind.METHOD
                and name.casefold() == "main"
                and is_static
                and valid_args
                and valid_return
                and "async" not in flags
                and not node.has_error
            )
            shadows = set(inherited_shadows or ()) | {p.name.casefold() for p in params}
            pending = list(node.named_children)
            while pending:
                current = pending.pop()
                if current.type == "lambda_expression":
                    continue
                if current.type in {
                    "dim_statement",
                    "variable_declarator",
                    "for_statement",
                    "for_each_statement",
                }:
                    shadows.update(
                        _name(n).casefold() for n in current.children_by_field_name("name")
                    )
                pending.extend(current.named_children)
            branches = []

            def scan(current: Node) -> None:
                if current.type == "lambda_expression":
                    lambda_name = f"<lambda>@{current.start_byte}"
                    function(
                        current,
                        f"{ident}.{lambda_name}",
                        ident,
                        lambda_name,
                        NodeKind.FUNCTION,
                        shadows,
                        is_static,
                    )
                    return
                if current.type in {"dim_statement", "for_statement", "for_each_statement"}:
                    name_node = current.child_by_field_name("name")
                    if name_node is not None:
                        shadows.add(_name(name_node).casefold())
                if current.type in _BRANCHES:
                    condition = current.child_by_field_name("condition")
                    branches.append(
                        BranchInfo(
                            make_location(current, file_path),
                            node_text(condition) if condition else current.type,
                        )
                    )
                target = (
                    current.child_by_field_name("target") if current.type == "invocation" else None
                )
                if current.type == "call_statement":
                    expression = first_child_type(current, {"expression"})
                    if (
                        expression is not None
                        and len(expression.named_children) == 1
                        and expression.named_children[0].type in {"identifier", "member_access"}
                    ):
                        target = expression.named_children[0]
                if target is not None:
                    calls.append(
                        (
                            ident,
                            owner if kind == NodeKind.METHOD else lambda_container,
                            current,
                            target,
                            shadows,
                            is_static,
                        )
                    )
                for child in current.named_children:
                    scan(child)

            lambda_container = owner
            if kind == NodeKind.FUNCTION:
                lambda_container = callable_owners.get(owner, owner)
            callable_owners[ident] = owner if kind == NodeKind.METHOD else lambda_container
            graph.nodes[ident] = CodeUnit(
                id=ident,
                name=name,
                kind=kind,
                location=make_location(node, file_path),
                parameters=params,
                return_type=return_type,
                attributes=(("vb_shared", is_static), ("vb_main", main)),
            )
            add_contains_edge(graph, owner, ident)
            for child in node.named_children:
                scan(child)

            graph.nodes[ident] = replace(
                graph.nodes[ident],
                branches=tuple(branches),
                cyclomatic_complexity=1 + len(branches),
            )

        callable_owners: dict[str, str] = {}
        for node, owner, name in declarations:
            ident = child_id(owner, name)
            if counts[owner, name.casefold()] > 1 or ident in graph.nodes:
                ident += f"@{node.start_point.row + 1}_{node.start_point.column}"
            function(node, ident, owner, name)
            methods[owner, name.casefold()].append(ident)
        for caller, container, site, callee, shadows, is_static in calls:
            raw = node_text(callee)
            candidates: list[str] = []
            if callee.type == "identifier" and _name(callee).casefold() not in shadows:
                candidates = methods.get((container, _name(callee).casefold()), [])
                if is_static:
                    candidates = [
                        ident
                        for ident in candidates
                        if dict(graph.nodes[ident].attributes).get("vb_shared")
                    ]
            elif callee.type == "member_access":
                receiver = callee.child_by_field_name("object")
                member = callee.child_by_field_name("member")
                receiver_name = node_text(receiver).casefold() if receiver is not None else ""
                owners = (
                    containers.get(receiver_name, set())
                    if receiver_name.split(".", 1)[0] not in shadows
                    else set()
                )
                if member is not None and len(owners) == 1:
                    candidates = [
                        ident
                        for ident in methods.get((next(iter(owners)), _name(member).casefold()), [])
                        if dict(graph.nodes[ident].attributes).get("vb_shared")
                    ]
            target = candidates[0] if len(candidates) == 1 and not root.has_error else None
            graph.edges.append(
                CodeEdge(
                    caller,
                    target or f"{caller}.<dynamic>.{raw}",
                    EdgeKind.CALLS,
                    confidence=EdgeConfidence.INFERRED if target else EdgeConfidence.UNCERTAIN,
                    location=make_location(site, file_path),
                )
            )
        return graph
