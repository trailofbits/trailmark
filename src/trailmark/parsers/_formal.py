"""Shared source evidence for declarative and verification-language frontends.

Adapters own their syntax, binders, visibility and executable/specification
classification. This module never interprets proof names or runs a verifier.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from heapq import heappop, heappush
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


def walk(
    node: Node, skip: frozenset[str] = frozenset(), *, include_skipped: bool = False
) -> list[Node]:
    result, pending = [], [node]
    while pending:
        child = pending.pop()
        if include_skipped or child.type not in skip:
            result.append(child)
        if child.type not in skip:
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
    callee: int | None = None
    argument: int | str | None = None

    def blocks(self, name: str, position: int) -> bool:
        return self.start <= position < self.end and name.split(".")[0] in self.names


class _ScopeIndex:
    """Sweep source-ordered references without revisiting unrelated scopes."""

    def __init__(self, scopes: tuple[Scope, ...]) -> None:
        self.scopes = scopes
        self.bound: Counter[str] = Counter()
        self.pending = sorted(enumerate(scopes), key=lambda item: item[1].start)
        self.offset = 0
        self.active: dict[int, Scope] = {}
        self.ends: list[tuple[int, int]] = []

    def at(self, position: int) -> tuple[Scope, ...]:
        while self.offset < len(self.pending) and self.pending[self.offset][1].start <= position:
            index, scope = self.pending[self.offset]
            self.offset += 1
            self.bound.update(scope.names)
            if scope.opens or scope.context or scope.callee is not None:
                self.active[index] = scope
            heappush(self.ends, (scope.end, index))
        while self.ends and self.ends[0][0] <= position:
            _, index = heappop(self.ends)
            self.bound.subtract(self.scopes[index].names)
            self.active.pop(index, None)
        return tuple(self.active[index] for index in sorted(self.active))


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
        self.module_name(self.module)

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

    def reexports(self, namespace: str, imports: tuple[str, ...]) -> None:
        """Record namespaces whose members are explicitly re-exported."""
        unit = self.graph.nodes[self.module]
        attrs = dict(unit.attributes)
        exports = json.loads(str(attrs.get("formal_exports", "{}")))
        exports[namespace] = list(imports)
        attrs["formal_exports"] = json.dumps(exports, sort_keys=True)
        self.graph.nodes[self.module] = replace(unit, attributes=tuple(attrs.items()))

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
        index = _ScopeIndex(scopes)
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
            active = index.at(node.start_byte)
            if name == "_" or index.bound[name.split(".")[0]] > 0:
                continue
            local_opens = tuple(prefix for scope in active for prefix in scope.opens)
            tiers = lookup_tiers(name, namespace, (*opens, *local_opens), aliases or {})
            contexts = [scope.context for scope in active if scope.context is not None]
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
                        ("_formal_position", node.start_byte),
                        (
                            "_formal_enclosing",
                            json.dumps(
                                [
                                    [scope.callee, scope.argument]
                                    for scope in active
                                    if scope.context == "argument"
                                ]
                            ),
                        ),
                    ),
                )
            )


@dataclass(frozen=True)
class Namespace:
    """A frontend's declaration environment, separate from lexical binders."""

    source: Source
    name: str = ""
    parent: str | None = None
    opens: tuple[str, ...] = ()
    aliases: dict[str, str] = field(default_factory=dict)

    def child(self, name: str, parent: str) -> Namespace:
        qualified = f"{self.name}.{name}" if self.name else name
        return replace(self, name=qualified, parent=parent, aliases=self.aliases.copy())


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
