"""Dafny 4 declarations and explicit references; no verification or lowering."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from tree_sitter import Language, Node

from trailmark.models.nodes import Attribute, NodeKind
from trailmark.parsers._common import node_text
from trailmark.parsers._formal import FormalParser, Namespace, Scope, Source, walk
from trailmark.parsers.dafny._scopes import SKIP, ghost_parameters, parameters, scopes
from trailmark.tree_sitter_custom._native import bundled_language

_CONTAINERS = {
    "module_definition": NodeKind.NAMESPACE,
    "class_decl": NodeKind.CLASS,
    "trait_decl": NodeKind.TRAIT,
    "datatype_decl": NodeKind.ENUM,
}
_FUNCTIONS = {"function_decl", "method_decl", "constructor_decl", "iterator_decl"}
_VALUES = {"synonym_type_decl", "newtype_decl", "field_decl", "constant_field_decl"}
_NAMES = frozenset({"name_segment", "qualified_name", "suffix_expression"})


def _spelling(node: Node) -> str | None:
    if node.type == "name_segment":
        name = next((n for n in node.named_children if n.type == "identifier"), None)
        return node_text(name) if name is not None else None
    if node.type == "qualified_name":
        return ".".join(node_text(n) for n in node.named_children)
    if node.type == "suffix_expression":
        obj, member = node.child_by_field_name("object"), node.child_by_field_name("member")
        if obj is not None and member is not None:
            head = _head(obj)
            return f"{head}.{node_text(member)}" if head else None
    return None


def _head(node: Node) -> str | None:
    while node.type not in _NAMES and len(node.named_children) == 1:
        node = node.named_children[0]
    return _spelling(node)


def _members(root: Node) -> list[Node]:
    result, pending = [], list(reversed(root.named_children))
    while pending:
        node = pending.pop()
        if node.type in {"module_body", "class_member"}:
            pending.extend(reversed(node.named_children))
        else:
            result.append(node)
    return result


def _imports(nodes: list[Node], env: Namespace) -> Namespace:
    opened, aliases = list(env.opens), env.aliases.copy()
    for node in nodes:
        if node.type == "include_directive":
            text = next((n for n in node.named_children if n.type == "string_literal"), None)
            if text is not None:
                env.source.import_(node, Path(node_text(text).strip('"')).stem)
        if node.type != "module_import":
            continue
        target = next((n for n in node.named_children if n.type == "qualified_name"), None)
        if target is None:
            continue
        name = node.child_by_field_name("name")
        env.source.import_(node, node_text(target))
        if name:
            aliases[node_text(name)] = node_text(target)
        if any(n.type == "opened" for n in node.children):
            opened.append(node_text(target))
    return replace(env, opens=tuple(opened), aliases=aliases)


def _role(node: Node, tokens: set[str]) -> str:
    if tokens & {"lemma", "colemma"}:
        return "lemma"
    if "predicate" in tokens or "copredicate" in tokens:
        return "predicate"
    return node.type.removesuffix("_decl")


def _ghost_metadata(node: Node) -> tuple[Attribute, ...]:
    erased = ghost_parameters(node)
    return (("ghost_parameters", json.dumps(erased)),) if erased else ()


class DafnyParser(FormalParser):
    """Extract source declarations with lexical binding and explicit ghost roles."""

    language = "dafny"
    extensions = (".dfy",)

    def load_language(self) -> Language:
        return Language(bundled_language("dafny"))

    def extract(self, root: Node, source: Source) -> None:
        self._declarations(root, Namespace(source))

    def _declarations(self, root: Node, env: Namespace) -> None:
        nodes = _members(root)
        env = _imports(nodes, env)
        modifiers: set[str] = set()
        for node in nodes:
            if node.type == "decl_modifier":
                modifiers.add(node_text(node))
                continue
            self._declaration(node, env, modifiers)
            modifiers = set()

    def _declaration(self, node: Node, env: Namespace, modifiers: set[str]) -> None:
        if node.type in _CONTAINERS:
            self._container(node, env, modifiers)
        elif node.type in _FUNCTIONS:
            self._function(node, env, modifiers)
        elif node.type == "datatype_ctor":
            self._constructor(node, env)
        elif node.type in _VALUES:
            self._value(node, env, modifiers)

    def _container(self, node: Node, env: Namespace, modifiers: set[str]) -> None:
        name = node.child_by_field_name("name")
        if name is None:
            return
        owner = env.source.declare(
            node,
            node_text(name),
            node.type.removesuffix("_decl"),
            namespace=env.name,
            parent=env.parent,
            kind=_CONTAINERS[node.type],
            attributes=(("ghost", "ghost" in modifiers),),
        )
        child = env.child(node_text(name), owner)
        env.source.module_name(child.name)
        self._declarations(node, child)

    def _constructor(self, node: Node, env: Namespace) -> None:
        name = node.child_by_field_name("name")
        if name is None:
            return
        outer = env.name.rpartition(".")[0]
        alias = f"{outer}.{node_text(name)}" if outer else node_text(name)
        owner = env.source.declare(
            node,
            node_text(name),
            "constructor",
            namespace=env.name,
            parent=env.parent,
            parameters=parameters(node),
            attributes=(("executable", True), ("qualified_aliases", json.dumps([alias])))
            + _ghost_metadata(node),
        )
        for formal in (n for n in node.named_children if n.type == "datatype_formal"):
            field = formal.child_by_field_name("name")
            if field:
                env.source.declare(
                    formal,
                    node_text(field),
                    "field",
                    namespace=f"{env.name}.{node_text(name)}",
                    parent=owner,
                    kind=NodeKind.STATE_VARIABLE,
                    attributes=(("ghost", any(c.type == "ghost" for c in formal.children)),),
                )

    def _value(self, node: Node, env: Namespace, modifiers: set[str]) -> None:
        kind = NodeKind.STRUCT if "type" in node.type else NodeKind.STATE_VARIABLE
        for name in node.children_by_field_name("name"):
            owner = env.source.declare(
                node,
                node_text(name),
                "type" if kind == NodeKind.STRUCT else "field",
                namespace=env.name,
                parent=env.parent,
                kind=kind,
                attributes=(("ghost", "ghost" in modifiers), ("executable", False)),
            )
            self._references(node, owner, env, context="initializer")

    def _function(self, node: Node, env: Namespace, modifiers: set[str]) -> None:
        name = node.child_by_field_name("name")
        tokens = {n.type for n in node.children if not n.is_named}
        role = _role(node, tokens)
        ghost = "ghost" in modifiers or bool(
            tokens
            & {"lemma", "colemma", "least", "greatest", "twostate", "inductive", "copredicate"}
        )
        assumed = any(
            n.type == "attribute" and any(node_text(c) == "axiom" for c in n.named_children)
            for n in node.named_children
        )
        attrs: tuple[Attribute, ...] = (("executable", not ghost), ("assumed", assumed))
        attrs += _ghost_metadata(node)
        parent = env.source.graph.nodes[env.parent or env.source.module]
        owner = env.source.declare(
            node,
            node_text(name) if name else f"<{role}>",
            role,
            namespace=env.name,
            parent=env.parent,
            kind=NodeKind.METHOD
            if parent.kind in {NodeKind.CLASS, NodeKind.TRAIT}
            else NodeKind.FUNCTION,
            parameters=parameters(node),
            attributes=attrs,
        )
        bindings = scopes(node)
        for region in node.named_children:
            if region == name or region.type in SKIP:
                continue
            context = "contract" if region.type.endswith("_clause") else "type"
            if region.type in {"function_body", "block_statement"}:
                context = "proof" if ghost else "body"
            self._references(region, owner, env, bindings=bindings, context=context)

    @staticmethod
    def _references(
        node: Node, owner: str, env: Namespace, *, bindings: tuple[Scope, ...] = (), context: str
    ) -> None:
        heads = frozenset(
            head.start_byte
            for part in walk(node, SKIP)
            if (head := part.child_by_field_name("function")) is not None
        )
        env.source.references(
            owner,
            node,
            _NAMES,
            namespace=env.name,
            opens=env.opens,
            aliases=env.aliases,
            scopes=bindings,
            skip=SKIP,
            application_heads=heads,
            context=context,
            spelling=_spelling,
        )
