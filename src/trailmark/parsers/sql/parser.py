"""PostgreSQL-oriented SQL parser using tree-sitter-sql."""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterator
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
from trailmark.parsers._declarations import add_callable, add_type, callable_scope

_EXTENSIONS = (".sql",)
_DECLARATIONS: dict[str, NodeKind] = {
    "create_schema": NodeKind.SCHEMA,
    "create_table": NodeKind.TABLE,
    "create_view": NodeKind.VIEW,
    "create_materialized_view": NodeKind.VIEW,
    "create_function": NodeKind.FUNCTION,
    "create_procedure": NodeKind.PROCEDURE,
}


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
        with callable_scope(graph):
            for node in _walk(tree.root_node):
                kind = _DECLARATIONS.get(node.type)
                if kind is not None:
                    _extract_declaration(node, kind, file_path, module_id, graph)
            _extract_procedures(source, file_path, module_id, graph)
        _link_cross_file_sql_dependencies(graph)
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

    parameters = (
        _routine_parameters(node) if kind in {NodeKind.FUNCTION, NodeKind.PROCEDURE} else ()
    )
    existing = graph.nodes.get(unit_id)
    if (
        kind not in {NodeKind.FUNCTION, NodeKind.PROCEDURE}
        and existing is not None
        and existing.kind not in {NodeKind.FUNCTION, NodeKind.PROCEDURE, kind}
    ):
        unit_id = _disambiguate_sql_id(graph, unit_id, kind)
    unit = CodeUnit(
        id=unit_id,
        name=name,
        kind=kind,
        location=make_location(node, file_path),
        parameters=parameters,
        attributes=(("sql_schema", schema or ""),),
    )
    if kind in {NodeKind.FUNCTION, NodeKind.PROCEDURE}:
        unit_id = _add_routine(graph, unit)
    else:
        add_type(graph, unit)
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
        default = next(
            (
                node_text(named[index + 1])
                for index, item in enumerate(named[:-1])
                if item.type == "keyword_default"
            ),
            None,
        )
        result.append(Parameter(name=name, type_ref=type_ref, default=default))
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
    # Relation identity is independent of the callable namespace. Matching is
    # repeated after all declarations are known, including later files.
    return _sql_id(module_id, schema, name)


def _extract_procedures(source: bytes, file_path: str, module_id: str, graph: CodeGraph) -> None:
    """Recover procedures from actual top-level statements, never quoted text."""
    text = source.decode("utf-8", errors="replace")
    for tokens in _statements(text):
        words = [token[0].upper() for token in tokens[:5]]
        offset = 3 if words[:3] == ["CREATE", "OR", "REPLACE"] else 1
        if (
            not words
            or words[0] != "CREATE"
            or len(tokens) <= offset
            or words[offset] != "PROCEDURE"
        ):
            continue
        cursor = offset + 1
        if cursor >= len(tokens):
            continue
        name = tokens[cursor][0]
        schema = None
        cursor += 1
        if cursor + 1 < len(tokens) and tokens[cursor][0] == ".":
            schema, name = name, tokens[cursor + 1][0]
            cursor += 2
        if cursor >= len(tokens) or tokens[cursor][0] != "(":
            continue
        depth, end_params = 0, cursor
        for index in range(cursor, len(tokens)):
            if tokens[index][0] == "(":
                depth += 1
            elif tokens[index][0] == ")":
                depth -= 1
                if depth == 0:
                    end_params = index
                    break
        parameters = _recovered_parameters(tokens[cursor + 1 : end_params])
        start, end = tokens[0][1], tokens[-1][2]
        location = SourceLocation(
            file_path,
            text.count("\n", 0, start) + 1,
            text.count("\n", 0, end) + 1,
            start - text.rfind("\n", 0, start) - 1,
            end - text.rfind("\n", 0, end) - 1,
        )
        base = _sql_id(module_id, schema, name)
        # The grammar may gain native procedure support in a later version.
        if any(
            unit.kind == NodeKind.PROCEDURE and unit.location.start_line == location.start_line
            for unit in graph.nodes.values()
        ):
            continue
        unit_id = _add_routine(
            graph,
            CodeUnit(
                id=base,
                name=name,
                kind=NodeKind.PROCEDURE,
                location=location,
                parameters=parameters,
                attributes=(("sql_schema", schema or ""),),
            ),
        )
        container = f"{module_id}:{schema}" if schema else module_id
        add_contains_edge(graph, container if container in graph.nodes else module_id, unit_id)
        body = tokens[end_params + 1 :]
        # Dollar/single quoted AS bodies are SQL at one deeper lexical level.
        for index, token in enumerate(body):
            if token[0].upper() == "AS" and index + 1 < len(body):
                literal = body[index + 1][0]
                tag = _dollar_quote_tag(literal, 0)
                if tag:
                    body = list(_tokens(literal[len(tag) : -len(tag)]))
                elif literal.startswith("'"):
                    body = list(_tokens(literal[1:-1].replace("''", "'")))
                break
        seen: set[str] = set()
        for index, token in enumerate(body[:-1]):
            if token[0].upper() not in {"FROM", "JOIN", "UPDATE", "INTO"}:
                continue
            target_name = body[index + 1][0]
            if re.fullmatch(_IDENTIFIER, target_name) is None:
                continue
            target_schema = None
            if index + 3 < len(body) and body[index + 2][0] == ".":
                target_schema, target_name = target_name, body[index + 3][0]
            target = _sql_reference_id(graph, module_id, target_schema, target_name)
            if target not in seen:
                seen.add(target)
                graph.edges.append(
                    CodeEdge(
                        unit_id,
                        target,
                        EdgeKind.CORRESPONDS_TO,
                        confidence=EdgeConfidence.INFERRED,
                        attributes=(("relationship", "sql_dependency"),),
                    )
                )


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
    by_name: dict[tuple[str, str], list[str]] = {}
    for node_id, unit in graph.nodes.items():
        if unit.kind not in {NodeKind.TABLE, NodeKind.VIEW}:
            continue
        schema = str(dict(unit.attributes).get("sql_schema", ""))
        by_name.setdefault((_identifier(schema), _identifier(unit.name)), []).append(node_id)
    rewritten: list[CodeEdge] = []
    for edge in graph.edges:
        if edge.kind != EdgeKind.CORRESPONDS_TO:
            rewritten.append(edge)
            continue
        attributes = dict(edge.attributes)
        qualified = str(
            attributes.get(
                "sql_reference", edge.target_id.rsplit(":", 1)[-1].removesuffix("#relation")
            )
        )
        pieces = re.findall(_IDENTIFIER, qualified)
        key = (
            (_identifier(pieces[-2]) if len(pieces) > 1 else "", _identifier(pieces[-1]))
            if pieces
            else ("", "")
        )
        matches = by_name.get(key, [])
        local = [
            candidate
            for candidate in matches
            if candidate.split(":", 1)[0] == edge.source_id.split(":", 1)[0]
        ]
        candidates = local or matches
        attributes["sql_reference"] = qualified
        unresolved = edge.target_id
        existing = graph.nodes.get(unresolved)
        if existing is not None and existing.kind not in {
            NodeKind.TABLE,
            NodeKind.VIEW,
            NodeKind.PROXY,
        }:
            unresolved += "#relation"
        rewritten.append(
            dataclasses.replace(
                edge,
                target_id=candidates[0] if len(candidates) == 1 else unresolved,
                attributes=tuple(attributes.items()),
            )
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


_IDENTIFIER = r'"(?:[^"\n]|"")+"|[A-Za-z_][\w$]*'


def _identifier(raw: str) -> str:
    return (
        raw[1:-1].replace('""', '"') if raw.startswith('"') and raw.endswith('"') else raw.lower()
    )


def _add_routine(graph: CodeGraph, unit: CodeUnit) -> str:
    signature = ",".join(
        _identifier(parameter.type_ref.name if parameter.type_ref else parameter.name)
        for parameter in unit.parameters
    )
    schema = dict(unit.attributes).get("sql_schema", "")
    for old in list(graph.nodes.values()):
        attributes = dict(old.attributes)
        if (
            old.kind == unit.kind
            and _identifier(old.name) == _identifier(unit.name)
            and _identifier(str(attributes.get("sql_schema", ""))) == _identifier(str(schema))
            and attributes.get("sql_signature") == signature
        ):
            graph.nodes[old.id] = dataclasses.replace(
                unit, id=old.id, attributes=(*unit.attributes, ("sql_signature", signature))
            )
            graph.edges[:] = [
                edge
                for edge in graph.edges
                if edge.source_id != old.id
                and not (edge.kind == EdgeKind.CONTAINS and edge.target_id == old.id)
            ]
            return old.id
    return add_callable(
        graph,
        dataclasses.replace(unit, attributes=(*unit.attributes, ("sql_signature", signature))),
    )


def _tokens(text: str) -> Iterator[tuple[str, int, int]]:
    """Yield lexical tokens and character spans; skip nested SQL comments."""
    index = 0
    while index < len(text):
        if text[index].isspace():
            index += 1
            continue
        start = index
        if text.startswith("--", index):
            end = text.find("\n", index)
            index = len(text) if end < 0 else end + 1
            continue
        if text.startswith("/*", index):
            depth, index = 1, index + 2
            while index < len(text) and depth:
                if text.startswith("/*", index):
                    depth, index = depth + 1, index + 2
                elif text.startswith("*/", index):
                    depth, index = depth - 1, index + 2
                else:
                    index += 1
            continue
        tag = _dollar_quote_tag(text, index) if text[index] == "$" else None
        if tag:
            end = text.find(tag, index + len(tag))
            index = len(text) if end < 0 else end + len(tag)
        elif text[index] in "'\"":
            quote, index = text[index], index + 1
            while index < len(text):
                if text[index] == quote:
                    index += 1
                    if index >= len(text) or text[index] != quote:
                        break
                elif text[index] == "\\" and quote == "'":
                    index += 1
                index += 1
        else:
            match = re.match(r"[A-Za-z_][\w$]*|[0-9]+(?:\.[0-9]+)?", text[index:])
            index += len(match.group(0)) if match else 1
        yield text[start:index], start, index


def _statements(text: str) -> Iterator[list[tuple[str, int, int]]]:
    statement = []
    parentheses, atomic = 0, 0
    previous = ""
    for token in _tokens(text):
        value = token[0].upper()
        statement.append(token)
        if value == "(":
            parentheses += 1
        elif value == ")":
            parentheses -= 1
        elif value == "ATOMIC" and previous == "BEGIN":
            atomic += 1
        elif value == "END" and atomic:
            atomic -= 1
        if value == ";" and parentheses == 0 and atomic == 0:
            yield statement
            statement = []
        previous = value
    if statement:
        yield statement


def _recovered_parameters(tokens: list[tuple[str, int, int]]) -> tuple[Parameter, ...]:
    parts: list[list[str]] = [[]]
    depth = 0
    for lexeme, _, _ in tokens:
        if lexeme == "," and depth == 0:
            parts.append([])
        else:
            parts[-1].append(lexeme)
        if lexeme in {"(", "["}:
            depth += 1
        elif lexeme in {")", "]"}:
            depth -= 1
    result = []
    for index, words in enumerate(parts):
        if not words:
            continue
        if words[0].upper() in {"IN", "OUT", "INOUT", "VARIADIC"}:
            words = words[1:]
        if not words:
            continue
        default = None
        for position, word in enumerate(words):
            if word.upper() in {"DEFAULT", "="}:
                default = " ".join(words[position + 1 :])
                words = words[:position]
                break
        if not words:
            continue
        name = words[0] if len(words) > 1 else f"$arg{index}"
        type_name = " ".join(words[1:] if len(words) > 1 else words)
        result.append(Parameter(name, TypeRef(type_name), default=default))
    return tuple(result)
