"""Typed pi-calculus declarations and source references, without verification."""

from tree_sitter import Language, Node

from trailmark.models.nodes import NodeKind, Parameter
from trailmark.parsers._common import node_text
from trailmark.parsers._formal import FormalParser, Scope, Source, walk
from trailmark.parsers.proverif._scopes import OPAQUE, SKIP, scopes, typed_names
from trailmark.tree_sitter_custom._native import bundled_language

_ROLES = {
    "function_def": "function",
    "function_macro_def": "function_macro",
    "type_def": "type",
    "decl_event": "event",
    "decl_pred": "predicate",
    "decl_table": "table",
    "decl_process_macro": "process_macro",
    "decl_query": "query",
    "decl_free": "free",
    "decl_const": "constant",
    "decl_channel": "channel",
}


class ProVerifParser(FormalParser):
    """Retain symbolic source dependencies without executing process semantics."""

    language = "proverif"
    extensions = (".pv", ".pvl")

    def load_language(self) -> Language:
        return Language(bundled_language("proverif"))

    def extract(self, root: Node, source: Source) -> None:
        source.ordered_declarations()
        for wrapper in root.named_children:
            if wrapper.type == "decl":
                self._wrapper(wrapper, source)
            elif wrapper.type == "process":
                self._declaration(
                    wrapper, f"process@{wrapper.start_point.row + 1}", "process", source
                )
            elif wrapper.type not in SKIP and not wrapper.is_error:
                source.unsupported(wrapper, f"ProVerif {wrapper.type} is not modeled")
        for node in walk(root, SKIP, include_skipped=True):
            if node.type in OPAQUE:
                source.unsupported(
                    node, "ProVerif table binding, clauses or CryptoVerif expansion is omitted"
                )

    def _wrapper(self, wrapper: Node, source: Source) -> None:
        node = next((c for c in wrapper.named_children if c.type in _ROLES), wrapper)
        role = _ROLES.get(node.type, wrapper.children[0].type)
        if role in {"set", "param", "clauses"} or any(
            c.type in OPAQUE for c in wrapper.named_children
        ):
            return
        if node.type not in _ROLES and role not in {
            "reduc",
            "equation",
            "axiom",
            "restriction",
            "lemma",
            "weaksecret",
            "noninterf",
            "nounif",
            "select",
            "noselect",
            "not",
            "elimtrue",
        }:
            source.unsupported(node, f"ProVerif {role} declaration is not modeled")
            return
        name = node.child_by_field_name("name")
        names = (
            [name]
            if name
            else [c for c in node.named_children if c.type == "ident"]
            if role in {"free", "constant", "channel"}
            else []
        )
        bindings = scopes(node)
        if not names:
            self._declaration(
                node, f"{role}@{node.start_point.row + 1}", role, source, bindings=bindings
            )
        else:
            for declared in names:
                self._declaration(
                    node, node_text(declared), role, source, declared=declared, bindings=bindings
                )

    def _declaration(
        self,
        node: Node,
        name: str,
        role: str,
        source: Source,
        *,
        declared: Node | None = None,
        bindings: tuple[Scope, ...] | None = None,
    ) -> None:
        parameters = tuple(
            Parameter(n)
            for c in node.named_children
            if c.type == "typedecl"
            for n in sorted(typed_names(c))
        )
        owner = source.declare(
            node,
            name,
            role,
            parameters=parameters,
            kind=NodeKind.STRUCT if role == "type" else NodeKind.FUNCTION,
            attributes=(("executable", False),),
        )
        bindings = scopes(node) if bindings is None else bindings
        for child in node.named_children:
            if child != declared and not (
                role in {"free", "constant", "channel"} and child.type == "ident"
            ):
                source.references(
                    owner,
                    child,
                    frozenset({"ident"}),
                    scopes=bindings,
                    skip=SKIP,
                    context="specification",
                )
