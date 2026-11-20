"""Recover literal declarations around bounded macro annotation positions."""

import re
from collections.abc import Iterator
from dataclasses import dataclass

from tree_sitter import Node, Parser, Range, Tree

_UPPER = re.compile(rb"[A-Z][A-Z0-9_]*\Z")
_CONTAINERS = frozenset(
    {
        "translation_unit",
        "declaration_list",
        "namespace_definition",
        "preproc_if",
        "preproc_ifdef",
        "preproc_else",
        "preproc_elif",
        "ERROR",
    }
)


@dataclass(frozen=True)
class DeclarationMarker:
    name: str
    start_byte: int
    end_byte: int
    role: str


def _nodes(root: Node) -> Iterator[Node]:
    pending = [root]
    while pending:
        node = pending.pop()
        yield node
        pending.extend(reversed(node.named_children))


def _annotation_body(value: bytes) -> bool:
    return (
        re.fullmatch(
            rb"(?:constexpr|inline(?:\s+__attribute__\s*\(\(\s*always_inline\s*\)\))?)?",
            value.strip(),
        )
        is not None
    )


def _annotation_macros(root: Node, source: bytes) -> set[bytes]:
    definitions: dict[bytes, list[bytes | None]] = {}
    view = bytearray(source)
    for node in _nodes(root):
        if node.type in {"comment", "string_literal", "raw_string_literal", "char_literal"}:
            # Literal contents cannot introduce directives or empty macro bodies.
            fill = 32 if node.type == "comment" else 64
            view[node.start_byte : node.end_byte] = bytes(
                byte if node.type != "comment" and byte in (10, 13) else fill
                for byte in source[node.start_byte : node.end_byte]
            )
        if node.type in {"preproc_def", "preproc_function_def"}:
            name = node.child_by_field_name("name")
            if name is None:
                continue
            value = node.child_by_field_name("value")
            definitions.setdefault(name.text or b"", []).append(
                None if node.type == "preproc_function_def" else (value.text if value else b"")
            )
    known = {
        name
        for name, values in definitions.items()
        if _UPPER.fullmatch(name)
        and all(value is not None and _annotation_body(value) for value in values)
    }
    # Native error recovery may split directive keywords or names, or lose a
    # directive after #/**/. This scan can only revoke an AST-proven candidate.
    logical = re.sub(rb"\\\r?\n", b"", bytes(view))
    logical = re.sub(rb"/\*[\s\S]*?(?:\*/|\Z)|//[^\n]*", b" ", logical)
    for match in re.finditer(
        rb"(?m)^[ \t\v\f\r]*(?:#|%:|\?\?=)[ \t\v\f\r]*"
        rb"(define|undef)[ \t\v\f\r]+(\w+)([^\n]*)",
        logical,
    ):
        directive, name, body = match.groups()
        if directive == b"undef" or not _annotation_body(body):
            known.discard(name)
    return known


def _standalone_prefix(node: Node, source: bytes) -> bool:
    parent = node.parent
    if parent is None or parent.type not in {"function_definition", "ERROR"}:
        return False
    ancestor = parent.parent
    while ancestor is not None:
        if ancestor.type not in _CONTAINERS:
            return False
        ancestor = ancestor.parent
    start = source.rfind(b"\n", 0, node.start_byte) + 1
    end = source.find(b"\n", node.end_byte)
    return (
        end >= 0
        and not source[start : node.start_byte].strip()
        and not source[node.end_byte : end].strip()
    )


def _namespace(node: Node, source: bytes) -> bool:
    if not _standalone_prefix(node, source):
        return False
    declarator = node.parent.child_by_field_name("declarator") if node.parent else None
    return (
        declarator is not None
        and declarator.type == "identifier"
        and declarator.text == b"namespace"
        and re.match(rb"\s+namespace\s+[A-Za-z_]\w*(?:::\w+)*\s*\{", source[node.end_byte :])
        is not None
    )


def _template(node: Node, source: bytes) -> bool:
    return (
        _standalone_prefix(node, source)
        and re.match(
            rb"(?:\s|/\*[\s\S]*?\*/|//[^\n]*(?:\n|\Z))*template\s*<",
            source[node.end_byte :],
        )
        is not None
    )


def _constructor(node: Node, source: bytes, known: set[bytes]) -> bool:
    if node.text not in known:
        return False
    declaration = node.parent
    if declaration is None or declaration.type not in {
        "declaration",
        "field_declaration",
        "function_definition",
        "ERROR",
    }:
        return False
    ancestor = declaration.parent
    while ancestor is not None and ancestor.type not in {"class_specifier", "struct_specifier"}:
        if ancestor.type in {"function_definition", "lambda_expression", "compound_statement"}:
            return False
        ancestor = ancestor.parent
    name = ancestor.child_by_field_name("name") if ancestor is not None else None
    if name is None or name.type != "type_identifier":
        return False
    following = b"|".join(re.escape(marker) for marker in sorted(known))
    return (
        re.match(
            rb"\s+(?:(?:"
            + following
            + rb")\s+)*(?:explicit\s+)?~?"
            + re.escape(name.text or b"")
            + rb"\s*\(",
            source[node.end_byte :],
        )
        is not None
    )


def _exclude(parser: Parser, tree: Tree, source: bytes, markers: list[DeclarationMarker]) -> Tree:
    def point(offset: int) -> tuple[int, int]:
        return source.count(b"\n", 0, offset), offset - source.rfind(b"\n", 0, offset) - 1

    ranges = []
    for included in tree.included_ranges:
        left, end = included.start_byte, min(included.end_byte, len(source))
        for marker in markers:
            if marker.end_byte <= left or marker.start_byte >= end:
                continue
            if left < marker.start_byte:
                ranges.append(Range(point(left), point(marker.start_byte), left, marker.start_byte))
            left = marker.end_byte
        if left < end:
            ranges.append(Range(point(left), point(end), left, end))
    bounded = Parser(parser.language)
    bounded.included_ranges = ranges
    return bounded.parse(source)


def recover_declaration_markers(
    parser: Parser, source: bytes, original: Tree
) -> tuple[Tree, tuple[DeclarationMarker, ...]]:
    """Recover syntax positions, retaining bytes, diagnostics and opaque macros.

    A bare standalone identifier cannot prefix the reserved namespace keyword.
    Constructor annotations additionally require every inspected object-macro
    definition to be a bounded constructor annotation, and an exact class name.
    """
    known = _annotation_macros(original.root_node, source)
    tree, recovered = original, []
    for _ in range(64):
        markers = []
        for node in _nodes(tree.root_node):
            if node.type != "type_identifier" or _UPPER.fullmatch(node.text or b"") is None:
                continue
            parent = node.parent
            if parent is None or (
                parent.child_by_field_name("type") != node
                and not (
                    parent.type == "ERROR"
                    and parent.named_children[0] == node
                    and (_template(node, source) or _constructor(node, source, known))
                )
            ):
                continue
            role = (
                "namespace_prefix"
                if _namespace(node, source)
                else "template_prefix"
                if _template(node, source)
                else "constructor_annotation"
                if _constructor(node, source, known)
                else None
            )
            if role is not None:
                markers.append(
                    DeclarationMarker(
                        (node.text or b"").decode(), node.start_byte, node.end_byte, role
                    )
                )
        if not markers:
            break
        markers.sort(key=lambda marker: marker.start_byte)
        tree = _exclude(parser, tree, source, markers)
        recovered.extend(markers)
    return tree, tuple(recovered)
