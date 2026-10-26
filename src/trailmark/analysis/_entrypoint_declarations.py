"""Associate entrypoint markers with their declaring syntax nodes."""

from __future__ import annotations

from pathlib import Path

from tree_sitter import Node, Parser
from tree_sitter_language_pack import get_language

from trailmark.models.nodes import CodeUnit, NodeKind

_LANGUAGES = {
    ".py": "python",
    ".rs": "rust",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".cs": "csharp",
    ".php": "php",
    ".phtml": "php",
    ".cairo": "cairo",
    ".swift": "swift",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".dart": "dart",
    ".c": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".h": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".hxx": "cpp",
}
_CALLABLE_NODES = frozenset(
    {
        "function_definition",
        "function_item",
        "function_declaration",
        "generator_function_declaration",
        "function_expression",
        "generator_function",
        "arrow_function",
        "method_definition",
        "method_declaration",
        "constructor_declaration",
        "local_function_statement",
        "method_signature",
        "function_signature",
    }
)
_ATTRIBUTES = frozenset(
    {"decorator", "annotation", "marker_annotation", "attribute", "attribute_item"}
)
_COMMENTS = frozenset({"comment", "line_comment", "block_comment"})


class DeclarationCache:
    """Parse each inspected file once and index callable declaration starts.

    Frontends disagree about whether locations include attributes. Matching
    syntax nodes by source position handles both forms, without taking
    attributes from a neighbouring declaration, an enclosing class, or a body.
    """

    def __init__(self, language: str | None = None) -> None:
        self._files: dict[str, dict[tuple[int, int], Node]] = {}
        self._language = language

    def _declaration(self, unit: CodeUnit) -> Node | None:
        path = unit.location.file_path
        if path not in self._files:
            declarations: dict[tuple[int, int], Node] = {}
            extension = Path(path).suffix.lower()
            language = _LANGUAGES.get(extension)
            if extension == ".h" and self._language in {"c", "cpp"}:
                language = self._language
            if language is not None:
                try:
                    source = Path(path).read_bytes()
                except OSError:
                    source = b""
                if language == "rust":
                    from trailmark.parsers.rust.parser import parse_syntax_roots

                    pending = list(reversed(parse_syntax_roots(source, path)))
                elif language == "php":
                    from trailmark.parsers.php._syntax import parse_php

                    pending = [parse_php(Parser(get_language("php")), source).root_node]
                else:
                    pending = [Parser(get_language(language)).parse(source).root_node]
                while pending:
                    node = pending.pop()
                    if node.type in _CALLABLE_NODES:
                        # Dart's method_signature wraps a function_signature
                        # at the same position. Keep the outer declaration.
                        point = (node.start_point.row + 1, node.start_point.column)
                        declarations.setdefault(point, node)
                    pending.extend(reversed(node.named_children))
            self._files[path] = declarations
        declarations = self._files[path]
        line = unit.location.start_line
        column = unit.location.start_col
        if column is not None:
            return declarations.get((line, column))
        # Imported/manual graphs can omit columns. Only use an unambiguous
        # declaration; a line may contain several independent methods.
        candidates = [node for (row, _), node in declarations.items() if row == line]
        return candidates[0] if len(candidates) == 1 else None

    def decorators(self, unit: CodeUnit) -> list[str]:
        node = self._declaration(unit)
        if node is None:
            return []
        result = _owned_attributes(node)
        parent = node.parent
        if parent is not None and parent.type == "decorated_definition":
            result.extend(_owned_attributes(parent))
        # Rust/Cairo attributes, TypeScript decorators, and Dart annotations
        # are siblings before the declaration instead of children within it.
        previous = node.prev_named_sibling
        while previous is not None:
            if previous.type in _ATTRIBUTES:
                result.extend(_attribute_texts(previous))
            elif previous.type in _COMMENTS:
                if Path(unit.location.file_path).suffix.lower() == ".php":
                    text = _text(previous)
                    if text.startswith("/**"):
                        result.extend(
                            f"* {line.lstrip('* \t')}" for line in text[3:-2].splitlines()
                        )
            else:
                break
            previous = previous.prev_named_sibling
        return result

    def signature(self, unit: CodeUnit) -> str:
        """Return only this declaration's prefix, excluding its body."""
        node = self._declaration(unit)
        if node is None:
            return ""
        body = node.child_by_field_name("body")
        source = node.text or b""
        if body is not None:
            source = source[: body.start_byte - node.start_byte]
        return source.decode("utf-8", errors="replace")

    def c_export_markers(self, unit: CodeUnit) -> list[str]:
        """Inspect actual export attributes and C linkage, excluding literals."""
        node = self._declaration(unit)
        if node is None:
            return []
        result: list[str] = []
        pending = [node]
        while pending:
            current = pending.pop()
            if current.type in {"attribute_specifier", "ms_declspec_modifier"}:
                result.append(_text(current))
            else:
                pending.extend(
                    child
                    for child in current.named_children
                    if child.type
                    in {
                        "function_declarator",
                        "pointer_declarator",
                        "reference_declarator",
                        "parenthesized_declarator",
                        "attributed_declarator",
                        "attribute_specifier",
                        "ms_declspec_modifier",
                    }
                )
        # Class members retain C++ linkage even inside an extern "C" block.
        # A qualified definition may be an out-of-class member; without type
        # resolution, do not infer C linkage for it from an enclosing block.
        declarator = node.child_by_field_name("declarator")
        while declarator is not None:
            if declarator.type == "qualified_identifier":
                return result
            declarator = declarator.child_by_field_name("declarator")
        if unit.kind == NodeKind.METHOD:
            return result
        if any(
            child.type == "storage_class_specifier" and _text(child) == "static"
            for child in node.named_children
        ):
            return result
        owner = node.parent
        while owner is not None:
            if owner.type in _CALLABLE_NODES or owner.type in {
                "class_specifier",
                "struct_specifier",
                "union_specifier",
            }:
                break
            if owner.type == "linkage_specification":
                value = owner.child_by_field_name("value")
                if value is not None and _text(value) == '"C"':
                    result.append('extern "C"')
                break
            owner = owner.parent
        return result

    def is_swift_main(self, unit: CodeUnit) -> bool:
        """Match a type main method declared directly inside an @main type."""
        if unit.name != "main":
            return False
        node = self._declaration(unit)
        if node is None:
            return False
        modifiers = [child for child in node.named_children if child.type == "modifiers"]
        is_type_method = any(child.type == "class" for child in node.children) or any(
            _text(item) == "static" for mod in modifiers for item in mod.named_children
        )
        if not is_type_method:
            return False
        owner = node.parent
        while owner is not None:
            if owner.type in _CALLABLE_NODES:
                return False
            if owner.type == "class_declaration":
                return any(text.strip() == "@main" for text in _owned_attributes(owner))
            owner = owner.parent
        return False


def _text(node: Node) -> str:
    return (node.text or b"").decode("utf-8", errors="replace")


def _attribute_texts(node: Node) -> list[str]:
    if node.type == "attribute_list":
        result: list[str] = []
        for child in node.named_children:
            if child.type == "attribute_group":  # PHP
                result.extend(f"#[{_text(attr)}]" for attr in child.named_children)
            elif child.type == "attribute":  # C#
                result.append(f"[{_text(child)}]")
        return result
    return [_text(node)]


def _owned_attributes(node: Node) -> list[str]:
    result: list[str] = []
    for child in node.named_children:
        if child.type in _ATTRIBUTES or child.type == "attribute_list":
            result.extend(_attribute_texts(child))
        elif child.type == "modifiers":
            result.extend(_owned_attributes(child))
    return result
