"""Recover standard enum typedefs without changing physical source coordinates."""

import re
from typing import NamedTuple

from tree_sitter import Node, Parser, Range, Tree
from tree_sitter_language_pack import get_language

from trailmark.parsers._common import node_text

_MACROS = frozenset({b"NS_ENUM", b"NS_OPTIONS"})
_CONTEXTS = frozenset(
    {"ERROR", "type_definition", "preproc_if", "preproc_ifdef", "preproc_else", "preproc_elif"}
)
_TOKEN = re.compile(
    rb'//[^\n]*|/\*[\s\S]*?(?:\*/|$)|"(?:\\[\s\S]|[^"\\])*(?:"|$)|'
    rb"'(?:\\[\s\S]|[^'\\])*(?:'|$)|[A-Za-z_][A-Za-z_0-9]*|[^\s]"
)
_IDENTIFIER = re.compile(rb"[A-Za-z_][A-Za-z_0-9]*\Z")


class EnumMacro(NamedTuple):
    name: str
    macro: str
    start: int
    end: int


def _tokens(source: bytes) -> list[re.Match[bytes]] | None:
    result = []
    for token in _TOKEN.finditer(source):
        text = token[0]
        if text.startswith(b"/*"):
            if not text.endswith(b"*/"):
                return None
        elif not text.startswith(b"//"):
            if text.startswith((b'"', b"'")) and (len(text) < 2 or text[-1:] != text[:1]):
                return None
            result.append(token)
    return result


def _declaration(
    node: Node,
    tokens: list[re.Match[bytes]],
    starts: dict[int, int],
    source: bytes,
    enum_parser: Parser,
) -> EnumMacro | None:
    index = starts.get(node.start_byte, 0)
    if index < 1 or tokens[index - 1][0] != b"typedef":
        return None
    parent = node.parent
    if parent is None or parent.start_byte != tokens[index - 1].start():
        return None
    while parent is not None and parent.type in _CONTEXTS:
        parent = parent.parent
    if parent is None or parent.type != "translation_unit":
        return None
    values = [token[0] for token in tokens]
    if values[index + 1 : index + 2] != [b"("]:
        return None
    cursor = index + 2
    underlying = []
    while cursor < len(tokens) and _IDENTIFIER.fullmatch(values[cursor]):
        underlying.append(values[cursor])
        cursor += 1
    if not underlying or len(underlying) > 3 or values[cursor : cursor + 1] != [b","]:
        return None
    cursor += 1
    if cursor + 3 >= len(tokens) or not _IDENTIFIER.fullmatch(values[cursor]):
        return None
    name = values[cursor]
    if values[cursor + 1 : cursor + 3] != [b")", b"{"]:
        return None
    opening = cursor + 2
    cursor, depth = opening, 0
    while cursor < len(tokens):
        depth += (values[cursor] == b"{") - (values[cursor] == b"}")
        if depth == 0:
            break
        cursor += 1
    if values[cursor + 1 : cursor + 2] != [b";"]:
        return None
    body = source[tokens[opening].start() : tokens[cursor].end()]
    # Validate the original enum body as C syntax; this text is never executed.
    if enum_parser.parse(b"enum _TrailmarkEnum " + body + b";").root_node.has_error:
        return None
    return EnumMacro(
        name.decode(), values[index].decode(), tokens[index - 1].start(), tokens[cursor + 1].end()
    )


def point(source: bytes, offset: int) -> tuple[int, int]:
    return source.count(b"\n", 0, offset), offset - source.rfind(b"\n", 0, offset) - 1


def recover_enum_macros(
    parser: Parser, source: bytes, original: Tree
) -> tuple[Tree, list[EnumMacro]]:
    """Admit complete AST-backed enum typedefs in translation-unit contexts."""
    tokens = _tokens(source)
    if tokens is None:
        return original, []
    starts = {token.start(): index for index, token in enumerate(tokens)}
    pending = [original.root_node]
    while pending:
        node = pending.pop()
        if node.type in {
            "preproc_def",
            "preproc_function_def",
            "preproc_undef",
            "preproc_call",
        } and any(macro in (node.text or b"") for macro in _MACROS):
            return original, []
        pending.extend(node.named_children)
    tree = original
    recovered = []
    enum_parser = Parser(get_language("c"))
    for _ in range(64):
        candidates = []
        pending = [tree.root_node]
        while pending:
            node = pending.pop()
            if (
                node.type == "macro_type_specifier"
                and (name := node.child_by_field_name("name")) is not None
                and node_text(name).encode() in _MACROS
            ):
                fact = _declaration(node, tokens, starts, source, enum_parser)
                if fact is not None:
                    candidates.append(fact)
            pending.extend(node.named_children)
        if not candidates:
            break
        fact = min(candidates, key=lambda item: item.start)
        ranges = []
        for selected in tree.included_ranges:
            start, end = selected.start_byte, min(selected.end_byte, len(source))
            for left, right in ((start, min(end, fact.start)), (max(start, fact.end), end)):
                if left < right:
                    ranges.append(Range(point(source, left), point(source, right), left, right))
        if not ranges:
            return Parser(parser.language).parse(b""), [*recovered, fact]
        bounded = Parser(parser.language)
        bounded.included_ranges = ranges
        tree = bounded.parse(source)
        recovered.append(fact)
    return tree, recovered
