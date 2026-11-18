"""Rocq/Coq source declarations and Gallina references, without elaboration."""

from dataclasses import dataclass, replace

from tree_sitter import Language, Node

from trailmark.models.nodes import NodeKind, Parameter, TypeRef
from trailmark.parsers._common import node_text
from trailmark.parsers._formal import FormalParser, Namespace, Scope, Source, walk
from trailmark.parsers.rocq._loadpath import logical_module
from trailmark.tree_sitter_custom._native import bundled_language

_NAMES = frozenset({"ident", "dotted_qualid"})
_OPAQUE = frozenset({"match_expression", "notation_declaration", "ltac_definition"})
_SKIP = _OPAQUE | frozenset({"comment", "string", "string_literal", "ident_decl", "attributes"})
_PROOF = frozenset(
    {"proof_command", "tactic_invocation", "tactic_sequence", "proof_block", "bullet"}
)
_END_PROOF = frozenset({"qed_command", "defined_command", "admitted_command", "abort_command"})
_ROLES = {
    "definition_command": "definition",
    "theorem_command": "theorem",
    "fixpoint_command": "fixpoint",
}
_IGNORED = frozenset(
    {"comment", "open_scope_command", "close_scope_command", "arguments_command", "setting_command"}
)


def _binder_names(node: Node) -> tuple[Node, ...]:
    implicit = next((c for c in node.named_children if c.type == "implicit_binders"), None)
    if implicit:
        node = implicit
    result = []
    for child in node.children:
        if child.type in {":", ":=", "|"}:
            break
        if child.type in _NAMES:
            result.append(child)
    return tuple(result)


def _parameters(regions: tuple[Node, ...]) -> tuple[Parameter, ...]:
    result = []
    for node in regions:
        if node.type != "binder":
            continue
        typed = next((c for c in walk(node) if c.type == "type"), None)
        result.extend(
            Parameter(node_text(n), TypeRef(node_text(typed)) if typed else None)
            for n in _binder_names(node)
        )
    return tuple(result)


def _scopes(regions: tuple[Node, ...]) -> tuple[Scope, ...]:
    result = []
    end = max((n.end_byte for n in regions), default=0)
    for region in regions:
        for node in walk(region, _SKIP):
            if node.type == "binder":
                names = _binder_names(node)
                parent = node.parent
                if parent and parent.type == "open_binders":
                    parent = parent.parent
                stop = min(parent.end_byte, end) if parent else end
                result.append(Scope(frozenset(node_text(n) for n in names), node.end_byte, stop))
                result.extend(
                    Scope(frozenset({node_text(n)}), n.start_byte, n.end_byte) for n in names
                )
            elif node.type == "let_expression":
                result.extend(_let_scopes(node))
    return tuple(result)


def _let_scopes(node: Node) -> list[Scope]:
    body = node.child_by_field_name("body")
    pattern = node.child_by_field_name("pattern") or node.child_by_field_name("name")
    if body is None or pattern is None:
        return []
    names = frozenset(node_text(n) for n in walk(pattern) if n.type in _NAMES)
    return [
        Scope(names, body.start_byte, body.end_byte),
        Scope(names, pattern.start_byte, pattern.end_byte),
    ]


def _spelling(node: Node) -> str | None:
    parent = node.parent
    if parent and node in parent.children_by_field_name("argument_label"):
        return None
    return node_text(node)


@dataclass(frozen=True)
class _Context:
    env: Namespace
    bound: frozenset[str] = frozenset()
    section: bool = False


class RocqParser(FormalParser):
    """Keep Gallina dependencies with source-local module and binder scopes."""

    language = "rocq"
    extensions = (".v",)

    def load_language(self) -> Language:
        return Language(bundled_language("rocq"))

    def extract(self, root: Node, source: Source) -> None:
        source.ordered_declarations()
        namespace = logical_module(source.path, source.module)
        source.module_name(namespace)
        context = _Context(Namespace(source, namespace))
        stack: list[_Context] = []
        proof = False
        for sentence in root.named_children:
            commands = [
                c for c in sentence.named_children if c.type not in {"attributes", "comment"}
            ]
            if any(c.type == "fail" for c in commands):
                source.unsupported(sentence, "Rocq Fail commands do not establish declarations")
                continue
            for node in commands:
                if node.type in _PROOF:
                    if not proof:
                        source.unsupported(
                            node, "Rocq tactics are not elaborated into proof dependencies"
                        )
                    proof = True
                elif node.type in _END_PROOF:
                    proof = False
                else:
                    context = self._command(node, context, stack)

    def _command(self, node: Node, context: _Context, stack: list[_Context]) -> _Context:
        if node.type in {"module_command", "section_command"}:
            return self._enter(node, context, stack)
        if node.type == "end_command":
            return stack.pop() if stack else context
        if node.type in {"require_command", "import_command", "export_command"}:
            return replace(context, env=self._imports(node, context.env))
        if node.type == "assumption_command":
            return self._assumptions(node, context)
        if node.type in _ROLES:
            self._definitions(node, context)
        elif node.type == "inductive_command":
            self._inductives(node, context)
        elif node.type not in _IGNORED:
            context.env.source.unsupported(node, f"Rocq {node.type} is not modeled")
        return context

    @staticmethod
    def _enter(node: Node, context: _Context, stack: list[_Context]) -> _Context:
        env = context.env
        name = node.child_by_field_name("name") or next(
            (n for n in node.named_children if n.type == "ident"), None
        )
        if name is None:
            return context
        value = node.child_by_field_name("value")
        if value is not None:
            target = node_text(value)
            local = f"{env.name}.{target}"
            qualified = local if local in env.source.modules else env.aliases.get(target, target)
            env.source.import_(node, node_text(value))
            aliases = {**env.aliases, node_text(name): qualified}
            opens = (
                (*env.opens, qualified)
                if any(c.type in {"Import", "Export"} for c in node.children)
                else env.opens
            )
            return replace(context, env=replace(env, aliases=aliases, opens=opens))
        stack.append(context)
        if node.type == "section_command":
            return replace(context, section=True)
        owner = env.source.declare(
            node,
            node_text(name),
            "module",
            namespace=env.name,
            parent=env.parent,
            kind=NodeKind.NAMESPACE,
        )
        child = env.child(node_text(name), owner)
        if any(c.type in {"Import", "Export"} for c in node.children):
            stack[-1] = replace(context, env=replace(env, opens=(*env.opens, child.name)))
        env.source.module_name(child.name)
        return replace(context, env=child, section=False)

    @staticmethod
    def _imports(node: Node, env: Namespace) -> Namespace:
        prefix = node.child_by_field_name("dirpath")
        opens = list(env.opens)
        for imported in (n for n in node.named_children if n.type == "filtered_import"):
            if not imported.named_children:
                continue
            name = node_text(imported.named_children[0])
            qualified = f"{node_text(prefix)}.{name}" if prefix else name
            env.source.import_(imported, qualified)
            if node.type != "require_command" or any(
                c.type in {"Import", "Export"} for c in node.children
            ):
                opens.extend((qualified, f"{env.name}.{qualified}"))
        return replace(env, opens=tuple(opens))

    def _assumptions(self, node: Node, context: _Context) -> _Context:
        names = [n for n in walk(node) if n.type == "ident_decl"]
        local = context.section and node.children[0].type in {
            "Variable",
            "Variables",
            "Hypothesis",
            "Hypotheses",
        }
        if local:
            return replace(context, bound=context.bound | frozenset(node_text(n) for n in names))
        for name in names:
            self._declaration(
                node, name, context, role="assumption", regions=tuple(node.named_children)
            )
        return context

    def _definitions(self, node: Node, context: _Context) -> None:
        names = [n for n in node.named_children if n.type == "ident_decl"]
        for index, name in enumerate(names):
            end = names[index + 1].start_byte if index + 1 < len(names) else node.end_byte
            regions = tuple(n for n in node.named_children if name.end_byte <= n.start_byte < end)
            self._declaration(node, name, context, role=_ROLES[node.type], regions=regions)

    def _inductives(self, node: Node, context: _Context) -> None:
        for decl in node.named_children:
            name = decl.child_by_field_name("name")
            if name is None:
                continue
            regions = tuple(
                n for n in decl.named_children if n != name and n.type != "constructor_block"
            )
            owner = self._declaration(decl, name, context, role="inductive", regions=regions)
            bound = context.bound | frozenset(p.name for p in _parameters(regions))
            nested = replace(context, bound=bound, env=replace(context.env, parent=owner))
            for constructor in (n for n in walk(decl) if n.type == "constructor"):
                name = constructor.child_by_field_name("name")
                if name:
                    self._declaration(
                        constructor,
                        name,
                        nested,
                        role="constructor",
                        regions=tuple(n for n in constructor.named_children if n != name),
                    )

    @staticmethod
    def _declaration(
        node: Node, name: Node, context: _Context, *, role: str, regions: tuple[Node, ...]
    ) -> str:
        env = context.env
        owner = env.source.declare(
            node,
            node_text(name),
            role,
            namespace=env.name,
            parent=env.parent,
            kind=NodeKind.ENUM if role == "inductive" else NodeKind.FUNCTION,
            parameters=_parameters(regions),
            attributes=(("executable", False), ("assumed", role == "assumption")),
        )
        scopes = (*_scopes(regions), Scope(context.bound, node.start_byte, node.end_byte))
        for region in regions:
            for child in walk(region, _OPAQUE, include_skipped=True):
                if child.type in _OPAQUE:
                    env.source.unsupported(
                        child, "Rocq match binders or notation require elaboration and are omitted"
                    )
            env.source.references(
                owner,
                region,
                _NAMES,
                namespace=env.name,
                opens=env.opens,
                aliases=env.aliases,
                scopes=scopes,
                skip=_SKIP,
                context="type" if region.type in {"type", "of_type", "binder"} else "definition",
                spelling=_spelling,
            )
        return owner
