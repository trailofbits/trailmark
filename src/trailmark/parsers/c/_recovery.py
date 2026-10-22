"""Recover physical C bodies without expanding control-flow macros."""

from __future__ import annotations

import re
from dataclasses import dataclass

from tree_sitter import Node, Parser, Tree

from trailmark.parsers._walk import walk_owned

_TOKENS = re.compile(
    rb"(?m)^[ \t]*\#(?:\\\r?\n|\\[^\n]|[^\n\\])*"
    rb"|//[^\n]*|/\*[\s\S]*?\*/"
    rb"|\"(?:\\[\s\S]|[^\"\\])*\"|'(?:\\[\s\S]|[^'\\])*'"
    rb"|[A-Za-z_]\w*|[^\s]"
)
_KEYWORDS = frozenset({b"else", b"do", b"struct", b"union", b"enum"})
_CONDITIONAL = re.compile(rb"\s*#\s*(if|ifdef|ifndef|else|elif|endif)\b")
_DECLARATION_BOUNDARIES = frozenset(
    {"function_definition", "declaration", "type_definition", "struct_specifier", "enum_specifier"}
)


@dataclass(frozen=True)
class _Candidate:
    end: int
    masks: tuple[tuple[int, int], ...]


def recover_bodies(source: bytes, tree: Tree, parser: Parser) -> tuple[Tree, frozenset[int]]:
    """Verify each physical span independently and retain original diagnostics."""
    if not tree.root_node.has_error:
        return tree, frozenset()
    functions = tuple(
        node
        for node in walk_owned(tree.root_node, boundaries=_DECLARATION_BOUNDARIES)
        if node.type == "function_definition"
    )
    if not functions:
        return tree, frozenset()
    tokens = [
        token for token in _TOKENS.finditer(source) if not token.group().startswith((b"//", b"/*"))
    ]
    candidates = _candidates(functions, tokens)
    for _ in range(4):
        if not candidates:
            break
        recovered = _reparse(source, parser, candidates)
        verified = _verified(recovered.root_node, candidates)
        if len(verified) == len(candidates):
            return recovered, frozenset(verified)
        candidates = {start: candidates[start] for start in verified}
    return tree, frozenset()


def _candidates(
    functions: tuple[Node, ...], tokens: list[re.Match[bytes]]
) -> dict[int, _Candidate]:
    starts = {token.start(): index for index, token in enumerate(tokens)}
    candidates: dict[int, _Candidate] = {}
    for function in functions:
        body = function.child_by_field_name("body")
        if body is None or body.start_byte not in starts:
            continue
        start = starts[body.start_byte]
        end = _physical_end(tokens, start)
        if end is None or tokens[end].end() < body.end_byte:
            continue
        if tokens[end].end() == body.end_byte and not body.has_error:
            continue
        masks = _mask_ranges(tokens, start, end)
        if masks:
            candidates[function.start_byte] = _Candidate(tokens[end].end(), masks)
    return candidates


def _physical_end(tokens: list[re.Match[bytes]], start: int) -> int | None:
    depth = 0
    groups: list[int] = []
    for index in range(start, len(tokens)):
        text = tokens[index].group()
        conditional = _CONDITIONAL.match(text)
        if conditional is not None:
            if not _conditional_depth(conditional[1], groups, depth):
                return None
        elif text == b"{":
            depth += 1
        elif text == b"}":
            depth -= 1
            if depth == 0:
                return None if groups else index
    return None


def _conditional_depth(directive: bytes, groups: list[int], depth: int) -> bool:
    if directive in {b"if", b"ifdef", b"ifndef"}:
        groups.append(depth)
    elif not groups or depth != groups[-1]:
        return False
    elif directive == b"endif":
        groups.pop()
    return True


def _mask_ranges(
    tokens: list[re.Match[bytes]], start: int, end: int
) -> tuple[tuple[int, int], ...]:
    statements = [
        token for token in tokens[start : end + 1] if not token.group().lstrip().startswith(b"#")
    ]
    return tuple(
        (statements[index].start(), statements[index].end())
        for index in range(1, len(statements) - 1)
        if _block_macro(statements, index)
    )


def _block_macro(tokens: list[re.Match[bytes]], index: int) -> bool:
    text = tokens[index].group()
    return (
        re.fullmatch(rb"[A-Za-z_]\w*", text) is not None
        and text not in _KEYWORDS
        and tokens[index - 1].group() in {b"{", b"}", b";", b":"}
        and tokens[index + 1].group() == b"{"
    )


def _reparse(source: bytes, parser: Parser, candidates: dict[int, _Candidate]) -> Tree:
    masked = bytearray(source)
    for candidate in candidates.values():
        for start, end in candidate.masks:
            masked[start:end] = b" " * (end - start)
    return parser.parse(bytes(masked))


def _verified(root: Node, candidates: dict[int, _Candidate]) -> set[int]:
    return {
        node.start_byte
        for node in walk_owned(root, boundaries=_DECLARATION_BOUNDARIES)
        if node.type == "function_definition"
        and node.start_byte in candidates
        and node.end_byte == candidates[node.start_byte].end
    }
