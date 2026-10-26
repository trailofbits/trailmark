"""Associate entrypoint markers with their declaring syntax nodes."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from tree_sitter import Node, Parser
from tree_sitter_language_pack import get_language

from trailmark.models.nodes import CodeUnit, NodeKind, SourceLocation

_LANGUAGES = {
    ".py": "python",
    ".rs": "rust",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
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
_ECMASCRIPT = frozenset({"javascript", "typescript", "tsx"})
_FUNCTION_DECLARATIONS = frozenset({"function_declaration", "generator_function_declaration"})
_VARIABLE_DECLARATIONS = frozenset({"lexical_declaration", "variable_declaration"})
# Syntax that yields its operand's runtime value unchanged.
_TRANSPARENT = frozenset(
    {"parenthesized_expression", "as_expression", "satisfies_expression", "non_null_expression"}
)


@dataclass(frozen=True)
class ExportFact:
    """A name under which module syntax exports a callable.

    Attributes:
        name: The exported name; ``default`` for default exports and for a
            CommonJS ``module.exports`` value.
        location: The export site, not the callable declaration.
        indirect: The callable reaches the export only as a call argument,
            so the exported value may or may not be the callable itself.
    """

    name: str
    location: SourceLocation
    indirect: bool = False


@dataclass
class _ModuleExports:
    """Export sites keyed by module-scope binding name or by callable span."""

    path: str
    by_binding: dict[str, list[ExportFact]] = field(default_factory=dict)
    by_node: dict[tuple[int, int], list[ExportFact]] = field(default_factory=dict)

    def _fact(self, name: str, site: Node, indirect: bool) -> ExportFact:
        location = SourceLocation(
            file_path=self.path,
            start_line=site.start_point.row + 1,
            end_line=site.end_point.row + 1,
            start_col=site.start_point.column,
            end_col=site.end_point.column,
        )
        return ExportFact(name=name, location=location, indirect=indirect)

    def bind(self, local: str, name: str, site: Node, indirect: bool = False) -> None:
        self.by_binding.setdefault(local, []).append(self._fact(name, site, indirect))

    def callable(self, node: Node, name: str, site: Node, indirect: bool) -> None:
        key = (node.start_byte, node.end_byte)
        self.by_node.setdefault(key, []).append(self._fact(name, site, indirect))

    def value(self, expression: Node, name: str, site: Node) -> None:
        """Export an expression's value; call arguments are only possibly exported."""
        pending = [(expression, False)]
        while pending:
            current, indirect = pending.pop()
            if current.type in _TRANSPARENT:
                pending.extend((child, indirect) for child in _named(current)[:1])
            elif current.type == "call_expression":
                arguments = current.child_by_field_name("arguments")
                if arguments is not None:
                    pending.extend((argument, True) for argument in _named(arguments))
            elif current.type == "identifier":
                self.bind(_text(current), name, site, indirect)
            elif current.type in _CALLABLE_NODES:
                self.callable(current, name, site, indirect)


class DeclarationCache:
    """Parse each inspected file once and index callable declaration starts.

    Frontends disagree about whether locations include attributes. Matching
    syntax nodes by source position handles both forms, without taking
    attributes from a neighbouring declaration, an enclosing class, or a body.
    """

    def __init__(self, language: str | None = None) -> None:
        self._files: dict[str, dict[tuple[int, int], Node]] = {}
        self._exports: dict[str, _ModuleExports] = {}
        self._language = language

    def _load(self, path: str) -> None:
        declarations: dict[tuple[int, int], Node] = {}
        extension = Path(path).suffix.lower()
        language = _LANGUAGES.get(extension)
        if extension == ".h" and self._language in {"c", "cpp"}:
            language = self._language
        pending = [] if language is None else _parse_roots(path, language)
        if language in _ECMASCRIPT:
            self._exports[path] = _index_exports(pending[0], path)
        while pending:
            node = pending.pop()
            if node.type in _CALLABLE_NODES:
                # Dart's method_signature wraps a function_signature
                # at the same position. Keep the outer declaration.
                point = (node.start_point.row + 1, node.start_point.column)
                declarations.setdefault(point, node)
            pending.extend(reversed(node.named_children))
        self._files[path] = declarations

    def _declaration(self, unit: CodeUnit) -> Node | None:
        path = unit.location.file_path
        if path not in self._files:
            self._load(path)
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
                if Path(unit.location.file_path).suffix.lower() in {".php", ".phtml"}:
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

    def module_exports(self, unit: CodeUnit) -> list[ExportFact]:
        """Return the names under which ECMAScript or CommonJS syntax exports a callable.

        Names come from export syntax, not from the callable's own name, so
        ``export { internal as GET }`` exports ``internal`` as ``GET`` only.
        Nested functions and methods never inherit an enclosing export.

        Args:
            unit: A callable parsed from a JavaScript or TypeScript file.

        Returns:
            One fact per export site; empty when no syntax exports the unit.
        """
        node = self._declaration(unit)
        exports = self._exports.get(unit.location.file_path)
        if node is None or exports is None:
            return []
        facts = list(exports.by_node.get((node.start_byte, node.end_byte), []))
        binding = _module_binding(node)
        if binding is not None:
            local, wrapped = binding
            for fact in exports.by_binding.get(local, []):
                facts.append(replace(fact, indirect=True) if wrapped else fact)
        return facts

    def is_module_export(self, unit: CodeUnit) -> bool:
        """Check whether any export syntax, direct or indirect, names this callable."""
        return bool(self.module_exports(unit))

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


def _parse_roots(path: str, language: str) -> list[Node]:
    try:
        source = Path(path).read_bytes()
    except OSError:
        source = b""
    if language == "rust":
        from trailmark.parsers.rust.parser import parse_syntax_roots

        return list(reversed(parse_syntax_roots(source, path)))
    if language == "php":
        from trailmark.parsers.php._syntax import parse_php

        return [parse_php(Parser(get_language("php")), source).root_node]
    return [Parser(get_language(language)).parse(source).root_node]


def _named(node: Node) -> list[Node]:
    """Named children without comments, which may appear between any tokens."""
    return [child for child in node.named_children if child.type not in _COMMENTS]


def _index_exports(program: Node, path: str) -> _ModuleExports:
    """Index only module-scope statements; nested code cannot declare exports."""
    exports = _ModuleExports(path)
    for statement in _named(program):
        if statement.type == "export_statement":
            _index_export_statement(exports, statement)
        elif statement.type == "expression_statement":
            for expression in _named(statement)[:1]:
                _index_commonjs(exports, expression)
    return exports


def _index_export_statement(exports: _ModuleExports, statement: Node) -> None:
    tokens = {child.type for child in statement.children if not child.is_named}
    # Re-exports bind nothing local; type-only exports have no runtime value.
    if statement.child_by_field_name("source") is not None or "type" in tokens:
        return
    declaration = statement.child_by_field_name("declaration")
    if declaration is not None:
        for local in _declared_names(declaration):
            exports.bind(local, "default" if "default" in tokens else local, statement)
    else:
        _index_default_value(exports, statement, tokens)
    for clause in _named(statement):
        if clause.type == "export_clause":
            _index_export_clause(exports, clause)


def _index_default_value(exports: _ModuleExports, statement: Node, tokens: set[str]) -> None:
    value = statement.child_by_field_name("value")
    if value is not None:
        exports.value(value, "default", statement)
    elif "=" in tokens:  # TypeScript `export = value`
        for operand in _named(statement)[-1:]:
            exports.value(operand, "default", statement)


def _index_export_clause(exports: _ModuleExports, clause: Node) -> None:
    """Index `export { local as name }`; the exported name is the alias, if any."""
    for specifier in _named(clause):
        local = specifier.child_by_field_name("name")
        alias = specifier.child_by_field_name("alias") or local
        type_only = any(child.type == "type" for child in specifier.children)
        if local is not None and alias is not None and not type_only:
            exports.bind(_text(local), _export_name(alias), specifier)


def _export_name(node: Node) -> str:
    """ES2022 allows string export names: `export { x as "y" }`."""
    fragments = [child for child in node.named_children if child.type == "string_fragment"]
    return _text(fragments[0]) if node.type == "string" and fragments else _text(node)


def _declared_names(declaration: Node) -> list[str]:
    if declaration.type in _FUNCTION_DECLARATIONS:
        name = declaration.child_by_field_name("name")
        return [] if name is None else [_text(name)]
    if declaration.type not in _VARIABLE_DECLARATIONS:
        return []
    names = [d.child_by_field_name("name") for d in _named(declaration)]
    # Destructuring patterns bind properties of a value, not the value itself.
    return [_text(name) for name in names if name is not None and name.type == "identifier"]


def _index_commonjs(exports: _ModuleExports, assignment: Node) -> None:
    """Index `module.exports = value` as default and `exports.name = value` by name."""
    if assignment.type != "assignment_expression":
        return
    left = assignment.child_by_field_name("left")
    right = assignment.child_by_field_name("right")
    if left is None or right is None:
        return
    if _is_module_exports(left) and right.type == "object":
        _index_commonjs_object(exports, right)
        return
    name = _commonjs_name(left)
    if name is not None:
        exports.value(right, name, assignment)


def _commonjs_name(target: Node) -> str | None:
    if _is_module_exports(target):
        return "default"  # Node's ESM interop exposes `module.exports` as default.
    if target.type != "member_expression":
        return None
    owner = target.child_by_field_name("object")
    name = target.child_by_field_name("property")
    if owner is None or name is None:
        return None
    if (owner.type == "identifier" and _text(owner) == "exports") or _is_module_exports(owner):
        return _text(name)
    return None


def _is_module_exports(node: Node) -> bool:
    owner = node.child_by_field_name("object")
    name = node.child_by_field_name("property")
    return (
        node.type == "member_expression"
        and owner is not None
        and owner.type == "identifier"
        and _text(owner) == "module"
        and name is not None
        and _text(name) == "exports"
    )


def _index_commonjs_object(exports: _ModuleExports, literal: Node) -> None:
    for member in _named(literal):
        if member.type == "shorthand_property_identifier":
            exports.bind(_text(member), _text(member), member)
            continue
        key = member.child_by_field_name("key" if member.type == "pair" else "name")
        if key is None or key.type not in {"property_identifier", "string"}:
            continue  # Computed keys are not statically known.
        if member.type == "pair":
            value = member.child_by_field_name("value")
            if value is not None:
                exports.value(value, _export_name(key), member)
        elif member.type == "method_definition":
            exports.callable(member, _export_name(key), member, indirect=False)


def _module_binding(node: Node) -> tuple[str, bool] | None:
    """Return the module-scope name bound to a callable, and whether a call wraps it.

    Callables in bodies, classes, or object literals have no module binding,
    so they never inherit an export of their enclosing declaration.
    """
    if node.type in _FUNCTION_DECLARATIONS:
        name = node.child_by_field_name("name")
        return (_text(name), False) if name is not None and _at_module_scope(node) else None
    wrapped = False
    current = node
    wrappers = _TRANSPARENT | {"arguments"}
    while current.parent is not None and current.parent.type in wrappers:
        current = current.parent
        if current.type == "arguments" and current.parent is not None:
            wrapped = True
            current = current.parent  # The call receiving this callable.
    parent = current.parent
    if parent is None or parent.type != "variable_declarator":
        return None
    name = parent.child_by_field_name("name")
    value = parent.child_by_field_name("value")
    declaration = parent.parent
    if name is None or name.type != "identifier" or value != current or declaration is None:
        return None
    if declaration.type in _VARIABLE_DECLARATIONS and _at_module_scope(declaration):
        return _text(name), wrapped
    return None


def _at_module_scope(declaration: Node) -> bool:
    parent = declaration.parent
    if parent is not None and parent.type == "export_statement":
        parent = parent.parent
    return parent is not None and parent.type == "program"


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
