"""Recover a bounded class declaration interrupted by one export-marker token."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from tree_sitter import Parser, Range, Tree

from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind
from trailmark.parsers._common import node_text


@dataclass(frozen=True)
class ExportMarker:
    name: str
    start_byte: int
    end_byte: int
    declaration_name: str
    declaration_start: int
    declaration_end: int
    declaration_point: tuple[int, int]


def recover_export_markers(
    parser: Parser, source: bytes, original: Tree
) -> tuple[Tree, tuple[ExportMarker, ...]]:
    """Omit only proved annotation positions while retaining original offsets.

    The grammar reads ``class API Name { public: ... }`` as a function definition.
    A direct access label proves class context; an elaborated-type aggregate
    initializer cannot contain that reserved label. This does not expand macros.
    """
    candidates = []
    pending = [original.root_node]
    while pending:
        node = pending.pop()
        pending.extend(reversed(node.named_children))
        if node.type != "function_definition":
            continue
        specifier = node.child_by_field_name("type")
        declarator = node.child_by_field_name("declarator")
        body = node.child_by_field_name("body")
        if (
            specifier is None
            or specifier.type != "class_specifier"
            or specifier.child_by_field_name("body") is not None
            or declarator is None
            or declarator.type != "identifier"
            or body is None
            or body.type != "compound_statement"
            or len(node.named_children) != 3
        ):
            continue
        marker = specifier.child_by_field_name("name")
        if (
            marker is None
            or marker.type != "type_identifier"
            or re.fullmatch(r"[A-Z][A-Z0-9_]*", node_text(marker)) is None
            or not any(c.type == "class" for c in specifier.children)
            or not any(
                c.type == "labeled_statement"
                and (label := c.child_by_field_name("label")) is not None
                and node_text(label) in {"public", "private", "protected"}
                for c in body.named_children
            )
        ):
            continue
        candidates.append(
            (
                marker,
                ExportMarker(
                    node_text(marker),
                    marker.start_byte,
                    marker.end_byte,
                    node_text(declarator),
                    node.start_byte,
                    node.end_byte,
                    tuple(node.start_point),
                ),
            )
        )
    if not candidates:
        return original, ()
    candidates.sort(key=lambda candidate: candidate[0].start_byte)
    ranges = []
    eof = (source.count(b"\n"), len(source.rsplit(b"\n", 1)[-1]))
    for allowed in original.included_ranges:
        start_byte, start_point = allowed.start_byte, allowed.start_point
        limit = min(allowed.end_byte, len(source))
        for marker, _ in candidates:
            if marker.end_byte <= start_byte or marker.start_byte >= limit:
                continue
            if start_byte < marker.start_byte:
                ranges.append(Range(start_point, marker.start_point, start_byte, marker.start_byte))
            start_byte, start_point = marker.end_byte, marker.end_point
        if start_byte < limit:
            end_point = allowed.end_point if allowed.end_byte <= len(source) else eof
            ranges.append(Range(start_point, end_point, start_byte, limit))
    bounded = Parser(parser.language)
    bounded.included_ranges = ranges
    recovered = bounded.parse(source)
    declarations = set()
    pending = [recovered.root_node]
    while pending:
        node = pending.pop()
        pending.extend(node.named_children)
        if node.type == "class_specifier" and node.child_by_field_name("body") is not None:
            name = node.child_by_field_name("name")
            declarations.add((node.start_byte, node.end_byte, node_text(name) if name else ""))
    if not all(
        (m.declaration_start, m.declaration_end, m.declaration_name) in declarations
        for _, m in candidates
    ):
        return original, ()
    return recovered, tuple(m for _, m in candidates)


def mark_recovered_classes(graph: CodeGraph, markers: tuple[ExportMarker, ...]) -> None:
    by_point = {m.declaration_point: m for m in markers}
    for key, unit in graph.nodes.items():
        marker = by_point.get((unit.location.start_line - 1, unit.location.start_col))
        if unit.kind != NodeKind.CLASS or marker is None or marker.declaration_name != unit.name:
            continue
        graph.nodes[key] = replace(
            unit,
            attributes=(
                *unit.attributes,
                ("cpp_export_marker", marker.name),
                ("cpp_export_marker_start_byte", marker.start_byte),
                ("cpp_export_marker_end_byte", marker.end_byte),
            ),
        )
