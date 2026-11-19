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


_SYMBOLIC_ROLES = frozenset(
    {
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
    }
)


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
                    node, "ProVerif Horn clauses or ignored CryptoVerif declarations are omitted"
                )

    def _wrapper(
        self,
        wrapper: Node,
        source: Source,
        *,
        namespace: str = "",
        parent: str | None = None,
        outer: tuple[Scope, ...] = (),
    ) -> None:
        special = next(
            (n for n in wrapper.named_children if n.type in {"decl_define", "decl_expand"}), None
        )
        if special is not None:
            self._macro(special, source, namespace=namespace, parent=parent, outer=outer)
            return
        node = next((c for c in wrapper.named_children if c.type in _ROLES), wrapper)
        role = _ROLES.get(node.type, wrapper.children[0].type)
        if role in {"set", "param", "clauses"} or any(
            c.type in OPAQUE for c in wrapper.named_children
        ):
            return
        if node.type not in _ROLES and role not in _SYMBOLIC_ROLES:
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
        bindings = (*outer, *scopes(node))
        if role == "reduc":
            self._destructors(node, source, bindings, namespace=namespace, parent=parent)
            return
        for declared in names or [None]:
            self._declaration(
                node,
                node_text(declared)
                if declared is not None
                else f"{role}@{node.start_point.row + 1}",
                role,
                source,
                declared=declared,
                bindings=bindings,
                namespace=namespace,
                parent=parent,
            )

    def _destructors(
        self,
        node: Node,
        source: Source,
        bindings: tuple[Scope, ...],
        *,
        namespace: str,
        parent: str | None,
    ) -> None:
        for equation in walk(node):
            if equation.type == "equality":
                head = next(
                    (
                        n.child_by_field_name("name")
                        for n in walk(equation.named_children[0])
                        if n.type == "term_function_call"
                    ),
                    None,
                )
                if head is not None:
                    self._declaration(
                        equation,
                        node_text(head),
                        "destructor",
                        source,
                        declared=head,
                        bindings=bindings,
                        namespace=namespace,
                        parent=parent,
                    )

    def _macro(
        self,
        node: Node,
        source: Source,
        *,
        namespace: str,
        parent: str | None,
        outer: tuple[Scope, ...],
    ) -> None:
        name = node.child_by_field_name("name")
        if name is None:
            return
        if node.type == "decl_expand":
            source.references(
                parent or source.module,
                node,
                frozenset({"ident"}),
                namespace=namespace,
                scopes=outer,
                context="specification",
            )
            return
        parameters = tuple(
            Parameter(node_text(n)) for n in node.children_by_field_name("parameter")
        )
        owner = source.declare(
            node,
            node_text(name),
            "declaration_macro",
            namespace=namespace,
            parent=parent,
            kind=NodeKind.NAMESPACE,
            parameters=parameters,
            attributes=(("executable", False),),
        )
        bound = Scope(frozenset(p.name for p in parameters), node.start_byte, node.end_byte)
        qualified = f"{namespace}.{node_text(name)}" if namespace else node_text(name)
        for child in node.named_children:
            if child.type == "decl":
                self._wrapper(
                    child, source, namespace=qualified, parent=owner, outer=(*outer, bound)
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
        namespace: str = "",
        parent: str | None = None,
    ) -> None:
        parameters = tuple(
            Parameter(n)
            for c in node.named_children
            if c.type == "typedecl"
            for n in sorted(typed_names(c))
        )
        existing = _destructor(source, namespace, name) if role == "destructor" else None
        owner = existing or source.declare(
            node,
            name,
            role,
            parameters=parameters,
            kind=NodeKind.STRUCT if role == "type" else NodeKind.FUNCTION,
            attributes=(("executable", False),),
            namespace=namespace,
            parent=parent,
        )
        bindings = scopes(node) if bindings is None else bindings
        if declared is not None:
            bindings += (
                Scope(frozenset({node_text(declared)}), declared.start_byte, declared.end_byte),
            )
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
                    namespace=namespace,
                )


def _destructor(source: Source, namespace: str, name: str) -> str | None:
    return next(
        (
            n.id
            for n in source.graph.nodes.values()
            if dict(n.attributes).get("qualified_name")
            == (f"{namespace}.{name}" if namespace else name)
        ),
        None,
    )
