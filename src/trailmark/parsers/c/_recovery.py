"""Recover physical C bodies without expanding control-flow macros."""

from __future__ import annotations

import re

from tree_sitter import Parser, Tree

from trailmark.parsers._walk import walk_owned

_TOKENS = re.compile(
    rb"(?m)^[ \t]*\#(?:\\\r?\n|\\[^\n]|[^\n\\])*"
    rb"|//[^\n]*|/\*[\s\S]*?\*/"
    rb"|\"(?:\\[\s\S]|[^\"\\])*\"|'(?:\\[\s\S]|[^'\\])*'"
    rb"|[A-Za-z_]\w*|[^\s]"
)
_KEYWORDS = frozenset({b"else", b"do", b"struct", b"union", b"enum"})
_CONDITIONAL = re.compile(rb"\s*#\s*(?:if|ifdef|ifndef|else|elif|endif)\b")


def recover_bodies(source: bytes, tree: Tree, parser: Parser) -> tuple[Tree, frozenset[int]]:
    """Retain byte offsets while repairing bare macro tokens before blocks.

    Recovery requires a balanced physical body and a complete reparsed function.
    Conditional preprocessor branches are excluded because physical brace
    balance need not describe any compiled configuration. Original diagnostics
    must still be retained by the caller.
    """
    if not tree.root_node.has_error:
        return tree, frozenset()
    tokens = [
        token for token in _TOKENS.finditer(source) if not token.group().startswith((b"//", b"/*"))
    ]
    starts = {token.start(): index for index, token in enumerate(tokens)}
    masked = bytearray(source)
    expected: dict[int, int] = {}
    for function in walk_owned(tree.root_node):
        if function.type != "function_definition":
            continue
        body = function.child_by_field_name("body")
        if body is None or body.start_byte not in starts:
            continue
        start = starts[body.start_byte]
        depth = 0
        end = None
        for index in range(start, len(tokens)):
            text = tokens[index].group()
            if _CONDITIONAL.match(text):
                break
            if text == b"{":
                depth += 1
            elif text == b"}":
                depth -= 1
                if depth == 0:
                    end = index
                    break
        if end is None or tokens[end].end() <= body.end_byte:
            continue
        changed = False
        for index in range(start + 1, end):
            token = tokens[index]
            text = token.group()
            if (
                re.fullmatch(rb"[A-Za-z_]\w*", text)
                and text not in _KEYWORDS
                and tokens[index - 1].group() in {b"{", b"}", b";", b":"}
                and tokens[index + 1].group() == b"{"
            ):
                masked[token.start() : token.end()] = b" " * len(text)
                changed = True
        if changed:
            expected[function.start_byte] = tokens[end].end()
    if not expected:
        return tree, frozenset()
    recovered = parser.parse(bytes(masked))
    functions = {
        node.start_byte: node
        for node in walk_owned(recovered.root_node)
        if node.type == "function_definition"
    }
    if any(
        start not in functions or functions[start].end_byte != end or functions[start].has_error
        for start, end in expected.items()
    ):
        return tree, frozenset()
    return recovered, frozenset(expected)
