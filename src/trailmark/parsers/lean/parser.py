"""Lean 4 declarations and conservative syntax-based graph extraction."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from tree_sitter import Node, Parser
from tree_sitter_language_pack import get_language

from trailmark.models.edges import CodeEdge, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import Attribute, CodeUnit, NodeKind
from trailmark.parsers._call_sites import collect_call_sites
from trailmark.parsers._common import (
    add_contains_edge,
    add_module_node,
    make_location,
    module_id_from_path,
    node_text,
    parse_directory,
)
from trailmark.parsers._simple import first_child_type
from trailmark.parsers.lean._resolution import link_symbols
from trailmark.parsers.lean._syntax import (
    DECLARATIONS,
    TYPES,
    Binding,
    Context,
    Declaration,
    Task,
    docstring,
    field,
    fields,
    parameters,
    qualify,
    type_ref,
)
from trailmark.parsers.lean._terms import TermExtractor


class LeanParser:
    """Parse Lean 4 without running Lean, Lake, elaboration, or tactics."""

    @property
    def language(self) -> str:
        return "lean"

    def __init__(self) -> None:
        self._parser = Parser(get_language("lean"))

    def parse_file(self, file_path: str) -> CodeGraph:
        graph = self._parse_file(file_path)
        link_symbols(graph)
        return graph

    def _parse_file(self, file_path: str) -> CodeGraph:
        # Directory parsing retains pending lookups until inspected imports merge.
        root = self._parser.parse(Path(file_path).read_bytes()).root_node
        graph = CodeGraph(language="lean", root_path=file_path)
        graph._parse_state.resolved_type_files.add(file_path)
        module = module_id_from_path(file_path)
        add_module_node(root, file_path, module, graph)
        _Extractor(graph, module, file_path).declarations(root)
        collect_call_sites(root, file_path, graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        return parse_directory(
            self._parse_file, "lean", dir_path, (".lean",), link_calls=link_symbols
        )


@dataclass(frozen=True)
class _Scope:
    namespace: str
    opens: tuple[str, ...]
    variables: dict[str, Binding]
    mutual_group: str | None


class _Extractor(TermExtractor):
    def __init__(self, graph: CodeGraph, module: str, path: str) -> None:
        super().__init__(graph, module, path)
        self._scopes: list[_Scope] = []

    def scope(self) -> _Scope:
        return _Scope(self.namespace, self.opens, self.variables.copy(), self.mutual_group)

    def restore(self, scope: _Scope) -> None:
        self.namespace, self.opens = scope.namespace, scope.opens
        self.variables, self.mutual_group = scope.variables, scope.mutual_group

    def declarations(self, root: Node) -> None:
        pending: list[Node | _Scope] = list(reversed(root.named_children))
        while pending:
            command = pending.pop()
            if isinstance(command, _Scope):
                self.restore(command)
                continue
            saved = self.scope()
            body = self.command(command)
            if body is not None:
                pending.extend((saved, body))

    def command(self, node: Node) -> Node | None:
        handlers = {
            "import": self.import_,
            "namespace": self.enter,
            "section": self.enter,
            "public_section": self.enter,
            "mutual": self.enter,
            "end": self.leave,
            "open": self.open_,
            "variable": self.variable,
            "set_option": self.option,
            "declaration": self.declaration,
        }
        handler = handlers.get(node.type)
        return handler(node) if handler else None

    def import_(self, node: Node) -> None:
        name = field(node, "name")
        if name is not None:
            target = node_text(name)
            self.graph.dependencies.append(target)
            self.graph.edges.append(
                CodeEdge(
                    self.module, target, EdgeKind.IMPORTS, location=make_location(node, self.path)
                )
            )

    def enter(self, node: Node) -> None:
        self._scopes.append(self.scope())
        if node.type == "mutual":
            self.mutual_group = f"{self.module}@{node.start_byte}"
        if node.type == "namespace":
            name = field(node, "name")
            if name is not None:
                self.namespace = qualify(self.namespace, node_text(name))
                self.namespace_owner(node, self.namespace)

    def leave(self, node: Node) -> None:
        if self._scopes:
            self.restore(self._scopes.pop())

    def open_(self, node: Node) -> Node | None:
        namespaces = fields(node, "namespace")
        only = fields(node, "only")
        restricted = any(node_text(n) in {"hiding", "renaming"} for n in namespaces)
        if not only and not restricted:
            self.opens += tuple(node_text(n).removeprefix("_root_.") for n in namespaces)
        self.variables.update((node_text(n), Binding()) for n in only)
        return field(node, "body")

    def variable(self, node: Node) -> Node | None:
        self.binders(node, Context(self.module, self.variables))
        return field(node, "body")

    def option(self, node: Node) -> Node | None:
        return field(node, "body")

    def declaration(self, wrapper: Node) -> None:
        node = first_child_type(wrapper, DECLARATIONS | TYPES.keys())
        if node is None:
            return
        name = field(node, "name")
        qualified = qualify(
            self.namespace, node_text(name) if name else f"<{node.type}>@{node.start_byte}"
        )
        ident = f"{self.module}:{qualified}"
        parent = self.namespace_owner(wrapper, qualified.rpartition(".")[0])
        declaration = Declaration(parent, self.attributes(wrapper, qualified), docstring(wrapper))
        ctx = Context(ident, self.variables.copy())
        if node.type in TYPES:
            self.type_declaration(node, ctx, declaration)
        else:
            self.run([Task(node, ctx, "function", declaration)])

    def attributes(self, wrapper: Node, qualified: str) -> tuple[Attribute, ...]:
        modifiers = first_child_type(wrapper, {"decl_modifiers"})
        words = node_text(modifiers).split() if modifiers else []
        result = (("lean_qualified_name", qualified), ("lean_private", "private" in words))
        if "protected" in words:
            result += (("lean_protected", True),)
        if self.mutual_group is not None:
            result += (("_lean_mutual_group", self.mutual_group),)
        return result

    def namespace_owner(self, node: Node, namespace: str) -> str:
        owner, prefix = self.module, ""
        for part in namespace.split(".") if namespace else ():
            prefix = qualify(prefix, part)
            ident = f"{self.module}:{prefix}"
            if ident not in self.graph.nodes:
                self.graph.nodes[ident] = CodeUnit(
                    ident, part, NodeKind.NAMESPACE, make_location(node, self.path)
                )
                add_contains_edge(self.graph, owner, ident)
            owner = ident
        return owner

    def type_declaration(self, node: Node, ctx: Context, declaration: Declaration) -> None:
        kind = NodeKind.TRAIT if node.children[0].type == "class" else TYPES[node.type]
        name = field(node, "name")
        if name is None:
            return
        self.graph.nodes[ctx.owner] = CodeUnit(
            ctx.owner,
            node_text(name),
            kind,
            make_location(node, self.path),
            parameters=parameters(node),
            docstring=declaration.doc,
            attributes=(*declaration.attributes, ("lean_declaration", node.type)),
        )
        add_contains_edge(self.graph, declaration.parent, ctx.owner)
        self.run(
            self.signature(
                node, replace(ctx, reference_context="signature", calls=False, type_uses=True)
            )
        )
        for member in node.named_children:
            if member.type in {"field", "ctor", "ctor_alt"}:
                self.type_member(member, ctx, kind)

    def type_member(self, node: Node, parent: Context, parent_kind: NodeKind) -> None:
        name = field(node, "name")
        if name is None:
            return
        ident = f"{parent.owner}.{node_text(name)}"
        kind = _member_kind(node, parent_kind)
        inherited = tuple(
            (k, v)
            for k, v in self.graph.nodes[parent.owner].attributes
            if k in {"lean_private", "_lean_mutual_group"}
        )
        self.graph.nodes[ident] = CodeUnit(
            ident,
            node_text(name),
            kind,
            make_location(node, self.path),
            parameters=parameters(node),
            return_type=type_ref(node),
            docstring=docstring(node),
            attributes=(
                *inherited,
                ("lean_declaration", node.type),
                ("lean_qualified_name", ident.split(":", 1)[1]),
            ),
        )
        add_contains_edge(self.graph, parent.owner, ident)
        ctx = replace(
            parent.fork(), owner=ident, calls=False, type_uses=True, reference_context="signature"
        )
        tasks = self.signature(node, ctx)
        default = replace(ctx, reference_context="body", type_uses=False)
        tasks += [
            Task(value, default.fork()) for value in fields(node, "value") + fields(node, "default")
        ]
        self.run(tasks)
        parent.bindings[node_text(name)] = Binding(target=ident)


def _member_kind(node: Node, parent: NodeKind) -> NodeKind:
    if node.type != "field":
        return NodeKind.FUNCTION
    return NodeKind.METHOD if parent == NodeKind.TRAIT else NodeKind.STATE_VARIABLE
