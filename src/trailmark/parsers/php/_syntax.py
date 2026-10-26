"""Narrow compatibility recovery for reserved PHP class constant identifiers.

The packaged PHP grammar (including upstream 0.25.1) drops TRUE/FALSE
tokens in class constant declarations. Recover only that identifier slot,
using equal-length placeholders in parser input. Files and source coordinates
are unchanged. Remaining grammar errors are collected by the normal visitor.
Class constant names are not currently graph units.
"""

from __future__ import annotations

import re

from tree_sitter import Node, Parser, Tree

_BOOLEAN_NAME = re.compile(rb"\s+(true|false)\s*\Z", re.IGNORECASE)


def parse_php(parser: Parser, source: bytes) -> Tree:
    """Recover boolean-named class constants without suppressing diagnostics."""
    tree = parser.parse(source)
    if not tree.root_node.has_error:
        return tree
    leaves: list[Node] = []
    pending = [tree.root_node]
    while pending:
        node = pending.pop()
        if node.children:
            pending.extend(reversed(node.children))
        else:
            leaves.append(node)
    patched = bytearray(source)
    recovered = False
    scopes: list[bool] = []
    pending_class = False
    for index, leaf in enumerate(leaves[:-1]):
        if leaf.type in {"class", "interface", "trait", "enum"} and (
            index == 0 or leaves[index - 1].type != "::"
        ):
            pending_class = True
        elif leaf.type == "{":
            scopes.append(pending_class)
            pending_class = False
        elif leaf.type == "}" and scopes:
            scopes.pop()
        if leaf.type != "const":
            continue
        if not scopes or not scopes[-1]:
            continue
        following = leaves[index + 1]
        if following.type != "=":
            continue
        match = _BOOLEAN_NAME.fullmatch(source[leaf.end_byte : following.start_byte])
        if match:
            start = leaf.end_byte + match.start(1)
            patched[start] = ord("_")
            recovered = True
    return parser.parse(bytes(patched)) if recovered else tree
