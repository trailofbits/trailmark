"""A bounded WhyML syntax frontend; no Why3 transformations or clone expansion."""

import json
from dataclasses import replace
from pathlib import Path

from tree_sitter import Language, Node

from trailmark.models.nodes import Attribute, NodeKind
from trailmark.parsers._common import node_text
from trailmark.parsers._formal import FormalParser, Namespace, Source, walk
from trailmark.parsers.whyml._scopes import SKIP, parameters, scopes
from trailmark.tree_sitter_custom._native import bundled_language

_NAMES = frozenset({"identifier", "qualified_identifier", "constructor_identifier"})
_KINDS = {"type_declaration": NodeKind.STRUCT, "field_declaration": NodeKind.STATE_VARIABLE}


class WhyMLParser(FormalParser):
    """Extract explicit program and specification dependencies in WhyML."""

    language = "whyml"
    extensions = (".mlw", ".why")

    def load_language(self) -> Language:
        return Language(bundled_language("whyml"))

    def extract(self, root: Node, source: Source) -> None:
        source.ordered_declarations()
        for node in walk(root):
            if node.type == "unsupported_keyword":
                source.unsupported(node, f"WhyML {node_text(node)} syntax is not modeled")
        for node in root.named_children:
            if node.type == "module_declaration":
                self._container(node, Namespace(source))

    def _container(self, root: Node, env: Namespace) -> str:
        name = root.child_by_field_name("name")
        if name is None:
            return env.name
        owner = env.source.declare(
            root,
            node_text(name),
            "module" if not env.name else "scope",
            namespace=env.name,
            parent=env.parent,
            kind=NodeKind.NAMESPACE,
        )
        env = env.child(node_text(name), owner)
        env.source.module_name(env.name)
        env.source.module_name(f"{Path(env.source.path).stem}.{env.name}")
        for node in root.named_children:
            if node.type == "scope_declaration":
                nested = self._container(node, env)
                if any(c.type in {"import", "export"} for c in node.children):
                    env = replace(env, opens=(*env.opens, nested))
            elif node.type in {"use_declaration", "clone_declaration"}:
                env = self._import(node, env)
            elif node.type.endswith("_declaration") or node.type == "proposition":
                self._declaration(node, env)
        return env.name

    @staticmethod
    def _import(node: Node, env: Namespace) -> Namespace:
        imported = node.child_by_field_name("module")
        if imported is None:
            return env
        imported_name = node_text(imported)
        env.source.import_(node, imported_name)
        if node.type == "clone_declaration":
            env.source.unsupported(
                node, "WhyML clone substitutions and generated declarations are not expanded"
            )
            return env
        alias = node.child_by_field_name("alias")
        canonical = imported_name.rsplit(".", 1)[-1]
        if any(c.type == "export" for c in node.children):
            env.source.reexports(env.name, (canonical,))
        aliases = {
            **env.aliases,
            imported_name: canonical,
            node_text(alias) if alias else canonical: canonical,
        }
        opened = alias is None or any(c.type in {"import", "export"} for c in node.children)
        return replace(env, aliases=aliases, opens=(*env.opens, canonical) if opened else env.opens)

    def _declaration(self, node: Node, env: Namespace) -> None:
        source, namespace, parent = env.source, env.name, env.parent
        name = node.child_by_field_name("name")
        if name is None:
            return
        tokens = {c.type for c in node.children if not c.is_named}
        role = next(
            (
                t
                for t in ("axiom", "lemma", "goal", "predicate", "function", "constant")
                if t in tokens
            ),
            node.type.removesuffix("_declaration"),
        )
        executable = node.type in {"let_declaration", "val_declaration"} and not tokens & {
            "ghost",
            "lemma",
        }
        erased = [
            node_text(n)
            for p in node.children_by_field_name("parameter")
            if any(c.type == "ghost" for c in p.children)
            for n in p.children_by_field_name("name")
        ]
        attrs: tuple[Attribute, ...] = (
            ("executable", executable),
            ("ghost", "ghost" in tokens),
            ("assumed", role == "axiom"),
        )
        if erased:
            attrs += (("ghost_parameters", json.dumps(erased)),)
        owner = source.declare(
            node,
            node_text(name),
            role,
            namespace=namespace,
            parent=parent,
            kind=_KINDS.get(node.type, NodeKind.FUNCTION),
            parameters=parameters(node),
            attributes=attrs,
        )
        bindings = scopes(node)
        heads = frozenset(
            head.start_byte
            for n in walk(node, SKIP)
            if n.type == "application"
            if (head := n.child_by_field_name("function")) is not None and head.type in _NAMES
        )
        for child in node.named_children:
            if child.type == "contract" and any(
                c.type in {"returns", "raises"} for c in child.children
            ):
                source.unsupported(child, "WhyML returns/raises pattern binders are not modeled")
                continue
            if child == name or child.type in {"record_type", "variant_type"}:
                continue
            context = (
                "body"
                if child == node.child_by_field_name("body") and executable
                else "specification"
            )
            source.references(
                owner,
                child,
                _NAMES,
                namespace=namespace,
                opens=env.opens,
                aliases=env.aliases,
                scopes=bindings,
                skip=SKIP,
                context=context,
                application_heads=heads,
            )
        for child in walk(node):
            if child != node and child.type in {"field_declaration", "constructor_declaration"}:
                self._declaration(child, replace(env, parent=owner))
