"""Bash functions and command sites without executing shell code."""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from pathlib import Path

from tree_sitter import Node, Parser
from tree_sitter_language_pack import get_language

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import BranchInfo, CodeUnit, NodeKind
from trailmark.parsers._common import (
    add_contains_edge,
    add_module_node,
    make_location,
    module_id_from_path,
    node_text,
    parse_directory,
)

_LOG = logging.getLogger(__name__)
_BRANCHES = {
    "if_statement",
    "elif_clause",
    "while_statement",
    "for_statement",
    "c_style_for_statement",
    "case_item",
}


def _literal(node: Node) -> str | None:
    if node.type == "command_name" and len(node.named_children) == 1:
        return _literal(node.named_children[0])
    if node.type == "word":
        text = node_text(node)
        result = []
        index = 0
        while index < len(text):
            if text[index] == "\\" and index + 1 < len(text):
                index += 1
                if text[index] != "\n":
                    result.append(text[index])
            elif text[index] in "*?[]~":
                return None
            else:
                result.append(text[index])
            index += 1
        return "".join(result)
    if node.type == "raw_string":
        return node_text(node)[1:-1]
    if node.type == "string" and all(c.type == "string_content" for c in node.named_children):
        return re.sub(
            r'\\([$`"\\\n])',
            lambda match: "" if match[1] == "\n" else match[1],
            node_text(node)[1:-1],
        )
    if node.type == "concatenation":
        parts = [_literal(child) for child in node.named_children]
        return (
            "".join(part for part in parts if part is not None)
            if all(part is not None for part in parts)
            else None
        )
    return None


def _invocation(command: Node) -> tuple[str, list[Node], bool]:
    """Unwrap builtin selectors for effects, while retaining the actual call site."""
    name = command.child_by_field_name("name")
    raw = _literal(name) or "" if name is not None else ""
    args = list(command.children_by_field_name("argument"))
    forced_builtin = False
    # Only an unquoted reserved `time` token is syntax. `command time`,
    # quoted names and external /usr/bin/time remain ordinary commands.
    if name is not None and node_text(name) == "time" and args:
        if _literal(args[0]) == "-p":
            args.pop(0)
        raw = _literal(args.pop(0)) or "" if args else ""
    while raw in {"command", "builtin"} and args:
        wrapper = raw
        while args:
            flag = _literal(args[0])
            if flag == "--":
                args.pop(0)
                break
            if wrapper != "command" or not flag or not re.fullmatch(r"-[pVv]+", flag):
                break
            args.pop(0)
            # `command -v/-V` only inspects names; it does not invoke them.
            if "v" in flag or "V" in flag:
                return "", [], False
        if not args:
            break
        raw = _literal(args.pop(0)) or ""
        forced_builtin = True
    return raw, args, forced_builtin


class BashParser:
    """Extract shell functions, script execution, dependencies and command calls."""

    @property
    def language(self) -> str:
        return "bash"

    def __init__(self) -> None:
        self._parser = Parser(get_language("bash"))

    def parse_directory(self, dir_path: str) -> CodeGraph:
        return parse_directory(self.parse_file, "bash", dir_path, (".sh", ".bash"))

    def parse_file(self, file_path: str) -> CodeGraph:
        source = Path(file_path).read_bytes()
        root = self._parser.parse(source).root_node
        # The pinned grammar treats `coproc NAME {` as a simple command and
        # consumes its closing brace as the enclosing function's terminator.
        # Remove only the AST-identified reserved prefix, keeping every byte
        # position and the compound body's original source span intact.
        prefixes: list[tuple[int, int]] = []
        pending = [root]
        while pending:
            current = pending.pop()
            if current.type == "command":
                command_name = current.child_by_field_name("name")
                arguments = current.children_by_field_name("argument")
                if command_name is not None and node_text(command_name) == "coproc":
                    brace = next(
                        (argument for argument in arguments[:2] if node_text(argument) == "{"), None
                    )
                    if brace is not None:
                        prefixes.append((command_name.start_byte, brace.start_byte))
            pending.extend(current.named_children)
        if prefixes:
            normalized = bytearray(source)
            for start, end in prefixes:
                normalized[start:end] = bytes(
                    10 if byte == 10 else 32 for byte in normalized[start:end]
                )
            root = self._parser.parse(bytes(normalized)).root_node
        if root.has_error:
            _LOG.warning("Bash syntax errors in %s; graph may be incomplete", file_path)
        module = module_id_from_path(file_path)
        graph = CodeGraph(language="bash", root_path=file_path)
        add_module_node(root, file_path, module, graph)
        names: dict[str, list[tuple[str, int, bool]]] = defaultdict(list)
        commands: list[tuple[str, Node]] = []
        imports: list[tuple[str, Node, str, bool]] = []
        mutations: set[str] = set()
        dynamic_mutation = False

        def unit(node: Node, owner: str, name: str, body: Node) -> None:
            ident = f"{owner}:{name}" if owner == module else f"{owner}.{name}"
            if ident in graph.nodes:
                ident += f"@{node.start_byte}"
            branches: list[BranchInfo] = []
            if name != "<script>":
                names[name].append((ident, node.end_byte, node.parent == root))

            def scan(current: Node) -> None:
                nonlocal dynamic_mutation
                if current.type == "function_definition":
                    name_node = current.child_by_field_name("name")
                    function_body = current.child_by_field_name("body")
                    if name_node is not None and function_body is not None:
                        unit(
                            current,
                            module if name == "<script>" else ident,
                            node_text(name_node),
                            function_body,
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
                if current.type == "unset_command":
                    for child in current.named_children:
                        if child.type == "variable_name":
                            mutations.add(node_text(child))
                        elif child.type not in {"word"}:
                            dynamic_mutation = True
                if current.type == "command":
                    commands.append((ident, current))
                    raw, args, forced_builtin = _invocation(current)
                    if raw in {"eval", "source", "."}:
                        dynamic_mutation = True
                    if raw in {"alias", "unalias", "unset", "enable"}:
                        for arg in args:
                            literal = _literal(arg)
                            if literal is None:
                                dynamic_mutation = True
                            else:
                                mutations.add(literal.split("=", 1)[0])
                    if raw in {"source", "."} and args:
                        dependency = _literal(args[0])
                        if dependency:
                            imports.append((dependency, current, raw, forced_builtin))
                for child in current.named_children:
                    scan(child)

            scan(body)
            # Function-definition redirects execute when the function is called.
            # Their substitutions belong to that function, not the script.
            for redirect in node.children_by_field_name("redirect"):
                scan(redirect)
            graph.nodes[ident] = CodeUnit(
                id=ident,
                name=name,
                kind=NodeKind.FUNCTION,
                location=make_location(node, file_path),
                branches=tuple(branches),
                cyclomatic_complexity=1 + len(branches),
            )
            add_contains_edge(graph, owner, ident)

        unit(root, module, "<script>", root)
        for dependency, site, raw, forced_builtin in imports:
            if not forced_builtin and (raw in names or raw in mutations):
                continue
            if dependency not in graph.dependencies:
                graph.dependencies.append(dependency)
                graph.edges.append(
                    CodeEdge(
                        module,
                        dependency,
                        EdgeKind.IMPORTS,
                        location=make_location(site, file_path),
                    )
                )
        script = f"{module}:<script>"
        for owner, command in commands:
            callee = command.child_by_field_name("name")
            if callee is None:
                continue
            raw = _literal(callee) or node_text(callee)
            name_text = node_text(callee)
            if name_text == "time":
                raw, _, _ = _invocation(command)
            candidates = names.get(raw, [])
            # Only unconditional top-level declarations are considered below;
            # shell function lookup and sourced files can still change at runtime.
            target = None
            if (
                re.fullmatch(r"[A-Za-z_][\w-]*", raw)
                and not dynamic_mutation
                and raw not in mutations
                and len(candidates) == 1
                and candidates[0][2]
                and (owner != script or candidates[0][1] <= command.start_byte)
            ):
                target = candidates[0][0]
            graph.edges.append(
                CodeEdge(
                    owner,
                    target or f"{owner}.<command>.{raw}",
                    EdgeKind.CALLS,
                    confidence=EdgeConfidence.INFERRED if target else EdgeConfidence.UNCERTAIN,
                    location=make_location(command, file_path),
                )
            )
        return graph
