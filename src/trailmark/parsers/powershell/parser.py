"""PowerShell declarations and conservative command graphs."""

from __future__ import annotations

import logging
import re
from collections import defaultdict
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
from trailmark.parsers._walk import walk_owned

_LOG = logging.getLogger(__name__)
_BRANCHES = {
    "if_statement",
    "elseif_clause",
    "switch_clause",
    "while_statement",
    "for_statement",
    "foreach_statement",
    "do_statement",
    "catch_clause",
}
_MUTATORS = {
    "set-alias",
    "new-alias",
    "remove-alias",
    "sal",
    "nal",
    "set-item",
    "new-item",
    "remove-item",
    "rename-item",
    "invoke-expression",
    "iex",
    "import-module",
    "using",
    "si",
    "ni",
    "ri",
    "rm",
    "rmdir",
    "rd",
    "del",
    "erase",
    "rni",
    "clear-item",
    "cli",
    "clsi",
    "move-item",
    "mi",
    "mv",
    "copy-item",
    "cpi",
    "cp",
}


def _parameters(node: Node) -> tuple[Parameter, ...]:
    result = []
    boundaries = frozenset(
        {"script_block_expression", "function_statement", "class_method_definition"}
    )
    for item in walk_owned(node, boundaries=boundaries):
        if item.type not in {"script_parameter", "class_method_parameter"}:
            continue
        name = first_child_type(item, {"variable"})
        if name is None:
            continue
        annotation = next(
            (
                c
                for c in walk_owned(item, boundaries=boundaries | {"script_parameter_default"})
                if c.type == "type_literal"
            ),
            None,
        )
        default = first_child_type(item, {"script_parameter_default"})
        result.append(
            Parameter(
                node_text(name),
                TypeRef(node_text(annotation).strip("[]")) if annotation else None,
                node_text(default).lstrip("= ") if default else None,
            )
        )
    return tuple(result)


def _literal(node: Node) -> str | None:
    raw = node_text(node)
    if node.type == "generic_token" and re.fullmatch(r"[\w./\\:-]+", raw):
        return raw
    if len(raw) >= 2 and raw[0] == raw[-1] == "'":
        return raw[1:-1].replace("''", "'")
    if len(raw) >= 2 and raw[0] == raw[-1] == '"' and "$" not in raw and "`" not in raw:
        return raw[1:-1]
    return None


class PowerShellParser:
    """Parse scripts/modules, functions, filters, classes and script blocks."""

    @property
    def language(self) -> str:
        return "powershell"

    def __init__(self) -> None:
        self._parser = Parser(get_language("powershell"))

    def parse_directory(self, dir_path: str) -> CodeGraph:
        return parse_directory(self.parse_file, "powershell", dir_path, (".ps1", ".psm1"))

    def parse_file(self, file_path: str) -> CodeGraph:
        root = self._parser.parse(Path(file_path).read_bytes()).root_node
        if root.has_error:
            _LOG.warning("PowerShell syntax errors in %s; graph may be incomplete", file_path)
        module = module_id_from_path(file_path)
        graph = CodeGraph(language=self.language, root_path=file_path)
        add_module_node(root, file_path, module, graph)
        bindings: dict[tuple[str, str], list[tuple[str, Node, bool]]] = defaultdict(list)
        parents: dict[str, str] = {}
        calls: list[tuple[str, Node]] = []
        mutated = False
        script = f"{module}:<script>"

        def unit(
            node: Node,
            owner: str,
            name: str,
            kind: NodeKind,
            body: Node,
            params: tuple[Parameter, ...] = (),
            return_type: TypeRef | None = None,
            *,
            header: Node | None = None,
        ) -> str:
            ident = f"{owner}:{name}" if owner == module else f"{owner}.{name}"
            if ident in graph.nodes:
                ident += f"@{node.start_byte}"
            parents[ident] = owner
            graph.nodes[ident] = CodeUnit(
                id=ident,
                name=name,
                kind=kind,
                location=make_location(node, file_path),
                parameters=params,
                return_type=return_type,
            )
            add_contains_edge(graph, owner, ident)
            branches: list[BranchInfo] = []

            def scan(current: Node) -> None:
                nonlocal mutated
                if current.type in {"function_statement", "class_method_definition"}:
                    method = current.type == "class_method_definition"
                    name_node = first_child_type(
                        current, {"simple_name"} if method else {"function_name"}
                    )
                    block = first_child_type(current, {"script_block"}) or current
                    if name_node is None:
                        return
                    raw = node_text(name_node)
                    header = first_child_type(
                        current, {"function_parameter_declaration", "class_method_parameter_list"}
                    )
                    param_block = first_child_type(block, {"param_block"})
                    parameter_source = header if header is not None else param_block
                    annotation = first_child_type(current, {"type_literal"}) if method else None
                    child_owner = module if ident == script else ident
                    child_id = unit(
                        current,
                        child_owner,
                        raw.replace(":", "."),
                        NodeKind.METHOD if method else NodeKind.FUNCTION,
                        block,
                        _parameters(parameter_source) if parameter_source is not None else (),
                        TypeRef(node_text(annotation).strip("[]")) if annotation else None,
                        header=header,
                    )
                    parent = current.parent
                    direct = (
                        parent is not None
                        and parent.type == "statement_list"
                        and parent.parent is not None
                        and parent.parent.type in {"program", "script_block_body"}
                    )
                    if not method:
                        bindings[child_owner, raw.casefold()].append((child_id, current, direct))
                    return
                if current.type == "class_statement":
                    name_node = first_child_type(current, {"simple_name"})
                    if name_node is not None:
                        unit(
                            current,
                            module if ident == script else ident,
                            node_text(name_node),
                            NodeKind.CLASS,
                            current,
                        )
                    return
                if current.type == "script_block_expression":
                    block = first_child_type(current, {"script_block"})
                    if block is not None:
                        param_block = first_child_type(
                            current, {"param_block"}
                        ) or first_child_type(block, {"param_block"})
                        unit(
                            current,
                            ident,
                            f"<lambda>@{current.start_byte}",
                            NodeKind.FUNCTION,
                            block,
                            _parameters(param_block) if param_block else (),
                            header=param_block
                            if param_block is not None and param_block.parent == current
                            else None,
                        )
                    return
                if current.type in _BRANCHES:
                    condition = current.child_by_field_name("condition")
                    branches.append(
                        BranchInfo(
                            make_location(current, file_path),
                            node_text(condition) if condition else current.type,
                        )
                    )
                if current.type in {"command", "invokation_expression"}:
                    calls.append((ident, current))
                    if current.type == "command":
                        name_node = current.child_by_field_name("command_name")
                        raw = node_text(name_node).casefold() if name_node else ""
                        operator = first_child_type(current, {"command_invokation_operator"})
                        if raw.rsplit("\\", 1)[-1] in _MUTATORS or (
                            operator is not None and node_text(operator) == "."
                        ):
                            mutated = True
                for child in current.named_children:
                    scan(child)

            if header is not None:
                scan(header)
            if kind == NodeKind.CLASS or body == node and node.type != "program":
                for child in body.named_children:
                    scan(child)
            else:
                scan(body)
            if kind != NodeKind.CLASS:
                graph.nodes[ident] = replace(
                    graph.nodes[ident],
                    branches=tuple(branches),
                    cyclomatic_complexity=1 + len(branches),
                )
            return ident

        script_parameters = first_child_type(root, {"param_block"})
        unit(
            root,
            module,
            "<script>",
            NodeKind.FUNCTION,
            root,
            _parameters(script_parameters) if script_parameters is not None else (),
        )
        for owner, call in calls:
            name_node = call.child_by_field_name("command_name")
            raw = (
                node_text(name_node) if name_node is not None else node_text(call).split("(", 1)[0]
            )
            target = None
            if (
                not mutated
                and name_node is not None
                and name_node.type == "command_name"
                and re.fullmatch(r"[\w-]+", raw)
            ):
                scope = module if owner == script else owner
                while scope:
                    candidates = bindings.get((scope, raw.casefold()), [])
                    if candidates:
                        if (
                            len(candidates) == 1
                            and candidates[0][2]
                            and (owner != script or candidates[0][1].end_byte <= call.start_byte)
                        ):
                            target = candidates[0][0]
                        break
                    scope = parents.get(scope, "")
            if (
                name_node is not None
                and raw.casefold() in {"import-module", "using"}
                and not bindings.get((module, raw.casefold()))
            ):
                elements = call.child_by_field_name("command_elements")
                args = (
                    [c for c in elements.named_children if c.type != "command_argument_sep"]
                    if elements
                    else []
                )
                if raw.casefold() == "using" and args and node_text(args[0]).casefold() == "module":
                    args = args[1:]
                elif raw.casefold() == "using":
                    args = []
                if args and node_text(args[0]).casefold() == "-name":
                    args = args[1:]
                dependency = _literal(args[0]) if args else None
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
                    owner,
                    target or f"{owner}.<dynamic>.{raw}",
                    EdgeKind.CALLS,
                    confidence=EdgeConfidence.INFERRED if target else EdgeConfidence.UNCERTAIN,
                    location=make_location(call, file_path),
                )
            )
        return graph
