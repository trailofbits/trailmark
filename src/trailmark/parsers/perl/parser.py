"""Perl packages, subroutine bodies and conservative call sites."""

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
from trailmark.models.nodes import BranchInfo, CodeUnit, NodeKind, Parameter
from trailmark.parsers._common import (
    add_contains_edge,
    add_module_node,
    make_location,
    module_id_from_path,
    node_text,
    parse_directory,
)
from trailmark.parsers._simple import descendants, first_child_type

_LOG = logging.getLogger(__name__)
_BRANCHES = {
    "conditional_statement",
    "elsif_clause",
    "loop_statement",
    "for_statement",
    "foreach_statement",
    "while_statement",
    "until_statement",
    "conditional_expression",
}


def _parameters(node: Node) -> tuple[Parameter, ...]:
    signature = first_child_type(node, {"signature"})
    if signature is None:
        return ()
    result = []
    for item in signature.named_children:
        name = first_child_type(item, {"scalar", "array", "hash"})
        if name is not None:
            default = item.child_by_field_name("default")
            result.append(
                Parameter(node_text(name), default=node_text(default) if default else None)
            )
    return tuple(result)


def _literal(node: Node) -> str | None:
    if node.type == "bareword":
        return node_text(node)
    if node.type in {"string_literal", "interpolated_string_literal"} and all(
        c.type == "string_content" and not c.named_children for c in node.named_children
    ):
        return "".join(node_text(c) for c in node.named_children)
    return None


class PerlParser:
    """Extract Perl 5 source without executing imports, eval or generators."""

    @property
    def language(self) -> str:
        return "perl"

    def __init__(self) -> None:
        self._parser = Parser(get_language("perl"))

    def parse_directory(self, dir_path: str) -> CodeGraph:
        return parse_directory(self.parse_file, "perl", dir_path, (".pl", ".pm"))

    def parse_file(self, file_path: str) -> CodeGraph:
        root = self._parser.parse(Path(file_path).read_bytes()).root_node
        if root.has_error:
            _LOG.warning("Perl syntax errors in %s; graph may be incomplete", file_path)
        module = module_id_from_path(file_path)
        graph = CodeGraph(language=self.language, root_path=file_path)
        add_module_node(root, file_path, module, graph)
        script = f"{module}:<script>"
        bindings: dict[tuple[str, str], list[str]] = defaultdict(list)
        lexical: dict[tuple[str, str], list[str]] = defaultdict(list)
        parents: dict[str, str] = {}
        calls: list[tuple[str, str, Node]] = []
        branches: dict[str, list[BranchInfo]] = defaultdict(list)
        declared: dict[tuple[str, str], list[int]] = defaultdict(list)
        mutated = False

        def package_owner(package: str, site: Node) -> str:
            if package == "main":
                return module
            ident = f"{module}:{package.replace('::', '.')}"
            if ident not in graph.nodes:
                graph.nodes[ident] = CodeUnit(
                    id=ident,
                    name=package,
                    kind=NodeKind.NAMESPACE,
                    location=make_location(site, file_path),
                )
                add_contains_edge(graph, module, ident)
            return ident

        def dependency(target: str | None, site: Node) -> None:
            if target and target not in graph.dependencies:
                graph.dependencies.append(target)
                graph.edges.append(
                    CodeEdge(
                        module, target, EdgeKind.IMPORTS, location=make_location(site, file_path)
                    )
                )

        def function(node: Node, package: str, caller: str) -> None:
            declaration_name = node.child_by_field_name("name")
            if declaration_name is not None:
                raw = node_text(declaration_name)
                declaration_package, declaration_name_text = (
                    raw.rsplit("::", 1) if "::" in raw else (package, raw)
                )
                declared[declaration_package, declaration_name_text].append(node.start_byte)
            body = node.child_by_field_name("body")
            if body is None:
                return  # Forward declarations do not create duplicate implementations.
            name_node = node.child_by_field_name("name")
            anonymous = name_node is None
            name = node_text(name_node) if name_node else f"<lambda>@{node.start_byte}"
            is_lexical = bool(re.match(r"(?:my|state)\s+sub\b", node_text(node)))
            target_package = package
            if "::" in name:
                target_package, name = name.rsplit("::", 1)
            owner = caller if anonymous or is_lexical else package_owner(target_package, node)
            ident = f"{owner}:{name}" if owner == module else f"{owner}.{name}"
            if ident in graph.nodes:
                ident += f"@{node.start_byte}"
            prototype = first_child_type(node, {"prototype"})
            graph.nodes[ident] = CodeUnit(
                id=ident,
                name=name,
                kind=NodeKind.FUNCTION,
                location=make_location(node, file_path),
                parameters=_parameters(node),
                attributes=(("perl_prototype", node_text(prototype)),)
                if prototype is not None
                else (),
            )
            add_contains_edge(graph, owner, ident)
            parents[ident] = caller
            if is_lexical:
                lexical[caller, name].append(ident)
            elif not anonymous:
                bindings[target_package, name].append(ident)
            signature = first_child_type(node, {"signature"})
            if signature is not None:
                for parameter in signature.named_children:
                    default = parameter.child_by_field_name("default")
                    if default is not None:
                        scan(default, package, ident)
            # A qualified declaration changes symbol ownership, not the lexical
            # package in which its body and defaults were compiled.
            scan_children(body, package, ident)

        def scan_children(node: Node, package: str, caller: str) -> None:
            current_package = package
            for child in node.named_children:
                if child.type == "package_statement":
                    name_node = child.child_by_field_name("name")
                    if name_node is None:
                        continue
                    name = node_text(name_node)
                    package_owner(name, child)
                    block = first_child_type(child, {"block"})
                    if block is None:
                        current_package = name
                    else:
                        scan_children(block, name, caller)
                else:
                    scan(child, current_package, caller)

        def scan(node: Node, package: str, caller: str) -> None:
            nonlocal mutated
            if node.type in {"subroutine_declaration_statement", "anonymous_subroutine_expression"}:
                function(node, package, caller)
                return
            if node.type in _BRANCHES:
                condition = node.child_by_field_name("condition")
                branches[caller].append(
                    BranchInfo(
                        make_location(node, file_path),
                        node_text(condition) if condition else node.type,
                    )
                )
            if node.type == "assignment_expression":
                left = node.child_by_field_name("left")
                if left is not None and (
                    left.type == "glob" or any(c.type == "glob" for c in descendants(left))
                ):
                    mutated = True
            if node.type == "undef_expression" and any(
                c.type in {"function", "glob"} for c in descendants(node)
            ):
                mutated = True
            if node.type == "eval_expression" and first_child_type(node, {"filename"}) is None:
                mutated = True
            if node.type.endswith("_call_expression") or node.type in {
                "require_expression",
                "eval_expression",
            }:
                calls.append((caller, package, node))
            if (
                node.type == "expression_statement"
                and len(node.named_children) == 1
                and node.named_children[0].type == "bareword"
            ):
                calls.append((caller, package, node.named_children[0]))
            if node.type == "use_statement":
                name_node = node.child_by_field_name("module")
                if name_node is not None:
                    dependency(node_text(name_node), node)
            if node.type == "require_expression" and node.named_children:
                dependency(_literal(node.named_children[0]), node)
            if node.type == "eval_expression":
                filename = first_child_type(node, {"filename"})
                if filename is not None and filename.named_children:
                    dependency(_literal(filename.named_children[0]), node)
            scan_children(node, package, caller)

        graph.nodes[script] = CodeUnit(
            id=script,
            name="<script>",
            kind=NodeKind.FUNCTION,
            location=make_location(root, file_path),
        )
        add_contains_edge(graph, module, script)
        scan_children(root, "main", script)
        for ident, unit in list(graph.nodes.items()):
            if unit.kind == NodeKind.FUNCTION:
                owned = branches[ident]
                graph.nodes[ident] = replace(
                    unit, branches=tuple(owned), cyclomatic_complexity=1 + len(owned)
                )
        for caller, package, call in calls:
            name_node = call if call.type == "bareword" else call.child_by_field_name("function")
            raw = node_text(name_node).lstrip("&") if name_node else node_text(call.children[0])
            if call.type == "bareword":
                target_package, name = raw.rsplit("::", 1) if "::" in raw else (package, raw)
                if not any(
                    position < call.start_byte
                    for position in declared.get((target_package, name), [])
                ):
                    continue
            target = None
            if (
                not mutated
                and name_node is not None
                and re.fullmatch(r"(?:[A-Za-z_]\w*::)*[A-Za-z_]\w*", raw)
            ):
                scope = caller
                candidates: list[str] = []
                if "::" not in raw:
                    while scope:
                        candidates = lexical.get((scope, raw), [])
                        if candidates:
                            break
                        scope = parents.get(scope, "")
                shadowed = bool(candidates)
                if not shadowed:
                    target_package, name = raw.rsplit("::", 1) if "::" in raw else (package, raw)
                    candidates = bindings.get((target_package, name), [])
                if len(candidates) == 1 and not shadowed:
                    target = candidates[0]
            if call.type == "method_call_expression":
                invocant = call.child_by_field_name("invocant")
                method = call.child_by_field_name("method")
                receiver = node_text(invocant) if invocant else "?"
                raw = f"{receiver}->{node_text(method) if method else '?'}"
            elif call.type == "coderef_call_expression":
                raw = node_text(call)
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
