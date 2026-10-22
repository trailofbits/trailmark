"""R functions, closures and conservative environment lookup."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

from tree_sitter import Node, Parser
from tree_sitter_language_pack import get_language

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import BranchInfo, CodeUnit, NodeKind, Parameter
from trailmark.parsers._common import (
    add_contains_edge,
    add_module_node,
    make_location,
    module_id_from_path,
    node_text,
    parse_directory,
)

_LOG = logging.getLogger(__name__)
_BRANCHES = {"if_statement", "for_statement", "while_statement", "repeat_statement"}
_MUTATORS = {
    "assign",
    "rm",
    "remove",
    "load",
    "source",
    "sys.source",
    "eval",
    "evalq",
    "local",
    "with",
    "within",
}


def _name(node: Node) -> str:
    return node_text(node).strip("`")


def _literal(node: Node) -> str | None:
    content = node.child_by_field_name("content")
    if node.type == "string" and content is not None and "\\" not in node_text(content):
        return node_text(content)
    return None


class RParser:
    """Extract R source without evaluating promises or package environments."""

    @property
    def language(self) -> str:
        return "r"

    def __init__(self) -> None:
        self._parser = Parser(get_language("r"))

    def parse_directory(self, dir_path: str) -> CodeGraph:
        return parse_directory(self.parse_file, "r", dir_path, (".r",))

    def parse_file(self, file_path: str) -> CodeGraph:
        root = self._parser.parse(Path(file_path).read_bytes()).root_node
        if root.has_error:
            _LOG.warning("R syntax errors in %s; graph may be incomplete", file_path)
        module = module_id_from_path(file_path)
        graph = CodeGraph(language=self.language, root_path=file_path)
        add_module_node(root, file_path, module, graph)
        script = f"{module}:<script>"
        parents: dict[str, str] = {}
        bindings: dict[tuple[str, str], list[tuple[str | None, int]]] = defaultdict(list)
        calls: list[tuple[str, str, Node]] = []
        branches: dict[str, list[BranchInfo]] = defaultdict(list)
        mutated = False

        def function(node: Node, scope: str, caller: str, name: str | None = None) -> str:
            raw = name or f"<lambda>@{node.start_byte}"
            owner = module if caller == script and name is not None else caller
            ident = f"{owner}:{raw}" if owner == module else f"{owner}.{raw}"
            if ident in graph.nodes:
                ident += f"@{node.start_byte}"
            parents[ident] = scope
            params_node = node.child_by_field_name("parameters")
            params = []
            if params_node is not None:
                for parameter in params_node.named_children:
                    name_node = parameter.child_by_field_name("name")
                    if name_node is not None:
                        default = parameter.child_by_field_name("default")
                        name_text = _name(name_node)
                        params.append(
                            Parameter(name_text, default=node_text(default) if default else None)
                        )
                        bindings[ident, name_text].append((None, node.start_byte))
            graph.nodes[ident] = CodeUnit(
                id=ident,
                name=raw,
                kind=NodeKind.FUNCTION,
                location=make_location(node, file_path),
                parameters=tuple(params),
            )
            add_contains_edge(graph, owner, ident)
            if params_node is not None:
                for parameter in params_node.named_children:
                    default = parameter.child_by_field_name("default")
                    if default is not None:
                        scan(default, ident, ident)
            body = node.child_by_field_name("body")
            if body is not None:
                scan(body, ident, ident)
            return ident

        def scan(node: Node, scope: str, caller: str) -> None:
            nonlocal mutated
            if node.type == "binary_operator":
                lhs = node.child_by_field_name("lhs")
                rhs = node.child_by_field_name("rhs")
                operator = node.child_by_field_name("operator")
                op = (
                    node_text(operator)
                    if operator is not None
                    else next((node_text(c) for c in node.children if not c.is_named), "")
                )
                if op in {"<-", "=", "<<-", "->", "->>"} and lhs is not None and rhs is not None:
                    target, value = (rhs, lhs) if op in {"->", "->>"} else (lhs, rhs)
                    while (
                        value.type == "parenthesized_expression" and len(value.named_children) == 1
                    ):
                        value = value.named_children[0]
                    if op in {"<<-", "->>"}:
                        mutated = True
                    if target.type == "identifier":
                        ident = (
                            function(value, scope, caller, _name(target))
                            if value.type == "function_definition"
                            else None
                        )
                        bindings[scope, _name(target)].append((ident, node.end_byte))
                        if ident is None:
                            scan(value, scope, caller)
                        return
            if node.type == "function_definition":
                function(node, scope, caller)
                return
            if node.type in _BRANCHES:
                condition = node.child_by_field_name("condition")
                branches[caller].append(
                    BranchInfo(
                        make_location(node, file_path),
                        node_text(condition) if condition else node.type,
                    )
                )
                if node.type == "for_statement":
                    variable = node.child_by_field_name("variable")
                    if variable is not None:
                        bindings[scope, _name(variable)].append((None, node.start_byte))
            if node.type == "call":
                calls.append((caller, scope, node))
                callee = node.child_by_field_name("function")
                raw = _name(callee) if callee is not None else ""
                builtin = raw.removeprefix("base:::").removeprefix("base::")
                if (
                    callee is not None
                    and (callee.type == "identifier" or raw.startswith(("base::", "base:::")))
                    and builtin in _MUTATORS
                ):
                    mutated = True
                if builtin in {"quote", "expression", "alist", "substitute"}:
                    lookup = scope
                    shadowed = False
                    while lookup:
                        if bindings.get((lookup, raw)):
                            shadowed = True
                            break
                        lookup = parents.get(lookup, "")
                    if raw.startswith(("base::", "base:::")) or (
                        callee is not None and callee.type == "identifier" and not shadowed
                    ):
                        return
            for child in node.named_children:
                scan(child, scope, caller)

        graph.nodes[script] = CodeUnit(
            id=script,
            name="<script>",
            kind=NodeKind.FUNCTION,
            location=make_location(root, file_path),
        )
        add_contains_edge(graph, module, script)
        scan(root, module, script)
        for ident, unit in list(graph.nodes.items()):
            if unit.kind == NodeKind.FUNCTION:
                owned = branches[ident]
                graph.nodes[ident] = replace(
                    unit, branches=tuple(owned), cyclomatic_complexity=1 + len(owned)
                )
        for caller, scope, call in calls:
            callee = call.child_by_field_name("function")
            if callee is None:
                continue
            raw = _name(callee)
            candidates: list[tuple[str | None, int]] = []
            if callee.type == "identifier":
                while scope:
                    candidates = bindings.get((scope, raw), [])
                    if candidates:
                        break
                    scope = parents.get(scope, "")
            target = None
            if not mutated and len(candidates) == 1:
                candidate, available = candidates[0]
                if (scope != caller and caller != script) or available <= call.start_byte:
                    target = candidate
            if (
                callee.type == "identifier"
                and not candidates
                and raw in {"library", "require", "source"}
            ):
                arguments = call.child_by_field_name("arguments")
                args = (
                    [c for c in arguments.named_children if c.type == "argument"]
                    if arguments
                    else []
                )
                values = {
                    node_text(key): node_text(value)
                    for a in args
                    if (key := a.child_by_field_name("name")) is not None
                    and (value := a.child_by_field_name("value")) is not None
                }
                value = args[0].child_by_field_name("value") if args else None
                dependency = _literal(value) if value is not None else None
                if (
                    raw != "source"
                    and value is not None
                    and value.type == "identifier"
                    and values.get("character.only", "FALSE") == "FALSE"
                ):
                    dependency = _name(value)
                if dependency and dependency not in graph.dependencies:
                    graph.dependencies.append(dependency)
                    graph.edges.append(
                        CodeEdge(
                            module,
                            dependency,
                            EdgeKind.IMPORTS,
                            location=make_location(call, file_path),
                        )
                    )
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
