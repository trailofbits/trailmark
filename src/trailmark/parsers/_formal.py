"""Shared source evidence for declarative and verification-language frontends.

Adapters own their syntax, binders, visibility and executable/specification
classification. This module never interprets proof names or runs a verifier.
"""

from __future__ import annotations

import json
from bisect import bisect_right
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from heapq import heappop, heappush
from pathlib import Path

from tree_sitter import Language, Node, Parser, Range

from trailmark.models._parsing import ArgumentScope, Lookup, PendingReference
from trailmark.models.diagnostics import ParseDiagnostic
from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import Attribute, CodeUnit, NodeKind, NodeOrigin, Parameter
from trailmark.parsers._call_sites import collect_call_sites
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


def parse_regions(parser: Parser, data: bytes, regions: list[tuple[int, int]]) -> list[Node]:
    """Parse disjoint recovery regions with original offsets and shared source bytes."""
    starts = [0, *(i + 1 for i, byte in enumerate(data) if byte == 10)]
    bounded = Parser(parser.language)
    result = []
    for start, end in regions:
        start_line, end_line = bisect_right(starts, start) - 1, bisect_right(starts, end) - 1
        bounded.included_ranges = [
            Range(
                start_point=(start_line, start - starts[start_line]),
                end_point=(end_line, end - starts[end_line]),
                start_byte=start,
                end_byte=end,
            )
        ]
        result.extend(bounded.parse(data).root_node.named_children)
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


def argument_scopes(head: Node | None, arguments: list[Node]) -> list[Scope]:
    return [
        Scope(
            frozenset(),
            argument.start_byte,
            argument.end_byte,
            context="argument",
            callee=head.start_byte if head is not None else None,
            argument=index,
        )
        for index, argument in enumerate(arguments)
    ]


class _ScopeIndex:
    """Sweep source-ordered references without revisiting unrelated scopes."""

    def __init__(self, scopes: tuple[Scope, ...]) -> None:
        self.scopes = scopes
        self.bound: Counter[str] = Counter()
        self.pending = sorted(enumerate(scopes), key=lambda item: item[1].start)
        self.offset = 0
        self.active: dict[int, Scope] = {}
        self.ends: list[tuple[int, int]] = []
        self.arguments: list[tuple[int, int, int]] = []
        self.parents: dict[str, ArgumentScope] = {}
        stack: list[Scope] = []
        for scope in sorted(
            (s for s in scopes if s.context == "argument"), key=lambda s: (s.start, -s.end)
        ):
            while stack and stack[-1].end <= scope.start:
                stack.pop()
            self.parents[self.key(scope)] = ArgumentScope(
                scope.callee,
                scope.argument,
                self.key(stack[-1]) if stack else None,
            )
            stack.append(scope)

    @staticmethod
    def key(scope: Scope) -> str:
        return f"{scope.start}:{scope.end}:{scope.callee}:{scope.argument}"

    def at(self, position: int) -> tuple[Scope, ...]:
        while self.offset < len(self.pending) and self.pending[self.offset][1].start <= position:
            index, scope = self.pending[self.offset]
            self.offset += 1
            self.bound.update(scope.names)
            if scope.context == "argument":
                heappush(self.arguments, (-scope.start, -index, scope.end))
            elif scope.opens or scope.context or scope.callee is not None:
                self.active[index] = scope
            heappush(self.ends, (scope.end, index))
        while self.ends and self.ends[0][0] <= position:
            _, index = heappop(self.ends)
            self.bound.subtract(self.scopes[index].names)
            self.active.pop(index, None)
        while self.arguments and self.arguments[0][2] <= position:
            heappop(self.arguments)
        active = list(self.active.values())
        if self.arguments:
            active.append(self.scopes[-self.arguments[0][1]])
        return tuple(active)


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

    def __init__(self, root: Node, path: str, language: str, *, data: bytes = b"") -> None:
        self.path, self.language = path, language
        self.data = data
        self.module = module_id_from_path(path)
        self.graph = CodeGraph(language=language, root_path=path)
        self.graph._parse_state.resolved_type_files.add(path)
        self.ghost_fields: set[str] = set()
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

    def reexports(
        self, namespace: str, imports: tuple[str, ...], *, qualified: bool = True
    ) -> None:
        """Accumulate explicit exports; unqualified exports propagate opens only."""
        unit = self.graph.nodes[self.module]
        attrs = dict(unit.attributes)
        key = "formal_exports" if qualified else "formal_open_exports"
        exports = json.loads(str(attrs.get(key, "{}")))
        exports[namespace] = sorted(set(exports.get(namespace, ())).union(imports))
        attrs[key] = json.dumps(exports, sort_keys=True)
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
        if kind == NodeKind.STATE_VARIABLE and dict(attributes).get("ghost"):
            self.ghost_fields.add(qualified)
        self.graph.edges.append(CodeEdge(parent or self.module, ident, EdgeKind.CONTAINS))
        return ident

    def import_(
        self, node: Node, name: str, *, relative_path: str | None = None, parent: str | None = None
    ) -> None:
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
                parent or self.module,
                target,
                EdgeKind.IMPORTS,
                location=make_location(node, self.path),
                attributes=(
                    (("import_name", name),)
                    if relative_path is None
                    else (("import_name", name), ("import_path", relative_path))
                ),
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
        bound_applications: dict[int, str] | None = None,
        context: str = "body",
        spelling: Callable[[Node], str | None] | None = None,
    ) -> None:
        pending = [root]
        index = _ScopeIndex(scopes)
        self.graph._parse_state.arguments.setdefault(owner, {}).update(index.parents)
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
            bound_name = node_text(node) if self.language == "tamarin" else name.split(".")[0]
            bound_target = (bound_applications or {}).get(node.start_byte)
            bound = index.bound[bound_name] > 0
            bound_application = (
                self.language == "fstar" and bound and node.start_byte in application_heads
            )
            if name == "_" or (bound and bound_target is None and not bound_application):
                continue
            local_opens = tuple(prefix for scope in active for prefix in scope.opens)
            tiers = lookup_tiers(name, namespace, (*opens, *local_opens), aliases or {})
            if self.language == "fstar" and not any(
                name == key or name.startswith(key + ".") for key in aliases or {}
            ):
                tiers[-1:] = [
                    [name],
                    *[[f"{prefix}.{name}"] for prefix in reversed((*opens, *local_opens))],
                ]
            contexts = [scope.context for scope in active if scope.context is not None]
            reference_context = next((c for c in reversed(contexts) if c != "argument"), context)
            if reference_context == "body" and "argument" in contexts:
                reference_context = "argument"
            edge = CodeEdge(
                owner,
                bound_target or "@pending",
                EdgeKind.REFERENCES,
                confidence=EdgeConfidence.UNCERTAIN if bound_target else EdgeConfidence.CERTAIN,
                location=make_location(node, self.path),
                attributes=(("reference_context", reference_context), ("spelling", name)),
            )
            self.graph.edges.append(
                self.graph._parse_state.stage(
                    edge,
                    PendingReference(
                        lookup=None if bound_target else Lookup(tuple(map(tuple, tiers))),
                        application=node.start_byte in application_heads,
                        bound_application=bound_application,
                        position=node.start_byte,
                        enclosing=next(
                            (
                                index.key(scope)
                                for scope in reversed(active)
                                if scope.context == "argument"
                            ),
                            None,
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
    link: Callable[[CodeGraph], None] = staticmethod(link_symbols)

    def __init__(self) -> None:
        self._parser = Parser(self.load_language())

    def load_language(self) -> Language:
        raise NotImplementedError

    def extract(self, root: Node, source: Source) -> None:
        raise NotImplementedError

    def _parse_file(self, path: str) -> CodeGraph:
        data = Path(path).read_bytes()
        root = self._parser.parse(data).root_node
        source = Source(root, path, self.language, data=data)
        pending = [(root, 0)]
        containers = {"module", "module_definition", "module_declaration", "scope_declaration"}
        while pending:
            node, depth = pending.pop()
            depth += node.type in containers
            if depth > 128:
                source.unsupported(node, "Declaration nesting exceeds the supported depth of 128")
                return source.graph
            pending.extend((child, depth) for child in node.named_children)
        self.extract(root, source)
        collect_call_sites(root, path, source.graph, source=data)
        return source.graph

    def parse_file(self, file_path: str) -> CodeGraph:
        graph = self._parse_file(file_path)
        self.link(graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        return parse_directory(
            self._parse_file,
            self.language,
            dir_path,
            self.extensions,
            link_calls=self.link,
        )
