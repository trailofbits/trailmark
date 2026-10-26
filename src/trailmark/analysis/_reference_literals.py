"""Extract exact string values from structured data files.

Data files often name code by qualified symbol or path. This module reports
only the string *values* a syntax parser places in value position, decoded
with the file format's own escape rules, with their source spans. Keys,
comments, descriptions and markup names are not values. Nothing is resolved,
expanded or executed, and the module makes no assumptions about which keys
or directives carry references; callers decide what each value names.
"""

from __future__ import annotations

import json
import re
import tomllib
from bisect import bisect_right
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from tree_sitter import Node, Parser
from tree_sitter_language_pack import get_language

from trailmark.models.nodes import SourceLocation
from trailmark.parsers.graphql._syntax import decode_graphql_string, mask_graphql_escapes

_MAX_BYTES = 8 * 1024 * 1024
_LANGUAGES = {
    ".graphql": "graphql",
    ".gql": "graphql",
    ".graphqls": "graphql",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".xml": "xml",
    ".toml": "toml",
}

_Literal = tuple[str, int, int]


@dataclass(frozen=True)
class LiteralReference:
    """A decoded string value and the span of its source token."""

    value: str
    location: SourceLocation


@dataclass(frozen=True)
class LiteralScan:
    literals: tuple[LiteralReference, ...] = ()
    issues: tuple[str, ...] = ()


def reference_literals(path: str) -> list[LiteralReference]:
    """Return decoded string values from a supported data file in source order.

    Unsupported extensions, unreadable or oversized files and files with any
    syntax error yield an empty list. Values whose escapes or references
    cannot be decoded exactly are omitted rather than approximated, as are
    empty strings.

    Args:
        path: GraphQL, JSON, YAML, XML or TOML file to inspect.

    Returns:
        Literal values with spans covering the whole token, quotes included.
    """
    return list(scan_reference_literals(path).literals)


def scan_reference_literals(path: str) -> LiteralScan:
    """Keep failures to inspect a file visible to discovery consumers."""
    language = _LANGUAGES.get(Path(path).suffix.lower())
    source = _read(path) if language else None
    if language is None:
        return LiteralScan(issues=("unsupported_extension",))
    if source is None:
        return LiteralScan(issues=("unreadable_or_over_8_mib",))
    parsed = mask_graphql_escapes(source) if language == "graphql" else source
    root = Parser(get_language(language)).parse(parsed).root_node
    if root.has_error:
        return LiteralScan(issues=("invalid_or_unsupported_syntax",))
    keep, decode = _SYNTAX[language]
    lines = _LineIndex(source)
    return LiteralScan(
        tuple(
            LiteralReference(value, lines.location(path, start, end))
            for node in _walk(root, keep)
            for value, start, end in decode(node, source)
            if value
        )
    )


def _read(path: str) -> bytes | None:
    try:
        if not Path(path).is_file():
            return None
        with open(path, "rb") as handle:
            data = handle.read(_MAX_BYTES + 1)
    except OSError:
        return None
    return data if len(data) <= _MAX_BYTES else None


class _LineIndex:
    """Convert byte offsets to tree-sitter style rows and byte columns."""

    def __init__(self, source: bytes) -> None:
        self._starts = [0, *(m.end() for m in re.finditer(rb"\n", source))]

    def _point(self, offset: int) -> tuple[int, int]:
        row = bisect_right(self._starts, offset) - 1
        return row, offset - self._starts[row]

    def location(self, path: str, start: int, end: int) -> SourceLocation:
        start_row, start_col = self._point(start)
        end_row, end_col = self._point(end)
        return SourceLocation(path, start_row + 1, end_row + 1, start_col, end_col)


def _walk(root: Node, keep: Callable[[Node, str | None, Node], bool]) -> Iterator[Node]:
    """Visit nodes in source order without recursion, pruning rejected subtrees."""
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        children = node.children
        for index in range(len(children) - 1, -1, -1):
            child = children[index]
            if keep(child, node.field_name_for_child(index), node):
                stack.append(child)


def _text(source: bytes, start: int, end: int) -> str | None:
    try:
        return source[start:end].decode("utf-8")
    except UnicodeDecodeError:
        return None


def _span(node: Node) -> tuple[int, int]:
    return node.start_byte, node.end_byte


def _token(node: Node, value: str | None) -> Iterator[_Literal]:
    if value is not None:
        yield value, node.start_byte, node.end_byte


def _keep_all(child: Node, field: str | None, parent: Node) -> bool:
    return True


# --- JSON -----------------------------------------------------------------


def _keep_json(child: Node, field: str | None, parent: Node) -> bool:
    return field != "key"


def _json_literals(node: Node, source: bytes) -> Iterator[_Literal]:
    if node.type == "string":
        yield from _token(node, _json_string(_text(source, *_span(node)), strict=True))


def _json_string(text: str | None, *, strict: bool) -> str | None:
    if text is None:
        return None
    try:
        value = json.loads(text, strict=strict)
    except ValueError:
        return None
    return value if isinstance(value, str) else None


# --- TOML -----------------------------------------------------------------


def _toml_literals(node: Node, source: bytes) -> Iterator[_Literal]:
    # Keys are quoted_key/bare_key nodes, so every string node is a value.
    text = _text(source, *_span(node)) if node.type == "string" else None
    if text is None:
        return
    try:
        value = tomllib.loads(f"v = {text}")["v"]
    except tomllib.TOMLDecodeError:
        return
    yield from _token(node, value if isinstance(value, str) else None)


# --- GraphQL --------------------------------------------------------------


def _graphql_literals(node: Node, source: bytes) -> Iterator[_Literal]:
    # Strings under a value node are arguments, defaults, list items or object
    # fields; descriptions use the same token under a description node.
    string = node.named_child(0) if node.type == "value" else None
    if string is None or string.type != "string_value":
        return
    text = _text(source, *_span(string))
    if text is None:
        return
    if text.startswith('"""'):
        value: str | None = _graphql_block_string(text[3:-3])
    else:
        value = decode_graphql_string(text)
    yield from _token(string, value)


def _graphql_block_string(raw: str) -> str:
    """Apply the GraphQL BlockStringValue algorithm."""
    lines = re.split(r"\r\n|\r|\n", raw.replace('\\"""', '"""'))
    indents = [len(line) - len(line.lstrip(" \t")) for line in lines[1:] if line.strip(" \t")]
    common = min(indents, default=0)
    lines = [lines[0], *(line[common:] for line in lines[1:])]
    blank = [not line.strip(" \t") for line in lines]
    first = blank.index(False) if False in blank else len(lines)
    last = len(lines) - blank[::-1].index(False) if False in blank else first
    return "\n".join(lines[first:last])


# --- XML ------------------------------------------------------------------

_XML_TEXT = frozenset({"CharData", "EntityRef", "CharRef", "CDSect"})
_XML_SPACE = b" \t\r\n"
_XML_PREDEFINED = {"amp": "&", "lt": "<", "gt": ">", "apos": "'", "quot": '"'}
_XML_REFERENCE = re.compile(
    r"<!\[CDATA\[(?P<cdata>.*?)\]\]>|&#x(?P<hex>[0-9A-Fa-f]+);|&#(?P<dec>[0-9]+);"
    r"|&(?P<name>[^;&\s]+);",
    re.DOTALL,
)


def _keep_xml(child: Node, field: str | None, parent: Node) -> bool:
    # DTD declarations hold entity and default-attribute text, not document values.
    return child.type != "doctypedecl"


def _xml_literals(node: Node, source: bytes) -> Iterator[_Literal]:
    if node.type == "AttValue":
        text = _text(source, node.start_byte + 1, node.end_byte - 1)
        yield from _token(node, _xml_decode(text, attribute=True))
    elif node.type == "content":
        for start, end in _xml_text_runs(node, source):
            yield from _xml_run(source, start, end)


def _xml_text_runs(content: Node, source: bytes) -> Iterator[tuple[int, int]]:
    """Yield contiguous character data spans; elements, comments and PIs split runs."""
    start: int | None = None
    end = 0
    for child in content.children:
        if child.type in _XML_TEXT:
            start = child.start_byte if start is None else start
            end = child.end_byte
        elif start is not None:
            yield start, end
            start = None
    if start is not None:
        yield start, end


def _xml_run(source: bytes, start: int, end: int) -> Iterator[_Literal]:
    # Trim insignificant indentation; a run starting with CDATA or a reference
    # starts with markup, so only raw character data whitespace is removed.
    raw = source[start:end]
    start += len(raw) - len(raw.lstrip(_XML_SPACE))
    end -= len(raw) - len(raw.rstrip(_XML_SPACE))
    value = _xml_decode(_text(source, start, end), attribute=False) if start < end else None
    if value is not None:
        yield value, start, end


def _xml_decode(raw: str | None, *, attribute: bool) -> str | None:
    """Decode character data without expanding entities beyond the predefined five."""
    if raw is None:
        return None
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    parts: list[str] = []
    position = 0
    for match in _XML_REFERENCE.finditer(raw):
        replacement = _xml_reference(match)
        if replacement is None:
            return None
        parts += [_xml_chars(raw[position : match.start()], attribute), replacement]
        position = match.end()
    parts.append(_xml_chars(raw[position:], attribute))
    return "".join(parts)


def _xml_chars(text: str, attribute: bool) -> str:
    return text.replace("\t", " ").replace("\n", " ") if attribute else text


def _xml_reference(match: re.Match[str]) -> str | None:
    if match["cdata"] is not None:
        return match["cdata"]
    if match["name"] is not None:
        return _XML_PREDEFINED.get(match["name"])
    code = int(match["hex"], 16) if match["hex"] is not None else int(match["dec"])
    allowed = code in (0x9, 0xA, 0xD) or 0x20 <= code <= 0xD7FF
    allowed = allowed or 0xE000 <= code <= 0xFFFD or 0x10000 <= code <= 0x10FFFF
    return chr(code) if allowed else None


# --- YAML -----------------------------------------------------------------

_YAML_ESCAPES = {
    "0": "\0",
    "a": "\a",
    "b": "\b",
    "t": "\t",
    "\t": "\t",
    "n": "\n",
    "v": "\v",
    "f": "\f",
    "r": "\r",
    "e": "\x1b",
    " ": " ",
    '"': '"',
    "/": "/",
    "\\": "\\",
    "N": "\x85",
    "_": "\xa0",
    "L": "\u2028",
    "P": "\u2029",
}
_YAML_HEX_ESCAPES = {"x": 2, "u": 4, "U": 8}
_HEX_DIGITS = re.compile(r"[0-9A-Fa-f]*")
_YAML_BLANK_LINES = re.compile(rb"(?:[ \t]*(?:\r\n|\r|\n))*")


def _keep_yaml(child: Node, field: str | None, parent: Node) -> bool:
    # `{a, b: c}` gives `a` as an implicit key with no pair node around it.
    return field != "key" and not (parent.type == "flow_mapping" and child.type == "flow_node")


def _yaml_literals(node: Node, source: bytes) -> Iterator[_Literal]:
    kind = node.type
    if kind == "block_scalar":
        yield from _token(node, _yaml_block_scalar(node, source))
        return
    text = _text(source, *_span(node))
    if text is None:
        return
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if kind == "double_quote_scalar":
        yield from _token(node, _yaml_fold(text[1:-1], '"'))
    elif kind == "single_quote_scalar":
        yield from _token(node, _yaml_fold(text[1:-1], "'"))
    elif kind == "plain_scalar" and _is_yaml_string(node):
        yield from _token(node, _yaml_fold(text, ""))


def _is_yaml_string(plain: Node) -> bool:
    # Plain scalars resolving to numbers, booleans or null are not strings.
    child = plain.named_child(0)
    return child is not None and child.type == "string_scalar"


def _yaml_fold(text: str, quote: str) -> str | None:
    """Fold flow scalar line breaks and decode the quoting style's escapes."""
    out: list[str] = []
    pending = ""
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\n":
            index, breaks = _yaml_skip_breaks(text, index)
            out.append("\n" * (breaks - 1) if breaks > 1 else " ")
            pending = ""
            continue
        if char in " \t":
            pending += char
            index += 1
            continue
        piece, index = _yaml_flow_char(text, index, quote)
        if piece is None:
            return None
        out.append(pending + piece)
        pending = ""
    return "".join(out) + pending


def _yaml_flow_char(text: str, index: int, quote: str) -> tuple[str | None, int]:
    if quote == "'" and text.startswith("''", index):
        return "'", index + 2
    if quote == '"' and text[index] == "\\":
        return _yaml_escape(text, index + 1)
    return text[index], index + 1


def _yaml_skip_breaks(text: str, index: int) -> tuple[int, int]:
    """Consume line breaks and indentation, returning the next index and break count."""
    breaks = 0
    while index < len(text) and text[index] == "\n":
        breaks += 1
        index += 1
        while index < len(text) and text[index] in " \t":
            index += 1
    return index, breaks


def _yaml_escape(text: str, index: int) -> tuple[str | None, int]:
    char = text[index] if index < len(text) else ""
    if char == "\n":
        # An escaped break joins lines without a folding space; any empty
        # lines that follow it still produce line feeds.
        index, breaks = _yaml_skip_breaks(text, index)
        return "\n" * (breaks - 1), index
    if char in _YAML_ESCAPES:
        return _YAML_ESCAPES[char], index + 1
    width = _YAML_HEX_ESCAPES.get(char, 0)
    digits = text[index + 1 : index + 1 + width]
    if not width or not _HEX_DIGITS.fullmatch(digits) or len(digits) != width:
        return None, index
    code = int(digits, 16)
    return (chr(code) if code <= 0x10FFFF else None), index + 1 + width


def _yaml_block_scalar(node: Node, source: bytes) -> str | None:
    """Decode a `|` or `>` block scalar whose indentation is auto-detected."""
    # Keep chomping retains blank lines the grammar may leave outside the node.
    tail = _YAML_BLANK_LINES.match(source, node.end_byte)
    text = _text(source, node.start_byte, tail.end() if tail else node.end_byte)
    if text is None:
        return None
    header, _, body = text.replace("\r\n", "\n").replace("\r", "\n").partition("\n")
    indicators = header.split("#", 1)[0].strip()
    if any(char.isdigit() for char in indicators):
        return None
    lines = body.split("\n")
    indent = next((len(line) - len(line.lstrip(" ")) for line in lines if line.strip()), 0)
    last = max((i for i, line in enumerate(lines) if line.strip()), default=-1)
    kept = [line[indent:] for line in lines[: last + 1]]
    text = _yaml_fold_block(kept) if indicators.startswith(">") else "\n".join(kept)
    trailing = len(lines) - 1 - last
    if "-" in indicators or (last < 0 and "+" not in indicators):
        return text
    return text + "\n" * (trailing if "+" in indicators else min(trailing, 1))


def _yaml_fold_block(lines: list[str]) -> str:
    """Fold `>` content: more-indented lines and blank lines keep their breaks."""
    out: list[str] = []
    previous: str | None = None
    empty = 0
    for line in lines:
        if not line:
            empty += 1
            continue
        if previous is None:
            out.append("\n" * empty)
        elif previous[0] in " \t" or line[0] in " \t":
            out.append("\n" * (empty + 1))
        else:
            out.append("\n" * empty if empty else " ")
        out.append(line)
        previous, empty = line, 0
    return "".join(out)


_Decoder = Callable[[Node, bytes], Iterator[_Literal]]
_Keep = Callable[[Node, str | None, Node], bool]
_SYNTAX: dict[str, tuple[_Keep, _Decoder]] = {
    "graphql": (_keep_all, _graphql_literals),
    "json": (_keep_json, _json_literals),
    "toml": (_keep_all, _toml_literals),
    "xml": (_keep_xml, _xml_literals),
    "yaml": (_keep_yaml, _yaml_literals),
}
