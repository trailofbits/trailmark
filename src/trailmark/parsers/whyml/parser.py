"""A bounded WhyML syntax frontend; no Why3 transformations or clone expansion."""

import json
import re
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from tree_sitter import Language, Node

from trailmark.models.nodes import Attribute, NodeKind, TypeRef
from trailmark.parsers._common import node_text
from trailmark.parsers._formal import FormalParser, Namespace, Scope, Source, parse_regions, walk
from trailmark.parsers.whyml._scopes import SKIP, parameters, scopes
from trailmark.tree_sitter_custom._native import bundled_language

_NAMES = frozenset({"identifier", "qualified_identifier", "constructor_identifier"})
_KINDS = {"type_declaration": NodeKind.STRUCT, "field_declaration": NodeKind.STATE_VARIABLE}


def _metadata(node: Node, path: str, group: int | None) -> tuple[str, tuple[Attribute, ...]]:
    tokens = {c.type for c in node.children if not c.is_named}
    role = next(
        (t for t in ("axiom", "lemma", "goal", "predicate", "function", "constant") if t in tokens),
        node.type.removesuffix("_declaration"),
    )
    executable = node.type in {
        "let_declaration",
        "val_declaration",
        "constructor_declaration",
        "recursive_binding",
    } and not tokens & {
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
    if group is not None:
        attrs += (("recursive_group", f"{path}:{group}"),)
    if erased:
        attrs += (("ghost_parameters", json.dumps(erased)),)
    return role, attrs


def _runtime_scopes(node: Node, env: Namespace) -> tuple[Scope, ...]:
    bindings = scopes(node)
    spelling = WhyMLParser._spelling(node, env)
    ghost_fields = {
        str(dict(n.attributes).get("qualified_name"))
        for n in env.source.graph.nodes.values()
        if n.kind == NodeKind.STATE_VARIABLE and dict(n.attributes).get("ghost")
    }
    for expression in walk(node, SKIP):
        if expression.type != "binary_expression" or len(expression.named_children) < 2:
            continue
        left, right = expression.named_children[0], expression.named_children[-1]
        tokens_ = {c.type for c in expression.children}
        if tokens_ & {"by", "so"} or ("<-" in tokens_ and spelling(left) in ghost_fields):
            bindings += (Scope(frozenset(), right.start_byte, right.end_byte, context="proof"),)
    return bindings


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
        implicit = Namespace(source, Path(source.path).stem.capitalize())
        for node in self._items(root):
            if node.type == "module_declaration":
                self._container(node, Namespace(source))
            elif node.type in {"use_declaration", "clone_declaration"}:
                implicit = self._import(node, implicit)
            elif node.type.endswith("_declaration") or node.type == "proposition":
                source.module_name(implicit.name)
                self._declaration(node, implicit)

    def _items(self, root: Node) -> list[Node]:
        if not root.has_error:
            return list(root.named_children)
        data = root.text or b""
        comments = [(n.start_byte, n.end_byte) for n in walk(root) if n.type == "comment"]
        starts = [
            m.start()
            for m in re.finditer(rb"(?m)^(?:module|theory)\s+[A-Z]", data)
            if not any(start <= m.start() < end for start, end in comments)
        ]
        if not starts:
            return list(root.named_children)
        regions = list(zip(starts, [*starts[1:], len(data)], strict=True))
        return [
            n for n in parse_regions(self._parser, data, regions) if n.type == "module_declaration"
        ]

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
        env.source.import_(node, imported_name, parent=env.parent)
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

    def _declaration(self, node: Node, env: Namespace, *, group: int | None = None) -> None:
        source, namespace, parent = env.source, env.name, env.parent
        name = node.child_by_field_name("name")
        if name is None:
            self._meta(node, env)
            return
        if node.type == "let_declaration" and any(c.type == "rec" for c in node.children):
            group = node.start_byte
        role, attrs = _metadata(node, source.path, group)
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
        self._references(node, owner, env, executable=dict(attrs).get("executable") is True)
        typed = node.child_by_field_name("type")
        if typed is not None:
            unit = source.graph.nodes[owner]
            source.graph.nodes[owner] = replace(unit, return_type=TypeRef(node_text(typed)))
        for child in node.named_children:
            if child.type == "recursive_binding":
                self._declaration(child, env, group=group)
        for child in walk(node):
            if child != node and child.type in {"field_declaration", "constructor_declaration"}:
                self._declaration(child, replace(env, parent=owner))

    @staticmethod
    def _meta(node: Node, env: Namespace) -> None:
        if node.type == "meta_declaration":
            env.source.references(
                env.parent or env.source.module,
                node,
                _NAMES,
                namespace=env.name,
                opens=env.opens,
                aliases=env.aliases,
                context="specification",
            )

    def _references(self, node: Node, owner: str, env: Namespace, *, executable: bool) -> None:
        name = node.child_by_field_name("name")
        bindings = _runtime_scopes(node, env)
        heads = frozenset(
            head.start_byte
            for n in walk(node, SKIP)
            if n.type == "application"
            if (head := n.child_by_field_name("function")) is not None and head.type in _NAMES
        )
        for child in node.named_children:
            if child == name or child.type in {"record_type", "variant_type", "recursive_binding"}:
                continue
            context = (
                "body"
                if child == node.child_by_field_name("body") and executable
                else "type"
                if child == node.child_by_field_name("type") or child.type == "binder"
                else "specification"
            )
            env.source.references(
                owner,
                child,
                _NAMES,
                namespace=env.name,
                opens=env.opens,
                aliases=env.aliases,
                scopes=bindings,
                skip=SKIP,
                context=context,
                application_heads=heads,
                spelling=self._spelling(node, env),
            )

    @staticmethod
    def _spelling(root: Node, env: Namespace) -> Callable[[Node], str]:
        types = {p.name: p.type_ref.name for p in parameters(root) if p.type_ref is not None}

        def spelling(node: Node) -> str:
            if (
                node.parent
                and node.parent.type == "record_assignment"
                and node == node.parent.child_by_field_name("name")
            ):
                return "_"
            value = node_text(node)
            base, dot, member = value.partition(".")
            if dot and base in types:
                return f"{env.name}.{types[base]}.{member}"
            return value

        return spelling
