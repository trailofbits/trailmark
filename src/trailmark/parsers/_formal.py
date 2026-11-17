"""Shared source evidence for declarative and verification-language frontends.

Adapters own their syntax, binders, visibility and executable/specification
classification. This module never interprets proof names or runs a verifier.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from tree_sitter import Language, Node, Parser

from trailmark.models.diagnostics import ParseDiagnostic
from trailmark.models.edges import CodeEdge, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import Attribute, CodeUnit, NodeKind, NodeOrigin, Parameter
from trailmark.parsers._common import (
    add_module_node,
    make_location,
    module_id_from_path,
    node_text,
    parse_directory,
)
from trailmark.parsers._formal_linking import link_symbols


def walk(node: Node, skip: frozenset[str] = frozenset()) -> list[Node]:
    result, pending = [], [node]
    while pending:
        child = pending.pop()
        if child.type not in skip:
            result.append(child)
            pending.extend(reversed(child.named_children))
    return result


@dataclass(frozen=True)
class Scope:
    """An adapter-established lexical binding over a half-open byte interval."""

    names: frozenset[str]
    start: int
    end: int
    opens: tuple[str, ...] = ()
    context: str | None = None

    def blocks(self, name: str, position: int) -> bool:
        return self.start <= position < self.end and name.split(".")[0] in self.names


def lookup_tiers(
    name: str, namespace: str, opens: tuple[str, ...], aliases: dict[str, str]
) -> list[list[str]]:
    for prefix in sorted(aliases, key=len, reverse=True):
        if name == prefix or name.startswith(prefix + "."):
            return [[aliases[prefix] + name[len(prefix) :]]]
    tiers = []
    while namespace:
        tiers.append([f"{namespace}.{name}"])
        namespace = namespace.rpartition(".")[0]
    tiers.append([name, *(f"{prefix}.{name}" for prefix in opens)])
    return tiers


class Source:
    """Build a file graph from explicit declarations and adapter-selected regions."""

    def __init__(self, root: Node, path: str, language: str) -> None:
        self.path, self.language = path, language
        self.module = module_id_from_path(path)
        self.graph = CodeGraph(language=language, root_path=path)
        add_module_node(root, path, self.module, self.graph)
        self.modules = {Path(path).stem}
        self.module_name(Path(path).stem)

    def module_name(self, name: str) -> None:
        self.modules.add(name)
        unit = self.graph.nodes[self.module]
        attrs = tuple((k, v) for k, v in unit.attributes if k != "formal_modules")
        self.graph.nodes[self.module] = replace(
            unit, attributes=(*attrs, ("formal_modules", json.dumps(sorted(self.modules))))
        )

    def ordered_declarations(self) -> None:
        unit = self.graph.nodes[self.module]
        self.graph.nodes[self.module] = replace(
            unit, attributes=(*unit.attributes, ("ordered_declarations", True))
        )

    def declare(
        self,
        node: Node,
        name: str,
        role: str,
        *,
        namespace: str = "",
        parent: str | None = None,
        kind: NodeKind = NodeKind.FUNCTION,
        parameters: tuple[Parameter, ...] = (),
        attributes: tuple[Attribute, ...] = (),
    ) -> str:
        qualified = f"{namespace}.{name}" if namespace else name
        ident = f"{self.module}:{qualified}"
        if ident in self.graph.nodes:
            ident += f"@{node.start_point.row + 1}:{node.start_point.column}"
        self.graph.nodes[ident] = CodeUnit(
            ident,
            name,
            kind,
            make_location(node, self.path),
            parameters=parameters,
            attributes=(("declaration_role", role), ("qualified_name", qualified), *attributes),
        )
        self.graph.edges.append(CodeEdge(parent or self.module, ident, EdgeKind.CONTAINS))
        return ident

    def import_(self, node: Node, name: str) -> None:
        target = f"@import:{self.language}:{name}"
        self.graph.nodes.setdefault(
            target,
            CodeUnit(
                target,
                name,
                NodeKind.MODULE,
                make_location(node, self.path),
                origin=NodeOrigin.PROXY,
            ),
        )
        self.graph.edges.append(
            CodeEdge(
                self.module,
                target,
                EdgeKind.IMPORTS,
                location=make_location(node, self.path),
                attributes=(("import_name", name),),
            )
        )

    def unsupported(self, node: Node, message: str) -> None:
        self.graph.parse_diagnostics.append(
            ParseDiagnostic(
                self.language,
                self.language,
                make_location(node, self.path),
                "unsupported",
                message,
            )
        )

    def references(
        self,
        owner: str,
        root: Node,
        identifiers: frozenset[str],
        *,
        namespace: str = "",
        opens: tuple[str, ...] = (),
        aliases: dict[str, str] | None = None,
        scopes: tuple[Scope, ...] = (),
        skip: frozenset[str] = frozenset(),
        application_heads: frozenset[int] = frozenset(),
        context: str = "body",
        spelling: Callable[[Node], str | None] | None = None,
    ) -> None:
        pending = [root]
        while pending:
            node = pending.pop()
            if node.type in skip or node.is_error or node.is_missing:
                continue
            name = (
                (spelling(node) if spelling else node_text(node))
                if node.type in identifiers
                else None
            )
            if name is None:
                pending.extend(reversed(node.named_children))
                continue
            if name == "_" or any(scope.blocks(name, node.start_byte) for scope in scopes):
                continue
            local_opens = tuple(
                prefix
                for scope in scopes
                if scope.start <= node.start_byte < scope.end
                for prefix in scope.opens
            )
            tiers = lookup_tiers(name, namespace, (*opens, *local_opens), aliases or {})
            contexts = [
                scope.context
                for scope in scopes
                if scope.context is not None and scope.start <= node.start_byte < scope.end
            ]
            reference_context = next((c for c in reversed(contexts) if c != "argument"), context)
            if reference_context == "body" and "argument" in contexts:
                reference_context = "argument"
            self.graph.edges.append(
                CodeEdge(
                    owner,
                    "@pending",
                    EdgeKind.REFERENCES,
                    location=make_location(node, self.path),
                    attributes=(
                        ("reference_context", reference_context),
                        ("spelling", name),
                        ("_formal_candidates", json.dumps(tiers)),
                        ("_formal_application", node.start_byte in application_heads),
                    ),
                )
            )


class FormalParser:
    """Common file/directory lifecycle; subclasses implement source syntax only."""

    language: str
    extensions: tuple[str, ...]

    def __init__(self) -> None:
        self._parser = Parser(self.load_language())

    def load_language(self) -> Language:
        raise NotImplementedError

    def extract(self, root: Node, source: Source) -> None:
        raise NotImplementedError

    def _parse_file(self, path: str) -> CodeGraph:
        root = self._parser.parse(Path(path).read_bytes()).root_node
        source = Source(root, path, self.language)
        self.extract(root, source)
        return source.graph

    def parse_file(self, file_path: str) -> CodeGraph:
        graph = self._parse_file(file_path)
        link_symbols(graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        return parse_directory(
            self._parse_file,
            self.language,
            dir_path,
            self.extensions,
            link_calls=link_symbols,
        )
