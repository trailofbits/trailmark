"""Exclude balanced nullability annotation tokens from the syntactic view."""

from tree_sitter import Node, Parser, Range, Tree

_BEGIN = b"NS_ASSUME_NONNULL_BEGIN"
_END = b"NS_ASSUME_NONNULL_END"
_CONDITIONALS = {"preproc_if", "preproc_ifdef", "preproc_elif", "preproc_else"}


def parse_nullability_regions(parser: Parser, source: bytes, original: Tree) -> tuple[Tree, int]:
    """Retain physical bytes/points while accepting known standalone annotations.

    Comments and strings cannot supply identifier tokens. Redefinitions,
    unmatched/nested regions and differing conditional branches stay untouched.
    """
    tokens = []
    pending = [original.root_node]
    while pending:
        node = pending.pop()
        if node.type in {
            "preproc_def",
            "preproc_function_def",
            "preproc_undef",
            "preproc_call",
        } and any(name in (node.text or b"") for name in (_BEGIN, _END)):
            return original, 0
        if node.type in {"identifier", "type_identifier"} and node.text in {_BEGIN, _END}:
            start = source.rfind(b"\n", 0, node.start_byte) + 1
            end = source.find(b"\n", node.end_byte)
            if end < 0:
                end = len(source)
            if (
                not source[start : node.start_byte].strip()
                and not source[node.end_byte : end].strip()
            ):
                tokens.append(node)
        pending.extend(node.named_children)
    tokens.sort(key=lambda node: node.start_byte)
    if not tokens:
        return original, 0
    begin = None
    for node in tokens:
        if node.text == _BEGIN:
            if begin is not None:
                return original, 0
            begin = node
        elif begin is None or _conditionals(begin) != _conditionals(node):
            return original, 0
        else:
            begin = None
    if begin is not None:
        return original, 0
    ranges = []
    start_byte, start_point = 0, (0, 0)
    for node in tokens:
        if start_byte < node.start_byte:
            ranges.append(Range(start_point, node.start_point, start_byte, node.start_byte))
        start_byte, start_point = node.end_byte, node.end_point
    root = original.root_node
    if start_byte < len(source):
        ranges.append(Range(start_point, root.end_point, start_byte, len(source)))
    bounded = Parser(parser.language)
    bounded.included_ranges = ranges
    return bounded.parse(source), len(tokens) // 2


def _conditionals(node: Node) -> tuple[int, ...]:
    result = []
    parent = node.parent
    while parent is not None:
        if parent.type in _CONDITIONALS:
            result.append(parent.start_byte)
        parent = parent.parent
    return tuple(result)
