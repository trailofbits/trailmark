"""Bounded syntactic profiles for functions hidden by conditional C braces.

These views select source arms, not compiler configurations. They never expand
macros, read build flags, or establish that a profile is buildable. Every byte
offset and newline is retained; extracted evidence must remain uncertain.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Iterator

from tree_sitter import Parser, Tree

from trailmark.parsers.c._recovery import _CONDITIONAL, _TOKENS, _directive_text

_DEFINED = re.compile(rb"(!?)\s*defined\s*(?:\(\s*(\w+)\s*\)|(\w+))\Z")
_LIMIT = 64


def _condition(text: bytes, directive: bytes) -> tuple[str, bool]:
    expression = text.split(directive, 1)[1].strip()
    if directive in {b"ifdef", b"ifndef", b"elifdef", b"elifndef"}:
        return f"defined({expression.decode(errors='replace')})", directive in {
            b"ifdef",
            b"elifdef",
        }
    match = _DEFINED.fullmatch(expression)
    if match:
        name = (match[2] or match[3]).decode(errors="replace")
        return f"defined({name})", not bool(match[1])
    return " ".join(expression.decode(errors="replace").split()), True


def conditional_views(source: bytes, parser: Parser) -> Iterator[tuple[Tree, str, bool]]:
    """Yield at most 64 source-mapped profiles, reporting incomplete enumeration."""
    directives = []
    atoms: set[str] = set()
    for token in _TOKENS.finditer(source):
        text = _directive_text(token[0]).strip()
        match = _CONDITIONAL.match(text)
        if match is None:
            continue
        kind = match[1]
        condition = _condition(text, kind) if kind not in {b"else", b"endif"} else ("", True)
        if condition[0] not in {"", "0", "1"}:
            atoms.add(condition[0])
        directives.append((token.start(), token.end(), kind, condition))
    if not directives:
        return
    names = sorted(atoms)
    complete = len(names) <= 6
    if complete:
        assignments = itertools.product((False, True), repeat=len(names))
    else:
        # Cover both extremes and individual predicates before stopping. The
        # original errors and a coverage diagnostic keep the remainder unknown.
        assignments = itertools.chain(
            [tuple(False for _ in names), tuple(True for _ in names)],
            (tuple(i == selected for i in range(len(names))) for selected in range(len(names))),
            (tuple(i != selected for i in range(len(names))) for selected in range(len(names))),
        )
    seen: set[bytes] = set()
    for values in itertools.islice(assignments, _LIMIT):
        profile = dict(zip(names, values, strict=True))
        masked = bytearray(source)
        stack: list[tuple[bool, bool]] = []
        active = True
        previous = 0
        valid = True
        for start, end, kind, (expression, positive) in directives:
            if not active:
                _mask(masked, previous, start)
            _mask(masked, start, end)
            selected = (expression == "1" or profile.get(expression, False)) == positive
            if kind in {b"if", b"ifdef", b"ifndef"}:
                stack.append((active, selected))
                active = active and selected
            elif not stack:
                valid = False
                break
            elif kind == b"endif":
                active, _ = stack.pop()
            else:
                parent, taken = stack[-1]
                selected = not taken if kind == b"else" else selected and not taken
                active = parent and selected
                stack[-1] = (parent, taken or selected)
            previous = end
        if not active:
            _mask(masked, previous, len(source))
        if not valid or stack:
            continue
        view = bytes(masked)
        if view in seen:
            continue
        seen.add(view)
        evidence = "; ".join(f"{name}={str(profile[name]).lower()}" for name in names)
        yield parser.parse(view), evidence, complete


def _mask(source: bytearray, start: int, end: int) -> None:
    for index in range(start, end):
        if source[index] not in (10, 13):
            source[index] = 32
