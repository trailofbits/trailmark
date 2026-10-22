"""Rego language parser using tree-sitter."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from tree_sitter import Node, Parser
from tree_sitter_language_pack import get_language

from trailmark.models.diagnostics import ParseDiagnostic
from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit, NodeKind, NodeOrigin, Parameter
from trailmark.parsers._common import (
    add_contains_edge,
    add_module_node,
    compute_complexity,
    make_location,
    module_id_from_path,
    node_text,
    parse_directory,
)
from trailmark.parsers._simple import (
    add_call_edges,
    collect_branches_and_calls,
    descendants,
    named_children,
)

_EXTENSIONS = (".rego",)
_BRANCH_TYPES = frozenset({"literal"})
_CALL_TYPES = frozenset({"expr_call"})


class RegoParser:
    """Parses Rego policy files into CodeGraph using tree-sitter."""

    @property
    def language(self) -> str:
        return "rego"

    def __init__(self) -> None:
        self._parser = Parser(get_language("rego"))

    def parse_file(self, file_path: str) -> CodeGraph:
        source = Path(file_path).read_bytes()
        tree = self._parser.parse(source)
        graph = CodeGraph(language="rego", root_path=file_path)
        module_id = module_id_from_path(file_path)
        _visit_root(tree.root_node, file_path, module_id, graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        return parse_directory(self.parse_file, "rego", dir_path, _EXTENSIONS)


def _visit_root(root: Node, file_path: str, fallback_module_id: str, graph: CodeGraph) -> None:
    module = _find_module(root)
    package_name = _package_name(module) if module is not None else ""
    module_id = package_name or fallback_module_id
    add_module_node(root, file_path, module_id, graph)
    if module is None:
        return
    for child in named_children(module):
        if child.type == "import":
            _extract_import(child, graph, module_id)
        elif child.type == "policy":
            for rule in named_children(child):
                if rule.type == "rule":
                    _extract_rule(rule, file_path, module_id, graph)


def _find_module(root: Node) -> Node | None:
    if root.type == "module":
        return root
    for child in named_children(root):
        if child.type == "module":
            return child
    return None


def _package_name(module: Node | None) -> str:
    if module is None:
        return ""
    seen_package = False
    for child in named_children(module):
        if child.type == "package":
            seen_package = True
            continue
        if seen_package and child.type == "ref":
            return node_text(child)
    return ""


def _extract_rule(node: Node, file_path: str, module_id: str, graph: CodeGraph) -> None:
    head = next((child for child in named_children(node) if child.type == "rule_head"), None)
    if head is None:
        return
    name = _rule_name(head)
    if not name:
        return
    rule_id = f"{module_id}:{name}"
    branches, calls = collect_branches_and_calls(
        node,
        file_path,
        branch_types=_BRANCH_TYPES,
        call_types=_CALL_TYPES,
    )
    position = (
        f"{module_id_from_path(file_path)}_{node.start_point.row + 1}_{node.start_point.column}"
    )
    clause_id = f"{rule_id}.$clause@{position}"
    graph.nodes[clause_id] = CodeUnit(
        id=clause_id,
        name="$clause",
        kind=NodeKind.FUNCTION,
        location=make_location(node, file_path),
        parameters=_rule_parameters(head),
        cyclomatic_complexity=compute_complexity(branches),
        branches=tuple(branches),
    )
    if rule_id not in graph.nodes:
        graph.nodes[rule_id] = CodeUnit(
            id=rule_id,
            name=name,
            kind=NodeKind.FUNCTION,
            origin=NodeOrigin.SYNTHETIC,
            location=make_location(node, file_path),
            parameters=_rule_parameters(head),
            cyclomatic_complexity=1,
        )
        add_contains_edge(graph, module_id, rule_id)
    add_contains_edge(graph, rule_id, clause_id)
    graph.edges.append(
        CodeEdge(
            rule_id,
            clause_id,
            EdgeKind.CALLS,
            confidence=EdgeConfidence.INFERRED,
            location=make_location(node, file_path),
            attributes=(("rego_clause", True),),
        )
    )
    previous = len(graph.edges)
    add_call_edges(graph, calls, clause_id, module_id, file_path)
    root = node
    while root.parent is not None:
        root = root.parent
    if root.has_error:
        graph.edges[previous:] = [
            replace(
                e,
                target_id=f"{clause_id}.$recovery.{e.target_id}",
                confidence=EdgeConfidence.UNCERTAIN,
                attributes=e.attributes + (("binding_blocked", True),),
            )
            for e in graph.edges[previous:]
        ]
    bodies = [child for child in node.named_children if child.type == "rule_body"]
    if any(node_text(body).lstrip().startswith("(") for body in bodies):
        graph.parse_diagnostics.append(
            ParseDiagnostic(
                language="rego",
                grammar="rego",
                location=make_location(node, file_path),
                kind="unsupported_syntax",
                message="Inline-if call syntax is split by this grammar; use a braced rule body",
            )
        )


def _rule_name(head: Node) -> str:
    for child in named_children(head):
        if child.type == "var":
            return node_text(child)
    return ""


def _rule_parameters(head: Node) -> tuple[Parameter, ...]:
    for child in descendants(head):
        if child.type == "rule_args":
            params = []
            for arg in named_children(child):
                text = node_text(arg)
                if text:
                    params.append(Parameter(name=text))
            return tuple(params)
    return ()


def _extract_import(node: Node, graph: CodeGraph, module_id: str) -> None:
    target = node.next_named_sibling
    if target is not None and target.type == "ref":
        text = node_text(target)
        graph.dependencies.append(text)
        graph.edges.append(CodeEdge(source_id=module_id, target_id=text, kind=EdgeKind.IMPORTS))
