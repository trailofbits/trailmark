"""Tamarin rule, fact-symbol and formula references; no trace inference."""

from tree_sitter import Language, Node

from trailmark.parsers._common import node_text
from trailmark.parsers._formal import FormalParser, Namespace, Source, walk
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
    "multiset": {"+": 2},
}


def _symbol(node: Node) -> Node | None:
    return next((c for c in node.named_children if c.type in {"ident", "builtin_facts"}), None)


def _fact_name(node: Node) -> str:
    symbol = _symbol(node)
    arity = sum(c.type == "," for c in node.children) + int(
        any(c.type == "term" for c in node.named_children)
    )
    return f"{node_text(symbol) if symbol else '?'}/{arity}"


def _reference(node: Node, namespace: str) -> str | None:
    if node.type == "builtin_operator":
        return f"{namespace}.function.{node_text(node)}"
    parent = node.parent
    if parent is None or node != _symbol(parent):
        return None
    if parent.type == "fact":
        return f"{namespace}.fact.{_fact_name(parent)}"
    if parent.type == "app":
        return f"{namespace}.function.{node_text(node)}"
    return None


class TamarinParser(FormalParser):
    """Retain symbolic dependencies with distinct fact and declaration namespaces."""

    language = "tamarin"
    extensions = (".spthy",)

    def load_language(self) -> Language:
        return Language(bundled_language("tamarin"))

    def extract(self, root: Node, source: Source) -> None:
        theory = next(
            (c for c in root.named_children if c.type == "security_protocol_theory"), None
        )
        if theory is None:
            return
        name = _symbol(theory)
        env = Namespace(source, node_text(name) if name else "")
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
                builtins.update(_BUILTINS[node_text(node)])
        for symbol, arity in builtins.items():
            if symbol not in explicit:
                self._function(theory, env, name=symbol, arity=arity, builtin=True)
        for node, owner in owners:
            source.references(
                owner,
                node,
                frozenset({"ident", "builtin_facts", "builtin_operator"}),
                namespace=env.name,
                skip=_SKIP,
                context="specification",
                spelling=lambda n: _reference(n, env.name),
            )

    def _collect(
        self, node: Node, env: Namespace, *, facts: set[str], owners: list[tuple[Node, str]]
    ) -> None:
        if node.type == "fact":
            name = _fact_name(node)
            if name not in facts:
                facts.add(name)
                env.source.declare(
                    node,
                    name,
                    "fact_symbol",
                    namespace=f"{env.name}.fact",
                    attributes=(("executable", False), ("implicit", True)),
                )
        elif node.type == "function_sym":
            name = _symbol(node)
            arity = next((c for c in node.named_children if c.type == "arity"), None)
            if name and arity:
                self._function(node, env, name=node_text(name), arity=int(node_text(arity)))
        elif node.type in _ROLES:
            role, name = _ROLES[node.type], _symbol(node)
            owner = env.source.declare(
                node,
                node_text(name) if name else f"{role}@{node.start_point.row + 1}",
                role,
                namespace=f"{env.name}.{role}",
                attributes=(("executable", False), ("assumed", role == "restriction")),
            )
            owners.append((node, owner))
        elif node.type in _OPAQUE:
            env.source.unsupported(
                node,
                "Tamarin preprocessing, predicates, macros, SAPIC processes, proof search, "
                "variants and embedded text are not expanded",
            )

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
                ("private", any(c.type == "[private]" for c in node.children)),
            ),
        )
