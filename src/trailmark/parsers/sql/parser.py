"""PostgreSQL-oriented SQL parser using tree-sitter-sql."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import tree_sitter_sql
from tree_sitter import Language, Node, Parser

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import (
    CodeUnit,
    NodeKind,
    NodeOrigin,
    Parameter,
    SourceLocation,
    TypeRef,
)
from trailmark.parsers._common import (
    add_contains_edge,
    add_module_node,
    make_location,
    module_id_from_path,
    node_text,
    parse_directory,
)

_EXTENSIONS = (".sql",)
_DECLARATIONS: dict[str, NodeKind] = {
    "create_schema": NodeKind.SCHEMA,
    "create_table": NodeKind.TABLE,
    "create_view": NodeKind.VIEW,
    "create_materialized_view": NodeKind.VIEW,
    "create_function": NodeKind.FUNCTION,
    "create_procedure": NodeKind.PROCEDURE,
}
_PROCEDURE = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?PROCEDURE\s+"
    r"(?:(?P<schema>[A-Za-z_][\w$]*)\.)?(?P<name>[A-Za-z_][\w$]*)\s*\(",
    re.IGNORECASE,
)
_RELATION_REFERENCE = re.compile(
    r"\b(?:FROM|JOIN|UPDATE|INTO)\s+"
    r"(?:(?P<schema>[A-Za-z_][\w$]*)\.)?(?P<name>[A-Za-z_][\w$]*)",
    re.IGNORECASE,
)
_NEXT_CREATE = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:SCHEMA|TABLE|VIEW|FUNCTION|PROCEDURE)\b", re.IGNORECASE
)


class SQLParser:
    """Parse common PostgreSQL-style schema and routine declarations."""

    @property
    def language(self) -> str:
        return "sql"

    def __init__(self) -> None:
        self._parser = Parser(Language(tree_sitter_sql.language()))

    def parse_file(self, file_path: str) -> CodeGraph:
        return self._parse_file(file_path, materialize_dependencies=True)

    def _parse_file(self, file_path: str, *, materialize_dependencies: bool) -> CodeGraph:
        source = Path(file_path).read_bytes()
        tree = self._parser.parse(source)
        graph = CodeGraph(language="sql", root_path=file_path)
        module_id = module_id_from_path(file_path)
        add_module_node(tree.root_node, file_path, module_id, graph)
        for node in _walk(tree.root_node):
            kind = _DECLARATIONS.get(node.type)
            if kind is not None:
                _extract_declaration(node, kind, file_path, module_id, graph)
        _extract_procedures(source, file_path, module_id, graph)
        if materialize_dependencies:
            _materialize_sql_dependency_targets(graph, file_path)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        graph = parse_directory(
            lambda file_path: self._parse_file(file_path, materialize_dependencies=False),
            "sql",
            dir_path,
            _EXTENSIONS,
        )
        _link_cross_file_sql_dependencies(graph)
        _materialize_sql_dependency_targets(graph, dir_path)
        return graph


def _extract_declaration(
    node: Node,
    kind: NodeKind,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    qualified = _declaration_name(node)
    if qualified is None:
        return
    schema, name = qualified
    unit_id = _sql_id(module_id, schema, name)
    container_id = module_id
    if kind == NodeKind.SCHEMA:
        name = schema or name
        unit_id = f"{module_id}:{name}"
    elif schema:
        schema_id = f"{module_id}:{schema}"
        if schema_id in graph.nodes:
            container_id = schema_id
    else:
        unit_id = _disambiguate_sql_id(graph, unit_id, kind)

    parameters = (
        _routine_parameters(node) if kind in {NodeKind.FUNCTION, NodeKind.PROCEDURE} else ()
    )
    graph.nodes[unit_id] = CodeUnit(
        id=unit_id,
        name=name,
        kind=kind,
        location=make_location(node, file_path),
        parameters=parameters,
        attributes=(("sql_schema", schema),) if schema else (),
    )
    add_contains_edge(graph, container_id, unit_id)

    if kind in {NodeKind.VIEW, NodeKind.FUNCTION, NodeKind.PROCEDURE}:
        _add_dependencies(node, unit_id, qualified, module_id, file_path, graph)


def _declaration_name(node: Node) -> tuple[str | None, str] | None:
    for child in node.named_children:
        if child.type == "object_reference":
            return _object_reference(child)
    if node.type == "create_schema":
        for child in node.named_children:
            if child.type == "identifier":
                return (None, node_text(child))
    return None


def _object_reference(node: Node) -> tuple[str | None, str] | None:
    schema_node = node.child_by_field_name("schema")
    name_node = node.child_by_field_name("name")
    if name_node is not None:
        return (node_text(schema_node) if schema_node is not None else None, node_text(name_node))
    identifiers = [node_text(child) for child in node.named_children if child.type == "identifier"]
    if len(identifiers) == 1:
        return (None, identifiers[0])
    if len(identifiers) >= 2:
        return (identifiers[-2], identifiers[-1])
    return None


def _routine_parameters(node: Node) -> tuple[Parameter, ...]:
    result: list[Parameter] = []
    for child in _walk(node):
        if child.type != "function_argument":
            continue
        named = child.named_children
        if not named:
            continue
        name = node_text(named[0])
        type_ref = TypeRef(node_text(named[1])) if len(named) > 1 else None
        result.append(Parameter(name=name, type_ref=type_ref))
    return tuple(result)


def _add_dependencies(
    node: Node,
    source_id: str,
    declared: tuple[str | None, str],
    module_id: str,
    file_path: str,
    graph: CodeGraph,
) -> None:
    skipped_declaration = False
    seen: set[str] = set()
    for child in _walk(node):
        if child.type != "object_reference":
            continue
        reference = _object_reference(child)
        if reference is None:
            continue
        if not skipped_declaration and reference == declared:
            skipped_declaration = True
            continue
        if not _is_relation_reference(child):
            continue
        target_id = _sql_reference_id(graph, module_id, *reference)
        if target_id == source_id or target_id in seen:
            continue
        seen.add(target_id)
        graph.edges.append(
            CodeEdge(
                source_id=source_id,
                target_id=target_id,
                kind=EdgeKind.CORRESPONDS_TO,
                confidence=EdgeConfidence.CERTAIN,
                location=make_location(child, file_path),
                attributes=(("relationship", "sql_dependency"),),
            )
        )


def _is_relation_reference(node: Node) -> bool:
    """Return true for references used as query relations rather than calls."""
    parent = node.parent
    while parent is not None:
        if parent.type == "invocation":
            return False
        if parent.type == "relation":
            return True
        parent = parent.parent
    return False


def _sql_id(module_id: str, schema: str | None, name: str) -> str:
    qualified = f"{schema}.{name}" if schema else name
    return f"{module_id}:{qualified}"


def _disambiguate_sql_id(graph: CodeGraph, unit_id: str, kind: NodeKind) -> str:
    existing = graph.nodes.get(unit_id)
    if existing is None or existing.kind is kind:
        return unit_id
    return f"{unit_id}#{kind.value}"


def _sql_reference_id(graph: CodeGraph, module_id: str, schema: str | None, name: str) -> str:
    unit_id = _sql_id(module_id, schema, name)
    if graph.nodes.get(unit_id, None) is not None:
        for kind in (NodeKind.TABLE, NodeKind.VIEW):
            disambiguated = f"{unit_id}#{kind.value}"
            if disambiguated in graph.nodes:
                return disambiguated
    return unit_id


def _extract_procedures(
    source: bytes,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Recover PostgreSQL procedures unsupported by the permissive grammar."""
    text = source.decode("utf-8", errors="replace")
    scan_text = _mask_sql_comments(text)
    matches = list(_PROCEDURE.finditer(scan_text))
    for match_index, match in enumerate(matches):
        schema = match.group("schema")
        name = match.group("name")
        unit_id = _sql_id(module_id, schema, name)
        if unit_id in graph.nodes:
            continue
        start_line = text.count("\n", 0, match.start()) + 1
        container_id = f"{module_id}:{schema}" if schema else module_id
        if container_id not in graph.nodes:
            container_id = module_id
        graph.nodes[unit_id] = CodeUnit(
            id=unit_id,
            name=name,
            kind=NodeKind.PROCEDURE,
            location=SourceLocation(file_path, start_line, start_line),
            attributes=(("sql_schema", schema),) if schema else (),
        )
        add_contains_edge(graph, container_id, unit_id)
        next_procedure = (
            matches[match_index + 1].start() if match_index + 1 < len(matches) else len(text)
        )
        next_declaration = _NEXT_CREATE.search(scan_text, match.end())
        end = min(
            next_procedure,
            next_declaration.start() if next_declaration is not None else len(text),
        )
        for reference in _RELATION_REFERENCE.finditer(scan_text, match.end(), end):
            target_id = _sql_reference_id(
                graph, module_id, reference.group("schema"), reference.group("name")
            )
            graph.edges.append(
                CodeEdge(
                    source_id=unit_id,
                    target_id=target_id,
                    kind=EdgeKind.CORRESPONDS_TO,
                    confidence=EdgeConfidence.INFERRED,
                    attributes=(("relationship", "sql_dependency"),),
                )
            )


def _mask_sql_comments(text: str) -> str:
    chars = list(text)
    index = 0
    dollar_quote: str | None = None
    while index < len(chars):
        if dollar_quote is not None:
            index, dollar_quote = _advance_dollar_quote(text, index, dollar_quote)
        elif _starts_line_comment(text, index):
            index = _mask_until_line_end(chars, index)
        elif text.startswith("/*", index):
            index = _mask_block_comment(chars, index)
        else:
            dollar_quote = _dollar_quote_tag(text, index)
            index += len(dollar_quote) if dollar_quote else 1
    return "".join(chars)


def _advance_dollar_quote(text: str, index: int, tag: str) -> tuple[int, str | None]:
    if text.startswith(tag, index):
        return index + len(tag), None
    return index + 1, tag


def _starts_line_comment(text: str, index: int) -> bool:
    return text.startswith("--", index)


def _mask_until_line_end(chars: list[str], index: int) -> int:
    while index < len(chars) and chars[index] != "\n":
        chars[index] = " "
        index += 1
    return index


def _mask_block_comment(chars: list[str], index: int) -> int:
    chars[index] = " "
    chars[index + 1] = " "
    index += 2
    while index < len(chars):
        if index + 1 < len(chars) and chars[index] == "*" and chars[index + 1] == "/":
            chars[index] = " "
            chars[index + 1] = " "
            return index + 2
        if chars[index] != "\n":
            chars[index] = " "
        index += 1
    return index


def _dollar_quote_tag(text: str, index: int) -> str | None:
    match = re.match(r"\$[A-Za-z_][A-Za-z_0-9]*\$|\$\$", text[index:])
    return match.group(0) if match else None


def _materialize_sql_dependency_targets(graph: CodeGraph, file_path: str) -> None:
    for edge in graph.edges:
        if edge.kind != EdgeKind.CORRESPONDS_TO or edge.target_id in graph.nodes:
            continue
        graph.nodes[edge.target_id] = CodeUnit(
            id=edge.target_id,
            name=edge.target_id.rsplit(":", 1)[-1],
            kind=NodeKind.PROXY,
            location=edge.location or SourceLocation(file_path, 0, 0),
            origin=NodeOrigin.PROXY,
            attributes=(
                ("raw_symbol", edge.target_id),
                ("proxy_kind", "sql_relation"),
            ),
        )


def _link_cross_file_sql_dependencies(graph: CodeGraph) -> None:
    by_qualified_name: dict[str, list[str]] = {}
    for node_id, unit in graph.nodes.items():
        if unit.kind not in {NodeKind.TABLE, NodeKind.VIEW, NodeKind.FUNCTION, NodeKind.PROCEDURE}:
            continue
        schema = dict(unit.attributes).get("sql_schema")
        qualified = f"{schema}.{unit.name}" if schema else unit.name
        by_qualified_name.setdefault(qualified, []).append(node_id)

    rewritten: list[CodeEdge] = []
    for edge in graph.edges:
        if edge.kind != EdgeKind.CORRESPONDS_TO or edge.target_id in graph.nodes:
            rewritten.append(edge)
            continue
        qualified = edge.target_id.rsplit(":", 1)[-1]
        matches = by_qualified_name.get(qualified, [])
        rewritten.append(
            dataclasses.replace(edge, target_id=matches[0]) if len(matches) == 1 else edge
        )
    graph.edges = rewritten


def _walk(root: Node) -> list[Node]:
    result: list[Node] = []
    stack = list(reversed(root.named_children))
    while stack:
        node = stack.pop()
        result.append(node)
        stack.extend(reversed(node.named_children))
    return result
