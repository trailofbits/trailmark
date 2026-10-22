"""Bounded declaration-macro identities from literal source definitions."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from tree_sitter import Node, Parser

from trailmark.parsers._common import node_text
from trailmark.parsers._walk import walk_owned
from trailmark.parsers.c._headers import HeaderSources

_IDENTIFIER = re.compile(r"[A-Za-z_]\w*\Z")


@dataclass(frozen=True)
class _Macro:
    parameters: tuple[str, ...] | None
    body: str
    pattern: re.Pattern[str] = field(compare=False, repr=False)
    parameter: re.Pattern[str] | None = field(compare=False, repr=False)


@dataclass(frozen=True)
class MacroSignature:
    name: str
    wrapper: str
    source_alias: str
    declaration: Node


class DeclarationMacros:
    """Consult in-root quoted includes; never select conditional definitions."""

    def __init__(self, root: Node, path: str, headers: HeaderSources) -> None:
        self._headers = headers
        self._macros: dict[str, tuple[_Macro | None, int]] = {}
        self._visited: set[Path] = set()
        self._root, self._path = root, Path(path)
        self._loaded = False

    def _collect(self, root: Node, path: Path, position: int, depth: int) -> None:
        if depth > 16 or len(self._visited) >= 128:
            return
        resolved = path.resolve()
        if resolved in self._visited:
            return
        self._visited.add(resolved)
        conditions = self._headers.conditions(root, path)
        for node in self._headers.directives(root, path):
            at = node.start_byte if depth == 0 else position
            if node.type in {"preproc_def", "preproc_function_def", "preproc_call"}:
                self._record_definition(node, at, conditions)
            elif node.type == "preproc_include" and not _conditional(node, conditions):
                self._include(node, path, at, depth)

    def _record_definition(self, node: Node, position: int, conditions: dict[int, bool]) -> None:
        if node.type == "preproc_call":
            directive = node.child_by_field_name("directive")
            argument = node.child_by_field_name("argument")
            if directive is not None and node_text(directive) == "#undef" and argument:
                self._record(node_text(argument).strip(), None, position)
            return
        name = node.child_by_field_name("name")
        if name is None:
            return
        value = node.child_by_field_name("value")
        params = node.child_by_field_name("parameters")
        macro = _macro(
            node_text(name),
            params,
            node_text(value).replace("\\\n", " ") if value is not None else "",
        )
        self._record(
            node_text(name),
            None if _conditional(node, conditions) else macro,
            position,
        )

    def _record(self, name: str, macro: _Macro | None, position: int) -> None:
        previous = self._macros.get(name)
        if previous is not None:
            position = min(position, previous[1])
            if previous[0] != macro:
                macro = None
        self._macros[name] = macro, position

    def _include(self, node: Node, path: Path, position: int, depth: int) -> None:
        include = node.child_by_field_name("path")
        if include is None or include.type != "string_literal":
            return
        target = path.parent / node_text(include)[1:-1]
        root = self._headers.get(target)
        if root is not None:
            self._collect(root, target, position, depth + 1)

    def wrapper(self, node: Node) -> tuple[str, str] | None:
        """Recognize untyped wrappers even when their macro headers are unavailable."""
        candidate = _wrapper_candidate(node)
        if candidate is None:
            return None
        wrapper, alias, unambiguous = candidate
        if not self._loaded:
            self._collect(self._root, self._path, 0, 0)
            self._loaded = True
        entry = self._macros.get(wrapper)
        if unambiguous or entry is not None and entry[1] <= node.start_byte:
            return wrapper, alias
        return None

    def signature(self, node: Node) -> MacroSignature | None:
        """Verify the expanded declaration, including object-like macro aliases."""
        candidate = self.wrapper(node)
        if candidate is None:
            return None
        wrapper, alias = candidate
        original = f"{wrapper}({alias})"
        expanded = self._expand(original, node.start_byte)
        if expanded is None or expanded == original:
            return None
        function = _expanded_function(self._headers.parser, expanded)
        if function is None:
            return None
        declaration, name = function
        return MacroSignature(name, wrapper, alias, declaration)

    def _expand(self, text: str, position: int) -> str | None:
        if _unsupported_expansion(text):
            return None
        for _ in range(16):
            previous = text
            expanded = self._expand_round(text, position)
            if expanded is None:
                return None
            text = expanded
            if text == previous:
                return text
        return None

    def _expand_round(self, text: str, position: int) -> str | None:
        for name in dict.fromkeys(re.findall(r"[A-Za-z_]\w*", text)):
            entry = self._macros.get(name)
            if entry is None:
                continue
            macro, at = entry
            if macro is None or at > position or _unsupported_expansion(macro.body):
                continue
            text = _replace_macro(text, name, macro)
            if len(text) > 16_384:
                return None
        text = re.sub(r"\s*##\s*", "", text)
        return None if "#" in text else text


def _wrapper_candidate(node: Node) -> tuple[str, str, bool] | None:
    declarator = node.child_by_field_name("declarator")
    type_node = node.child_by_field_name("type")
    if declarator is None:
        return None
    if declarator.type == "parenthesized_declarator" and type_node is not None:
        if any(child.type == "function_declarator" for child in walk_owned(declarator)):
            return None
        arguments = declarator.named_children
        wrapper = node_text(type_node)
        unambiguous = True
    elif declarator.type == "function_declarator":
        parts = _typed_wrapper(declarator)
        if parts is None:
            return None
        wrapper, arguments = parts
        unambiguous = False
    else:
        return None
    alias = _single_argument(arguments)
    return (wrapper, alias, unambiguous) if alias is not None else None


def _typed_wrapper(declarator: Node) -> tuple[str, list[Node]] | None:
    name = declarator.child_by_field_name("declarator")
    params = declarator.child_by_field_name("parameters")
    if name is None or params is None:
        return None
    return node_text(name), params.named_children


def _single_argument(arguments: list[Node]) -> str | None:
    if len(arguments) != 1:
        return None
    argument_type = arguments[0].child_by_field_name("type")
    if argument_type is not None and argument_type.type != "type_identifier":
        return None
    alias = node_text(arguments[0]).strip()
    return alias if _IDENTIFIER.fullmatch(alias) else None


def _expanded_function(parser: Parser, expanded: str) -> tuple[Node, str] | None:
    root = parser.parse((expanded + " {}").encode()).root_node
    if root.has_error or len(root.named_children) != 1:
        return None
    function = root.named_children[0]
    if function.type != "function_definition":
        return None
    declaration = function.child_by_field_name("declarator")
    if declaration is None or not any(
        child.type == "function_declarator" for child in walk_owned(declaration)
    ):
        return None
    while declaration is not None and declaration.type != "identifier":
        declaration = declaration.child_by_field_name("declarator")
    return (function, node_text(declaration)) if declaration is not None else None


def _unsupported_expansion(text: str) -> bool:
    return any(token in text for token in ('"', "'", "..."))


def _macro(name: str, parameters: Node | None, body: str) -> _Macro:
    params = tuple(node_text(child) for child in parameters.named_children) if parameters else None
    pattern, parameter = _macro_patterns(name, params)
    return _Macro(params, body, pattern, parameter)


@lru_cache(maxsize=16384)
def _macro_patterns(
    name: str, params: tuple[str, ...] | None
) -> tuple[re.Pattern[str], re.Pattern[str] | None]:
    pattern = rf"\b{re.escape(name)}\b"
    if params is not None:
        pattern += r"\s*\(\s*([A-Za-z_]\w*)\s*\)"
    parameter = re.compile(rf"\b{re.escape(params[0])}\b") if params and len(params) == 1 else None
    return re.compile(pattern), parameter


def _replace_macro(text: str, name: str, macro: _Macro) -> str:
    if macro.parameters is None:
        return macro.pattern.sub(lambda _: macro.body, text)
    if macro.parameter is None:
        return text
    parameter = macro.parameter
    return macro.pattern.sub(lambda match: parameter.sub(lambda _: match[1], macro.body), text)


def _conditional(node: Node, cache: dict[int, bool] | None = None) -> bool:
    cache = {} if cache is None else cache
    ancestors = []
    parent = node.parent
    while parent is not None and parent.id not in cache:
        ancestors.append(parent)
        parent = parent.parent
    conditional = cache.get(parent.id, False) if parent is not None else False
    for ancestor in reversed(ancestors):
        conditional = conditional or (
            ancestor.type
            in {
                "preproc_if",
                "preproc_ifdef",
                "preproc_else",
                "preproc_elif",
            }
            and not _include_guard(ancestor)
        )
        cache[ancestor.id] = conditional
    return conditional


def _include_guard(node: Node) -> bool:
    if node.type != "preproc_ifdef" or not node_text(node).lstrip().startswith("#ifndef"):
        return False
    if node.parent is None or node.parent.type != "translation_unit":
        return False
    name = node.child_by_field_name("name")
    if name is None or _has_alternative(node):
        return False
    declarations = [child for child in node.parent.named_children if child.type != "comment"]
    return declarations == [node] and _defines(node, node_text(name))


def _has_alternative(node: Node) -> bool:
    return any(child.type in {"preproc_else", "preproc_elif"} for child in node.named_children)


def _defines(node: Node, name: str) -> bool:
    return any(
        child.type == "preproc_def" and node_text(child_name) == name
        for child in node.named_children
        if (child_name := child.child_by_field_name("name")) is not None
    )
