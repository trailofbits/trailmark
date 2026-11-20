"""Recover physical function bodies around bounded block-macro headers."""

from __future__ import annotations

import json
import re
from bisect import bisect_right
from dataclasses import dataclass, replace

from tree_sitter import Parser, Range, Tree

from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind
from trailmark.parsers._common import node_text

_OPAQUE = {
    "comment",
    "string_literal",
    "char_literal",
    "raw_string_literal",
    "number_literal",
    "system_lib_string",
}
_TOKEN = re.compile(rb"[A-Za-z_]\w*|\.\.\.|[^\s]")
_MARKER = re.compile(rb"[A-Z][A-Z0-9_]*\Z")
_CONDITIONAL = re.compile(rb"\s*#\s*(if|ifdef|ifndef|else|elif|endif)\b")


@dataclass(frozen=True)
class _Token:
    text: bytes
    start: int
    end: int


@dataclass(frozen=True)
class RecoveredBody:
    start: int
    end: int
    point: tuple[int, int]
    declarator: str
    markers: tuple[tuple[int, int, str], ...]


def _tokens(source: bytes, tree: Tree) -> list[_Token] | None:
    opaque = []
    pending = [tree.root_node]
    while pending:
        node = pending.pop()
        if node.type in _OPAQUE:
            opaque.append((node.start_byte, node.end_byte))
        else:
            pending.extend(node.named_children)
    opaque.sort()
    opaque_starts = [start for start, _ in opaque]
    directives = []
    lines = source.splitlines(keepends=True)
    offset, index = 0, 0
    while index < len(lines):
        line = lines[index]
        marker = offset + len(line) - len(line.lstrip())
        previous = bisect_right(opaque_starts, marker) - 1
        inside_literal = previous >= 0 and marker < opaque[previous][1]
        if line.lstrip().startswith(b"#") and not inside_literal:
            start, text = offset, line
            offset += len(line)
            index += 1
            while text.rstrip(b"\r\n").endswith(b"\\") and index < len(lines):
                line = lines[index]
                text += line
                offset += len(line)
                index += 1
            opaque.append((start, offset))
            directives.append(_Token(text, start, offset))
        else:
            offset += len(line)
            index += 1
    view = bytearray(source)
    for start, end in opaque:
        view[start:end] = bytes(10 if byte == 10 else 32 for byte in source[start:end])
    if b"/*" in view or b"//" in view:
        return None
    tokens = [_Token(m[0], m.start(), m.end()) for m in _TOKEN.finditer(view)]
    # Unrecognized literal/comment recovery and line-spliced code cannot prove braces.
    if any(t.text in {b'"', b"'", b"\\", b"#"} for t in tokens):
        return None
    return sorted((*tokens, *directives), key=lambda token: token.start)


def _physical_end(tokens: list[_Token], start: int) -> int | None:
    if tokens[start].text != b"{":
        return None
    depth = 0
    groups = []
    for index in range(start, len(tokens)):
        text = tokens[index].text
        conditional = _CONDITIONAL.match(text)
        if conditional is not None:
            if conditional[1] in {b"if", b"ifdef", b"ifndef"}:
                groups.append(depth)
            elif not groups or groups[-1] != depth:
                return None
            elif conditional[1] == b"endif":
                groups.pop()
        elif text == b"{":
            depth += 1
        elif text == b"}":
            depth -= 1
            if depth == 0:
                return None if groups else index
    return None


def _masks(tokens: list[_Token], start: int, end: int) -> tuple[tuple[int, int, str], ...]:
    result = []
    for index in range(start + 1, end):
        token = tokens[index]
        previous = index - 1
        while previous > start and tokens[previous].text.lstrip().startswith(b"#"):
            previous -= 1
        if _MARKER.fullmatch(token.text) is None or tokens[previous].text not in {
            b"{",
            b"}",
            b";",
            b":",
        }:
            continue
        opening, mask_end = index + 1, token.end
        call_style = tokens[opening].text == b"("
        if tokens[opening].text == b"(":
            # Empty or catch-all headers carry no expressions that omission could lose.
            if tokens[opening + 1].text == b")":
                closing = opening + 1
            elif tokens[opening + 1].text == b"..." and tokens[opening + 2].text == b")":
                closing = opening + 2
            else:
                continue
            mask_end, opening = tokens[closing].end, closing + 1
        if tokens[opening].text != b"{":
            continue
        closing = _physical_end(tokens, opening)
        if closing is None or closing >= end:
            continue
        depth, semicolon = 1, False
        for child in tokens[opening + 1 : closing]:
            if child.text == b"{":
                depth += 1
            elif child.text == b"}":
                depth -= 1
            elif child.text == b";" and depth == 1:
                semicolon = True
        # A valid TYPE{...} initializer cannot contain a direct statement semicolon.
        if semicolon or (call_style and closing == opening + 1):
            result.append((token.start, mask_end, token.text.decode()))
    return tuple(result)


def recover_block_bodies(
    parser: Parser, source: bytes, original: Tree
) -> tuple[Tree, tuple[RecoveredBody, ...]]:
    if not original.root_node.has_error:
        return original, ()
    tokens = _tokens(source, original)
    if tokens is None:
        return original, ()
    positions = {token.start: index for index, token in enumerate(tokens)}
    candidates = []
    pending = [original.root_node]
    while pending:
        node = pending.pop()
        pending.extend(node.named_children)
        if node.type != "function_definition":
            continue
        body, declarator = node.child_by_field_name("body"), node.child_by_field_name("declarator")
        if (
            body is None
            or not body.has_error
            or body.start_byte not in positions
            or declarator is None
            or declarator.type != "function_declarator"
            or declarator.has_error
        ):
            continue
        name = declarator.child_by_field_name("declarator")
        if (
            name is not None
            and name.type == "identifier"
            and _MARKER.fullmatch(name.text or b"")
            and node.child_by_field_name("type") is None
        ):
            continue
        start = positions[body.start_byte]
        end = _physical_end(tokens, start)
        if end is None or tokens[end].end < node.end_byte:
            continue
        masks = _masks(tokens, start, end)
        if masks:
            candidates.append(
                RecoveredBody(
                    node.start_byte,
                    tokens[end].end,
                    tuple(node.start_point),
                    node_text(declarator),
                    masks,
                )
            )
    if not candidates:
        return original, ()
    masks = sorted(
        {(start, end) for candidate in candidates for start, end, _ in candidate.markers}
    )
    starts = [0, *(i + 1 for i, byte in enumerate(source) if byte == 10)]

    def point(byte: int) -> tuple[int, int]:
        row = bisect_right(starts, byte) - 1
        return row, byte - starts[row]

    ranges = []
    for allowed in original.included_ranges:
        start, limit = allowed.start_byte, min(allowed.end_byte, len(source))
        for left, right in masks:
            if right <= start or left >= limit:
                continue
            if start < left:
                ranges.append(Range(point(start), point(left), start, left))
            start = max(start, right)
        if start < limit:
            ranges.append(Range(point(start), point(limit), start, limit))
    bounded = Parser(parser.language)
    bounded.included_ranges = ranges
    tree = bounded.parse(source)
    verified = set()
    pending = [tree.root_node]
    while pending:
        node = pending.pop()
        pending.extend(node.named_children)
        declarator = node.child_by_field_name("declarator")
        if node.type == "function_definition" and not node.has_error and declarator is not None:
            nested = list(node.named_children)
            safe = True
            while nested:
                child = nested.pop()
                if child.type == "function_definition":
                    safe = False
                    break
                if child.type not in {"class_specifier", "struct_specifier"}:
                    nested.extend(child.named_children)
            if safe:
                verified.add((node.start_byte, node.end_byte, node_text(declarator)))
    if not all((c.start, c.end, c.declarator) in verified for c in candidates):
        return original, ()
    return tree, tuple(candidates)


def mark_recovered_bodies(graph: CodeGraph, bodies: tuple[RecoveredBody, ...]) -> None:
    by_point = {body.point: body for body in bodies}
    for key, unit in graph.nodes.items():
        body = by_point.get((unit.location.start_line - 1, unit.location.start_col))
        if body is not None and unit.kind in {NodeKind.FUNCTION, NodeKind.METHOD}:
            graph.nodes[key] = replace(
                unit,
                attributes=(
                    *unit.attributes,
                    ("cpp_body_recovered", True),
                    (
                        "cpp_block_macro_spans",
                        json.dumps(
                            [
                                {"name": name, "start_byte": start, "end_byte": end}
                                for start, end, name in body.markers
                            ]
                        ),
                    ),
                ),
            )
