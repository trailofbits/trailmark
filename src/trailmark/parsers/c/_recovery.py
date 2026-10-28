"""Recover physical C bodies without expanding control-flow macros."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from tree_sitter import Node, Parser, Tree

from trailmark.parsers._walk import walk_owned

_LINE_SPLICE = re.compile(rb"\\\r?\n")
_COMMENT_START = re.compile(rb"/(?:\\\r?\n)*[/*]")
_LINE_COMMENT = rb"/(?:\\\r?\n)*/(?:\\\r?\n|[^\n])*+"
_BLOCK_COMMENT = rb"/(?:\\\r?\n)*\*[\s\S]*?(?:\*(?:\\\r?\n)*/|\Z)"
_COMMENTS = _LINE_COMMENT + rb"|" + _BLOCK_COMMENT
_LITERALS = (
    rb'"(?:\\\r?\n|\\(?:\\\r?\n)*+[^\r\n]|[^"\\\r\n])*+"'
    rb"|'(?:\\\r?\n|\\(?:\\\r?\n)*+[^\r\n]|[^'\\\r\n])*+'"
)
_OPAQUE = re.compile(_COMMENTS + rb"|" + _LITERALS)
_TOKENS = re.compile(
    rb"(?m)^[ \t\v\f\r]*(?:"
    + _BLOCK_COMMENT
    + rb"[ \t\v\f\r]*)*+\#(?:"
    + _COMMENTS
    + rb"|"
    + _LITERALS
    + rb"|\\\r?\n|[^\n])*+"
    rb"|" + _COMMENTS + rb"|" + _LITERALS + rb"|[A-Za-z_](?:\w|\\\r?\n)*+|\\\r?\n|[^\s]"
)
_KEYWORDS = frozenset({b"else", b"do", b"struct", b"union", b"enum", b"__try", b"__finally"})
_IDENTIFIER = re.compile(rb"[A-Za-z_]\w*\Z")
_CONDITIONAL = re.compile(rb"\s*#\s*(if|ifdef|ifndef|else|elif|elifdef|elifndef|endif)\b")
_DISABLED_START = re.compile(rb"\s*#\s*(?:if|elif)\s+0\s*\Z")
_ENDIF = re.compile(rb"#\s*endif\Z")
_DECLARATION_BOUNDARIES = frozenset(
    {"function_definition", "declaration", "type_definition", "struct_specifier", "enum_specifier"}
)


@dataclass(frozen=True)
class _Candidate:
    end: int
    masks: tuple[tuple[int, int], ...]
    literals: tuple[tuple[int, int], ...] = ()


def recover_bodies(source: bytes, tree: Tree, parser: Parser) -> tuple[Tree, frozenset[int]]:
    """Verify each physical span independently and retain original diagnostics."""
    # Spliced macro names can look like valid nested definitions to the grammar.
    if not tree.root_node.has_error and (
        _LINE_SPLICE.search(source) is None
        or not any(
            node.type == "function_definition"
            and (body := node.child_by_field_name("body")) is not None
            and _LINE_SPLICE.search(source, body.start_byte, body.end_byte) is not None
            for node in walk_owned(tree.root_node, boundaries=_DECLARATION_BOUNDARIES)
        )
    ):
        return tree, frozenset()
    source, tree = _unwrap_cpp_namespace(source, tree, parser)
    ignored: list[tuple[int, int]] = []
    literals: list[tuple[int, int]] = []
    tokens = _source_tokens(source, ignored, literals)
    # Opaque preprocessor text can hide the function in the original error tree.
    # This view is only for discovery; each candidate must verify its own masks.
    provisional = (
        parser.parse(_masked_source(source, ignored, literals)) if ignored or literals else tree
    )
    functions = _recovery_functions(provisional.root_node, source)
    if not functions:
        return tree, frozenset()
    candidates = _candidates(functions, tokens)
    candidates = {
        start: _Candidate(
            candidate.end,
            candidate.masks
            + tuple(span for span in ignored if start <= span[0] and span[1] <= candidate.end),
            tuple(span for span in literals if start <= span[0] and span[1] <= candidate.end),
        )
        for start, candidate in candidates.items()
    }
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
        directive = first.child_by_field_name("directive")
        if (
            guard is None
            or first.type != "preproc_call"
            or directive is None
            or _ENDIF.fullmatch(directive.text or b"") is None
            or any(child.type != "comment" and child != directive for child in first.named_children)
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
    if node.type != "function_definition":
        return False
    body = node.child_by_field_name("body")
    if body is None:
        return False
    body_source = source[body.start_byte : body.end_byte]
    if not node.has_error and _LINE_SPLICE.search(body_source) is None:
        return False
    tokens = _source_tokens(body_source)
    return bool(_mask_ranges(tokens, 0, len(tokens) - 1))


def _recovery_functions(root: Node, source: bytes) -> tuple[Node, ...]:
    return tuple(
        node
        for node in walk_owned(root, boundaries=_DECLARATION_BOUNDARIES)
        if _needs_recovery(node, source)
    )


def _directive_text(text: bytes) -> bytes:
    """Remove directive trivia after line splicing; never evaluate expressions."""
    if b"#" not in text:
        return text
    text = _LINE_SPLICE.sub(b"", text)
    return _OPAQUE.sub(
        lambda match: b" " if _COMMENT_START.match(match[0]) is not None else match[0], text
    )


def _source_tokens(
    source: bytes,
    ignored: list[tuple[int, int]] | None = None,
    literals: list[tuple[int, int]] | None = None,
) -> list[re.Match[bytes]]:
    """Keep physical offsets while omitting comments and literal-zero arms."""
    tokens = []
    parents: list[bool] = []
    inactive = False
    for token in _TOKENS.finditer(source):
        text = token.group()
        normalized = _directive_text(text)
        directive = _CONDITIONAL.match(normalized)
        if directive is not None:
            if directive[1] in {b"if", b"ifdef", b"ifndef"}:
                parents.append(inactive)
                inactive = inactive or _DISABLED_START.fullmatch(normalized) is not None
            elif parents:
                if directive[1] == b"endif":
                    inactive = parents.pop()
                else:
                    inactive = parents[-1] or _DISABLED_START.fullmatch(normalized) is not None
            tokens.append(token)
        elif inactive or _LINE_SPLICE.fullmatch(text) is not None:
            if ignored is not None:
                ignored.append((token.start(), token.end()))
        elif _COMMENT_START.match(text) is None or normalized.lstrip().startswith(b"#"):
            tokens.append(token)
            if ignored is not None and normalized.lstrip().startswith(b"#"):
                ignored.append((token.start(), token.end()))
            if (
                literals is not None
                and text[:1] in (b'"', b"'")
                and _LINE_SPLICE.search(text) is not None
            ):
                literals.append((token.start(), token.end()))
        elif ignored is not None and _LINE_SPLICE.search(text) is not None:
            # The grammar cannot read comment delimiters split across lines.
            ignored.append((token.start(), token.end()))
    return tokens


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
        masks = _mask_ranges(tokens, start, end)
        if masks:
            candidates[function.start_byte] = _Candidate(tokens[end].end(), masks)
    return candidates


def _physical_end(tokens: list[re.Match[bytes]], start: int) -> int | None:
    depth = 0
    groups: list[int] = []
    for index in range(start, len(tokens)):
        text = tokens[index].group()
        conditional = _CONDITIONAL.match(_directive_text(text))
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
        token
        for token in tokens[start : end + 1]
        if not _directive_text(token.group()).lstrip().startswith(b"#")
    ]
    return tuple(
        (statements[index].start(), statements[index].end())
        for index in range(1, len(statements) - 1)
        if _block_macro(statements, index)
    )


def _block_macro(tokens: list[re.Match[bytes]], index: int) -> bool:
    text = _LINE_SPLICE.sub(b"", tokens[index].group())
    return (
        tokens[index + 1].group() == b"{"
        and tokens[index - 1].group() in {b"{", b"}", b";", b":"}
        and text not in _KEYWORDS
        and _IDENTIFIER.fullmatch(text) is not None
    )


def _reparse(source: bytes, parser: Parser, candidates: dict[int, _Candidate]) -> Tree:
    masks = (span for candidate in candidates.values() for span in candidate.masks)
    literals = (span for candidate in candidates.values() for span in candidate.literals)
    return parser.parse(_masked_source(source, masks, literals))


def _masked_source(
    source: bytes,
    masks: Iterable[tuple[int, int]],
    literals: Iterable[tuple[int, int]] = (),
) -> bytes:
    masked = bytearray(source)
    for start, end in masks:
        masked[start:end] = bytes(byte if byte in (10, 13) else 32 for byte in masked[start:end])
    for start, end in literals:
        # The grammar can misread line splices inside literals. Use an opaque
        # expression in the verified body without exposing their contents.
        masked[start:end] = bytes(byte if byte in (10, 13) else 32 for byte in masked[start:end])
        masked[start] = ord("0")
    return bytes(masked)


def _verified(root: Node, candidates: dict[int, _Candidate]) -> set[int]:
    return {
        node.start_byte
        for node in walk_owned(root, boundaries=_DECLARATION_BOUNDARIES)
        if node.type == "function_definition"
        and node.start_byte in candidates
        and node.end_byte == candidates[node.start_byte].end
    }
