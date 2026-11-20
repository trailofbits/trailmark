"""F* declarations and source references, without elaboration or extraction."""

from __future__ import annotations

from dataclasses import replace

from tree_sitter import Language, Node

from trailmark.models.nodes import Attribute, NodeKind, Parameter, TypeRef
from trailmark.parsers._common import node_text
from trailmark.parsers._formal import FormalParser, Namespace, Scope, Source, walk
from trailmark.parsers.fstar._bindings import (
    APPLICATION_HEAD_TYPES,
    OPAQUE,
    SKIP,
    Binding,
    application_head,
    bindings,
    groups,
    scopes,
)
from trailmark.parsers.fstar._semantics import link_fstar, operator_name
from trailmark.tree_sitter_custom._native import bundled_language

_NAMES = APPLICATION_HEAD_TYPES


def _spelling(node: Node) -> str | None:
    if node.type in {"operator", "operator_identifier"}:
        return operator_name(node_text(node))
    parent = node.parent
    if (
        parent is not None
        and parent.type == "projection_expression"
        and node == parent.child_by_field_name("field")
    ):
        return None
    if parent is not None and parent.type in {
        "record_field",
        "record_field_declaration",
        "constructor_declaration",
        "record_pattern_field",
    }:
        label = parent.child_by_field_name("field") or parent.child_by_field_name("name")
        if node == label and (
            parent.type != "record_field" or parent.child_by_field_name("value") is not None
        ):
            return None
    return node_text(node)


def _effect(node: Node) -> str:
    while node.type == "arrow_type":
        right = node.child_by_field_name("right")
        if right is None:
            return ""
        node = right
    head = node.child_by_field_name("function")
    return (
        node_text(head)
        if node.type == "application_expression" and head is not None
        else node_text(node).split()[0]
    )


def _parameters(slot: Binding) -> tuple[Parameter, ...]:
    result = []
    for parameter in slot.parameters:
        typed = parameter.child_by_field_name("type")
        result.extend(
            Parameter(name, TypeRef(node_text(typed)) if typed else None)
            for name in bindings(parameter)
        )
    return tuple(result)


class FStarParser(FormalParser):
    """Retain F* source dependencies and explicit computation/ghost evidence."""

    language = "fstar"
    extensions = (".fst", ".fsti")
    link = staticmethod(link_fstar)

    def load_language(self) -> Language:
        return Language(bundled_language("fstar"))

    def extract(self, root: Node, source: Source) -> None:
        source.ordered_declarations()
        env = Namespace(source)
        for node in root.named_children:
            env = self._declaration(node, env)
        for node in walk(root, OPAQUE, include_skipped=True):
            if node.type in OPAQUE:
                source.unsupported(node, "F* embedded or generated proof syntax is not expanded")

    def _declaration(self, node: Node, env: Namespace) -> Namespace:
        name = node.child_by_field_name("name")
        if node.type == "module_declaration" and name is not None:
            env.source.module_name(node_text(name))
            return replace(env, name=node_text(name))
        if node.type in {
            "module_alias",
            "open_declaration",
            "include_declaration",
            "friend_declaration",
        }:
            return self._import(node, env)
        if node.type in {"let_declaration", "val_declaration", "instance_declaration"}:
            self._binding(node, env)
        elif node.type == "effect_abbreviation":
            self._effect_declaration(node, env)
        elif node.type in {"type_declaration", "class_declaration"}:
            self._types(node, env)
        elif node.type == "directive":
            self._directive(node, env)
        elif node.type not in SKIP and node.type not in OPAQUE:
            env.source.unsupported(node, f"F* {node.type} is not modeled")
        return env

    @staticmethod
    def _directive(node: Node, env: Namespace) -> None:
        if any(c.type in {"#check", "#eval"} for c in node.children):
            env.source.unsupported(node, "F* interactive evaluation directives are not executed")

    @staticmethod
    def _effect_declaration(node: Node, env: Namespace) -> None:
        name = node.child_by_field_name("name")
        if name is not None:
            value = node.child_by_field_name("value")
            env.source.declare(
                node,
                node_text(name),
                "effect",
                namespace=env.name,
                attributes=(
                    ("executable", False),
                    ("effect_base", _effect(value) if value else ""),
                ),
            )

    @staticmethod
    def _import(node: Node, env: Namespace) -> Namespace:
        name = node.child_by_field_name("name")
        if name is None:
            return env
        if node.type == "module_alias":
            value = node.child_by_field_name("value")
            if value is None:
                return env
            env.source.import_(node, node_text(value))
            return replace(env, aliases={**env.aliases, node_text(name): node_text(value)})
        env.source.import_(node, node_text(name))
        if any(c.type == "import_restriction" for c in node.named_children):
            env.source.unsupported(node, "F* restricted imports and renamings are not modeled")
            return env
        if node.type == "include_declaration":
            env.source.reexports(env.name, (node_text(name),))
        return (
            env
            if node.type == "friend_declaration"
            else replace(env, opens=(*env.opens, node_text(name)))
        )

    def _binding(self, node: Node, env: Namespace) -> None:
        slots = groups(node)
        if not slots:
            self._pattern_binding(node, env)
            return
        flags = {node_text(p) for p in node.named_children if p.type == "qualifier"}
        bindings_ = scopes(node)
        for slot in slots:
            effects = {_effect(p).rsplit(".", 1)[-1] for p in slot.types}
            proof = "Lemma" in effects
            attrs: tuple[Attribute, ...] = (
                ("effect", _effect(slot.types[-1]) if slot.types else ""),
                ("signature", node.type == "val_declaration"),
                ("assumed", "assume" in flags),
                ("private", "private" in flags),
                (
                    "executable",
                    not (
                        proof
                        or effects & {"Ghost", "GTot", "Type", "Type0", "prop", "logical", "squash"}
                        or any(
                            node_text(p).split()[0]
                            in {"Type", "Type0", "prop", "logical", "squash"}
                            for p in slot.types
                        )
                        or ("noextract" in flags and "inline_for_extraction" not in flags)
                    ),
                ),
                ("_inherit_effect", not slot.types and node.type != "val_declaration"),
            )
            owner = env.source.declare(
                node,
                operator_name(node_text(slot.name))
                if slot.name.type == "operator_identifier"
                else node_text(slot.name),
                "lemma" if proof else "value" if node.type == "val_declaration" else "function",
                namespace=env.name,
                parameters=_parameters(slot),
                attributes=attrs,
            )
            local = bindings_
            if not any(child.type == "rec" for child in node.children):
                local += tuple(
                    Scope(frozenset({node_text(slot.name)}), p.start_byte, p.end_byte)
                    for p in slot.values
                )
            for region in (*slot.parameters, *slot.types, *slot.values):
                context = "type" if region not in slot.values else "proof" if proof else "body"
                self._references(region, owner, env, bindings_=local, context=context)

    def _pattern_binding(self, node: Node, env: Namespace) -> None:
        names = tuple(
            name for pattern in node.children_by_field_name("pattern") for name in bindings(pattern)
        )
        if not names:
            names = (f"<initializer>@{node.start_point.row + 1}",)
        for name in names:
            owner = env.source.declare(node, name, "value", namespace=env.name)
            for value in node.children_by_field_name("value"):
                self._references(value, owner, env, bindings_=scopes(node), context="definition")

    def _types(self, node: Node, env: Namespace) -> None:
        private = any(
            p.type == "qualifier" and node_text(p) == "private" for p in node.named_children
        )
        for definition in (p for p in node.named_children if p.type == "type_definition"):
            name = definition.child_by_field_name("name")
            if name is None:
                continue
            owner = env.source.declare(
                definition,
                node_text(name),
                "type",
                namespace=env.name,
                kind=NodeKind.STRUCT,
                attributes=(("executable", False), ("private", private)),
            )
            local = scopes(definition)
            for region in definition.children_by_field_name(
                "type"
            ) + definition.children_by_field_name("value"):
                self._references(region, owner, env, bindings_=local, context="type")
            self._type_members(definition, owner, env, private=private)

    @staticmethod
    def _type_members(definition: Node, owner: str, env: Namespace, *, private: bool) -> None:
        type_name = definition.child_by_field_name("name")
        if type_name is None:
            return
        for child in walk(definition, OPAQUE):
            if child.type not in {"constructor_declaration", "record_field_declaration"}:
                continue
            name = child.child_by_field_name("name")
            if name is None:
                continue
            role = "constructor" if child.type == "constructor_declaration" else "field"
            env.source.declare(
                child,
                node_text(name),
                role,
                namespace=env.name
                if role == "constructor"
                else f"{env.name}.{node_text(type_name)}",
                parent=owner,
                kind=NodeKind.FUNCTION if role == "constructor" else NodeKind.STATE_VARIABLE,
                attributes=(("private", private),),
            )

    @staticmethod
    def _references(
        node: Node, owner: str, env: Namespace, *, bindings_: tuple[Scope, ...], context: str
    ) -> None:
        heads = frozenset(
            head.start_byte
            for part in walk(node, SKIP)
            if (head := application_head(part)) is not None and head.type in _NAMES
        )
        env.source.references(
            owner,
            node,
            _NAMES,
            namespace=env.name,
            opens=env.opens,
            aliases=env.aliases,
            scopes=bindings_,
            skip=SKIP,
            application_heads=heads,
            context=context,
            spelling=_spelling,
        )
