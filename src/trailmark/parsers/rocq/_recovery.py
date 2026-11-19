"""Recover vernacular sentences at dots outside comments and strings."""

from tree_sitter import Node, Parser

from trailmark.parsers._formal import parse_regions


def sentences(root: Node, parser: Parser) -> list[Node]:
    if not root.has_error:
        return list(root.named_children)
    text = root.text or b""
    regions = []
    start = index = depth = 0
    quoted = False
    while index < len(text):
        pair = text[index : index + 2]
        if not quoted and pair == b"(*":
            depth += 1
            index += 2
            continue
        if depth and pair == b"*)":
            depth -= 1
            index += 2
            continue
        if not depth and text[index] == 34:
            if quoted and pair == b'""':
                index += 2
                continue
            quoted = not quoted
        if (
            not depth
            and not quoted
            and text[index] == 46
            and (index + 1 == len(text) or chr(text[index + 1]).isspace())
        ):
            regions.append((start, index + 1))
            start = index + 1
        index += 1
    return parse_regions(parser, text, regions)
