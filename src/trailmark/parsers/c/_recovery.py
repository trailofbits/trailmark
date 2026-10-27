"""Recover physical C bodies without expanding control-flow macros."""

from __future__ import annotations

import re
from dataclasses import dataclass

from tree_sitter import Node, Parser, Tree

from trailmark.parsers._walk import walk_owned

_TOKENS = re.compile(
    rb"(?m)^[ \t]*\#(?:\\\r?\n|\\[^\n]|[^\n\\])*"
    rb"|//[^\n]*|/\*[\s\S]*?\*/"
    rb"|\"(?:\\[\s\S]|[^\"\\\r\n])*\"|'(?:\\[\s\S]|[^'\\\r\n])*'"
    rb"|[A-Za-z_]\w*|[^\s]"
)
_KEYWORDS = frozenset({b"else", b"do", b"struct", b"union", b"enum"})
_BLOCK_START = re.compile(rb"\b([A-Za-z_]\w*)(?:\s|/\*[^*]*\*+(?:[^/*][^*]*\*+)*/|//[^\n]*\n)*\{")
_IDENTIFIER = re.compile(rb"[A-Za-z_]\w*\Z")
_CONDITIONAL = re.compile(rb"\s*#\s*(if|ifdef|ifndef|else|elif|endif)\b")
_DISABLED_START = re.compile(rb"\s*#\s*if\s+0\s*(?://[^\n]*|/\*.*?\*/\s*)?\Z")
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
    source, tree = _unwrap_cpp_namespace(source, tree, parser)
    functions = _recovery_functions(tree.root_node, source)
    if not functions:
        return tree, frozenset()
    tokens = _source_tokens(source)
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


def _unwrap_cpp_namespace(source: bytes, tree: Tree, parser: Parser) -> tuple[bytes, Tree]:
    """Expose C declarations inside a pair of guarded C++ namespace braces.

    The C grammar reads ``namespace NAME {`` as a function definition, swallowing
    the translation unit. Only unwrap the split, matching ``#ifdef NAME`` guards;
    do not interpret arbitrary C++ or conditional compilation.
    """
    masked = bytearray(source)
    changed = False
    for node in walk_owned(tree.root_node, boundaries=_DECLARATION_BOUNDARIES):
        if node.type != "function_definition":
            continue
        type_node = node.child_by_field_name("type")
        declarator = node.child_by_field_name("declarator")
        body = node.child_by_field_name("body")
        parent = node.parent
        if (
            type_node is None
            or type_node.text != b"namespace"
            or declarator is None
            or declarator.type != "identifier"
            or body is None
            or parent is None
            or parent.type != "preproc_ifdef"
            or parent.children[0].type != "#ifdef"
        ):
            continue
        children = [child for child in body.named_children if child.type != "comment"]
        if len(children) < 2:
            continue
        first, last = children[0], children[-1]
        guard = parent.child_by_field_name("name")
        closing_guard = last.child_by_field_name("name")
        if (
            guard is None
            or first.type != "preproc_call"
            or first.text is None
            or first.text.strip() != b"#endif"
            or last.type != "preproc_ifdef"
            or last.children[0].type != "#ifdef"
            or closing_guard is None
            or closing_guard.text != guard.text
            or source[body.end_byte - 1 : body.end_byte] != b"}"
        ):
            continue
        for index in range(node.start_byte, body.start_byte + 1):
            if masked[index] not in (10, 13):
                masked[index] = 32
        masked[body.end_byte - 1] = 32
        changed = True
    if not changed:
        return source, tree
    result = bytes(masked)
    return result, parser.parse(result)


def _needs_recovery(node: Node, source: bytes) -> bool:
    if node.type != "function_definition" or not node.has_error:
        return False
    body = node.child_by_field_name("body")
    return body is not None and any(
        match[1] not in _KEYWORDS
        for match in _BLOCK_START.finditer(source, body.start_byte, body.end_byte)
    )


def _recovery_functions(root: Node, source: bytes) -> tuple[Node, ...]:
    return tuple(
        node
        for node in walk_owned(root, boundaries=_DECLARATION_BOUNDARIES)
        if _needs_recovery(node, source)
    )


def _source_tokens(source: bytes) -> list[re.Match[bytes]]:
    tokens = []
    inactive = 0
    for token in _TOKENS.finditer(source):
        text = token.group()
        if inactive:
            directive = _CONDITIONAL.match(text)
            if directive is not None:
                inactive = _disabled_depth(directive[1], inactive)
                tokens.append(token)
        elif text[0] in (35, 32, 9) and _DISABLED_START.fullmatch(text):
            inactive = 1
            tokens.append(token)
        elif not text.startswith((b"//", b"/*")):
            tokens.append(token)
    return tokens


def _disabled_depth(directive: bytes, depth: int) -> int:
    if directive in {b"if", b"ifdef", b"ifndef"}:
        return depth + 1
    if directive == b"endif":
        return depth - 1
    if depth == 1 and directive in {b"else", b"elif"}:
        return 0
    return depth


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
        tokens[index + 1].group() == b"{"
        and tokens[index - 1].group() in {b"{", b"}", b";", b":"}
        and text not in _KEYWORDS
        and _IDENTIFIER.fullmatch(text) is not None
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
