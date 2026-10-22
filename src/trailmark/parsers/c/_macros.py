"""Bounded declaration-macro identities from literal source definitions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from tree_sitter import Node, Parser

from trailmark.parsers._common import node_text
from trailmark.parsers._walk import walk_owned

_IDENTIFIER = re.compile(r"[A-Za-z_]\w*\Z")


@dataclass(frozen=True)
class _Macro:
    parameters: tuple[str, ...] | None
    body: str


@dataclass(frozen=True)
class MacroSignature:
    name: str
    wrapper: str
    source_alias: str
    declaration: Node


class DeclarationMacros:
    """Consult literal quoted includes; never select conditional definitions."""

    def __init__(self, root: Node, path: str, parser: Parser) -> None:
        self._parser = parser
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
        for node in walk_owned(root):
            at = node.start_byte if depth == 0 else position
            if node.type in {"preproc_def", "preproc_function_def", "preproc_call"}:
                name = node.child_by_field_name("name")
                if node.type == "preproc_call":
                    directive = node.child_by_field_name("directive")
                    argument = node.child_by_field_name("argument")
                    if directive is not None and node_text(directive) == "#undef" and argument:
                        ident = node_text(argument).strip()
                        previous = self._macros.get(ident)
                        self._macros[ident] = (None, min(at, previous[1]) if previous else at)
                    continue
                if name is None:
                    continue
                value = node.child_by_field_name("value")
                params = node.child_by_field_name("parameters")
                macro = _Macro(
                    tuple(node_text(child) for child in params.named_children)
                    if params is not None
                    else None,
                    node_text(value).replace("\\\n", " ") if value is not None else "",
                )
                previous = self._macros.get(node_text(name))
                if _conditional(node) or previous is not None and previous[0] != macro:
                    self._macros[node_text(name)] = (None, min(at, previous[1]) if previous else at)
                else:
                    self._macros[node_text(name)] = (
                        macro,
                        min(at, previous[1]) if previous else at,
                    )
            elif node.type == "preproc_include" and not _conditional(node):
                include = node.child_by_field_name("path")
                if include is None or include.type != "string_literal":
                    continue
                target = path.parent / node_text(include)[1:-1]
                try:
                    if not target.is_file() or target.stat().st_size > 4 * 1024 * 1024:
                        continue
                    source = target.read_bytes()
                except OSError:
                    continue
                self._collect(self._parser.parse(source).root_node, target, at, depth + 1)

    def wrapper(self, node: Node) -> tuple[str, str] | None:
        """Identify a declared macro without confusing unnamed C parameters."""
        declarator = node.child_by_field_name("declarator")
        type_node = node.child_by_field_name("type")
        if declarator is None:
            return None
        if declarator.type == "parenthesized_declarator" and type_node is not None:
            wrapper = node_text(type_node)
            arguments = declarator.named_children
        elif declarator.type == "function_declarator":
            name = declarator.child_by_field_name("declarator")
            params = declarator.child_by_field_name("parameters")
            if name is None or params is None:
                return None
            wrapper = node_text(name)
            arguments = params.named_children
        else:
            return None
        if len(arguments) != 1:
            return None
        argument_type = arguments[0].child_by_field_name("type")
        if argument_type is not None and argument_type.type != "type_identifier":
            return None
        alias = node_text(arguments[0]).strip()
        if _IDENTIFIER.fullmatch(alias) is None:
            return None
        if not self._loaded:
            self._collect(self._root, self._path, 0, 0)
            self._loaded = True
        entry = self._macros.get(wrapper)
        return (wrapper, alias) if entry is not None and entry[1] <= node.start_byte else None

    def signature(self, node: Node) -> MacroSignature | None:
        """Verify the expanded declaration of a single-identifier wrapper."""
        candidate = self.wrapper(node)
        if candidate is None:
            return None
        wrapper, alias = candidate
        entry = self._macros.get(wrapper)
        if entry is None or entry[0] is None or entry[1] > node.start_byte:
            return None
        if entry[0].parameters is None or len(entry[0].parameters) != 1:
            return None
        expanded = self._expand(f"{wrapper}({alias})", node.start_byte)
        if expanded is None or expanded == f"{wrapper}({alias})":
            return None
        root = self._parser.parse((expanded + " {}").encode()).root_node
        if root.has_error or len(root.named_children) != 1:
            return None
        function = root.named_children[0]
        if function.type != "function_definition":
            return None
        decl = function.child_by_field_name("declarator")
        if decl is None or not any(
            child.type == "function_declarator" for child in walk_owned(decl)
        ):
            return None
        while decl is not None and decl.type != "identifier":
            decl = decl.child_by_field_name("declarator")
        if decl is None:
            return None
        return MacroSignature(node_text(decl), wrapper, alias, function)

    def _expand(self, text: str, position: int) -> str | None:
        # Only identifier arguments, simple token pasting, and ordinary signature
        # tokens are accepted. Stringification and variadic expansion are excluded.
        if any(token in text for token in ('"', "'", "...")):
            return None
        for _ in range(16):
            previous = text
            for name, (macro, at) in self._macros.items():
                if macro is None or at > position:
                    continue
                if any(token in macro.body for token in ('"', "'", "...")):
                    continue
                if macro.parameters is None:
                    text = re.sub(rf"\b{re.escape(name)}\b", lambda _, body=macro.body: body, text)
                else:
                    pattern = rf"\b{re.escape(name)}\s*\(\s*([A-Za-z_]\w*)\s*\)"
                    if len(macro.parameters) == 1:
                        parameter = macro.parameters[0]
                        text = re.sub(
                            pattern,
                            lambda match, body=macro.body, param=parameter: re.sub(
                                rf"\b{re.escape(param)}\b", lambda _: match[1], body
                            ),
                            text,
                        )
            text = re.sub(r"\s*##\s*", "", text)
            if "#" in text or len(text) > 16_384:
                return None
            if text == previous:
                return text
        return None


def _conditional(node: Node) -> bool:
    parent = node.parent
    while parent is not None:
        if parent.type in {"preproc_if", "preproc_ifdef", "preproc_else", "preproc_elif"}:
            # Conventional file include guards do not select between signatures.
            name = parent.child_by_field_name("name")
            guard = (
                parent.type == "preproc_ifdef"
                and parent.parent is not None
                and parent.parent.type == "translation_unit"
                and node_text(parent).lstrip().startswith("#ifndef")
                and name is not None
                and any(
                    child.type == "preproc_def" and node_text(child_name) == node_text(name)
                    for child in parent.named_children
                    if (child_name := child.child_by_field_name("name")) is not None
                )
                and not any(
                    child.type in {"preproc_else", "preproc_elif"}
                    for child in parent.named_children
                )
                and [child for child in parent.parent.named_children if child.type != "comment"]
                == [parent]
            )
            if not guard:
                return True
        parent = parent.parent
    return False
