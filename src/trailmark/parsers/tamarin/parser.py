"""Tamarin rule, fact-symbol and formula references; no trace inference."""

import json
from pathlib import Path

from tree_sitter import Language, Node

from trailmark.models.nodes import Attribute, Parameter
from trailmark.parsers._common import node_text
from trailmark.parsers._formal import FormalParser, Namespace, Source, walk
from trailmark.parsers.tamarin._linking import link_tamarin
from trailmark.parsers.tamarin._symbols import conditional, fact_name, reference, scopes
from trailmark.parsers.tamarin._symbols import symbol as _symbol
from trailmark.tree_sitter_custom._native import bundled_language

_OPAQUE = frozenset(
    {"proof_skeleton", "tactic", "variants", "formal_comment", "unsupported_directive"}
)
_SKIP = _OPAQUE | frozenset({"comment", "multiline_comment", "lemma_attrs", "rule_attrs"})
_ROLES = {
    "simple_rule": "rule",
    "lemma": "lemma",
    "restriction": "restriction",
    "equation": "equation",
    "macro": "macro",
    "predicate": "predicate",
}
# https://tamarin-prover.com/manual/master/book/004_cryptographic-messages.html
_DH = {"inv": 1, "1": 0, "DH_neutral": 0, "^": 2, "*": 2}
_BUILTINS = {
    "hashing": {"h": 1},
    "symmetric-encryption": {"senc": 2, "sdec": 2},
    "asymmetric-encryption": {"aenc": 2, "adec": 2, "pk": 1},
    "signing": {"sign": 2, "verify": 3, "pk": 1, "true": 0},
    "revealing-signing": {"revealSign": 2, "revealVerify": 3, "getMessage": 1, "pk": 1, "true": 0},
    "diffie-hellman": _DH,
    "bilinear-pairing": {**_DH, "pmult": 2, "em": 2},
    "xor": {"XOR": 2, "⊕": 2, "zero": 0},
    "multiset": {"+": 2, "++": 2},
    "natural-numbers": {"%+": 2, "%1": 0},
    "dest-pairing": {"pair": 2, "fst": 1, "snd": 1},
}

for _variant in ("signing", "symmetric-encryption", "asymmetric-encryption"):
    _BUILTINS["dest-" + _variant] = _BUILTINS[_variant]


class TamarinParser(FormalParser):
    """Retain symbolic dependencies with distinct fact and declaration namespaces."""

    language = "tamarin"
    extensions = (".spthy",)
    link = staticmethod(link_tamarin)

    def load_language(self) -> Language:
        return Language(bundled_language("tamarin"))

    def extract(self, root: Node, source: Source) -> None:
        theory = next(
            (c for c in root.named_children if c.type == "security_protocol_theory"), None
        )
        theory = theory or root
        name = _symbol(theory) if theory != root else None
        env = Namespace(source, node_text(name) if name else Path(source.path).stem)
        for role in ("function", "predicate"):
            source.reexports(f"{env.name}.{role}", (role,))
        source.module_name(env.name)
        facts: set[str] = set()
        owners: list[tuple[Node, str]] = []
        builtins = {"pair": 2, "fst": 1, "snd": 1}
        explicit: set[str] = set()
        for node in walk(theory, _SKIP, include_skipped=True):
            self._collect(node, env, facts=facts, owners=owners)
            if node.type == "function_sym" and (symbol := _symbol(node)):
                explicit.add(node_text(symbol))
            elif node.type == "built_in":
                builtins.update(_BUILTINS.get(node_text(node), {}))
        for symbol, arity in builtins.items():
            if symbol not in explicit:
                self._function(theory, env, name=symbol, arity=arity, builtin=True)
        for node, owner in owners:
            source.references(
                owner,
                node.child_by_field_name("body") or node,
                frozenset({"ident", "builtin_facts", "builtin_operator"}),
                namespace=env.name,
                skip=_SKIP,
                scopes=scopes(node, _SKIP),
                context="specification",
                spelling=lambda n: reference(n, env.name),
            )

    def _collect(
        self, node: Node, env: Namespace, *, facts: set[str], owners: list[tuple[Node, str]]
    ) -> None:
        if node.type == "include_directive":
            path = node.child_by_field_name("path")
            if path is not None:
                imported = node_text(path)[1:-1]
                env.source.import_(node, imported, relative_path=imported)
        elif node.type == "fact":
            self._fact(node, env, facts)
        elif node.type == "function_sym":
            name = _symbol(node)
            arity = next((c for c in node.named_children if c.type == "arity"), None)
            if name and arity:
                self._function(node, env, name=node_text(name), arity=int(node_text(arity)))
        elif node.type in _ROLES:
            self._owner(node, env, owners)
        elif node.type in _OPAQUE:
            env.source.unsupported(
                node,
                "Tamarin SAPIC processes, proof search, "
                "variants and embedded text are not expanded",
            )

    @staticmethod
    def _fact(node: Node, env: Namespace, facts: set[str]) -> None:
        name = fact_name(node)
        if name not in facts:
            facts.add(name)
            env.source.declare(
                node,
                name,
                "fact_symbol",
                namespace=f"{env.name}.fact",
                attributes=(
                    ("executable", False),
                    ("implicit", True),
                    ("persistent", any(c.type == "!" for c in node.children)),
                    ("conditional", conditional(node)),
                ),
            )

    @staticmethod
    def _owner(node: Node, env: Namespace, owners: list[tuple[Node, str]]) -> None:
        role, name = _ROLES[node.type], _symbol(node)
        attrs: tuple[Attribute, ...] = (
            ("executable", False),
            ("assumed", role == "restriction"),
            ("diff", any(c.type == "diffLemma" for c in node.children)),
        )
        if name and role in {"macro", "predicate"}:
            space = "function" if role == "macro" else role
            attrs += (("qualified_aliases", json.dumps([f"{space}.{node_text(name)}"])),)
        if conditional(node):
            attrs += (("conditional", True),)
        owner = env.source.declare(
            node,
            node_text(name) if name else f"{role}@{node.start_point.row + 1}",
            role,
            namespace=f"{env.name}.{role}",
            parameters=tuple(
                Parameter(node_text(n)) for n in node.children_by_field_name("parameter")
            ),
            attributes=attrs,
        )
        owners.append((node, owner))

    @staticmethod
    def _function(
        node: Node, env: Namespace, *, name: str, arity: int, builtin: bool = False
    ) -> None:
        env.source.declare(
            node,
            name,
            "function",
            namespace=f"{env.name}.function",
            attributes=(
                ("executable", False),
                ("arity", arity),
                ("builtin", builtin),
                ("implicit", builtin),
                ("attacker_private", any(c.type == "private" for c in node.children)),
                (
                    "qualified_aliases",
                    json.dumps([f"function.{name}", *([name] if arity == 0 else [])]),
                ),
                *((("conditional", True),) if conditional(node) else ()),
            ),
        )
