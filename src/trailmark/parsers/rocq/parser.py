"""Rocq/Coq source declarations and Gallina references, without elaboration."""

from dataclasses import dataclass, replace

from tree_sitter import Language, Node

from trailmark.models.nodes import NodeKind, Parameter, TypeRef
from trailmark.parsers._common import node_text
from trailmark.parsers._formal import FormalParser, Namespace, Scope, Source, walk
from trailmark.parsers.rocq._loadpath import logical_module
from trailmark.parsers.rocq._recovery import sentences
from trailmark.tree_sitter_custom._native import bundled_language

_NAMES = frozenset({"ident", "dotted_qualid"})
_OPAQUE = frozenset({"notation_declaration", "ltac_definition"})
_SKIP = _OPAQUE | frozenset({"comment", "string", "string_literal", "ident_decl", "attributes"})
_PROOF = frozenset(
    {
        "proof_command",
        "tactic_invocation",
        "tactic_sequence",
        "tactic_branch",
        "proof_block",
        "bullet",
    }
)
_END_PROOF = frozenset({"qed_command", "defined_command", "admitted_command", "abort_command"})
_ROLES = {
    "definition_command": "definition",
    "theorem_command": "theorem",
    "fixpoint_command": "fixpoint",
}
_IGNORED = frozenset(
    {
        "comment",
        "open_scope_command",
        "close_scope_command",
        "arguments_command",
        "set_command",
        "unset_command",
    }
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


def _scopes(
    regions: tuple[Node, ...], constructors: frozenset[str] = frozenset()
) -> tuple[Scope, ...]:
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
            elif node.type in {"match_case", "brace_term", "fix_expression"}:
                result.extend(_nested_scopes(node, constructors))
    return tuple(result)


def _nested_scopes(node: Node, constructors: frozenset[str]) -> list[Scope]:
    if node.type == "match_case":
        patterns = [n for n in walk(node) if n.type == "pattern_option"]
        names = {
            node_text(n)
            for pattern in patterns
            for n in walk(pattern)
            if n.type in _NAMES
            and node_text(n) not in constructors
            and not (
                n.parent
                and n.parent.type == "pattern_application"
                and n == n.parent.named_children[0]
            )
        }
        return [Scope(frozenset(names), node.start_byte, node.end_byte)]
    names = frozenset(
        node_text(n) for part in walk(node) for n in part.children_by_field_name("name")
    )
    return [Scope(names, node.start_byte, node.end_byte)]


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
        warned = False
        for sentence in sentences(root, self._parser):
            commands = [
                c for c in sentence.named_children if c.type not in {"attributes", "comment"}
            ]
            if any(c.type == "fail" for c in commands):
                source.unsupported(sentence, "Rocq Fail commands do not establish declarations")
                continue
            for node in commands:
                if node.type in _PROOF:
                    if not warned:
                        source.unsupported(
                            node, "Rocq tactics are not elaborated into proof dependencies"
                        )
                    warned = True
                elif node.type in _END_PROOF:
                    continue
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
        if node.type in _ROLES or node.type in {
            "record_command",
            "context_command",
            "hint_command",
            "inductive_command",
        }:
            self._evidence(node, context)
        elif node.type not in _IGNORED:
            context.env.source.unsupported(node, f"Rocq {node.type} is not modeled")
        return context

    def _evidence(self, node: Node, context: _Context) -> None:
        if node.type in _ROLES:
            self._definitions(node, context)
        elif node.type == "record_command":
            self._record(node, context)
        elif node.type == "context_command":
            for binder in node.named_children:
                for name in _binder_names(binder):
                    self._declaration(
                        binder,
                        name,
                        context,
                        role="assumption",
                        regions=tuple(binder.named_children),
                    )
        elif node.type == "hint_command":
            context.env.source.references(
                context.env.parent or context.env.source.module,
                node,
                _NAMES,
                namespace=context.env.name,
                opens=context.env.opens,
                context="proof",
            )
        elif node.type == "inductive_command":
            self._inductives(node, context)

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
            env.source.import_(node, node_text(value), parent=env.parent)
            if any(c.type == "Export" for c in node.children):
                env.source.reexports(env.name, (qualified,), qualified=False)
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
        for signature in node.children_by_field_name("signature"):
            env.source.references(
                owner,
                signature,
                _NAMES,
                namespace=env.name,
                opens=env.opens,
                aliases=env.aliases,
                context="module",
            )
        if any(c.type in {"Import", "Export"} for c in node.children):
            stack[-1] = replace(context, env=replace(env, opens=(*env.opens, child.name)))
        if any(c.type == "Export" for c in node.children):
            env.source.reexports(env.name, (child.name,), qualified=False)
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
            enclosing = env.name
            local = f"{enclosing}.{qualified}"
            while enclosing and local not in env.source.modules:
                enclosing = enclosing.rpartition(".")[0]
                local = f"{enclosing}.{qualified}" if enclosing else qualified
            canonical = (
                local if local in env.source.modules else env.aliases.get(qualified, qualified)
            )
            env.source.import_(imported, canonical, parent=env.parent)
            if any(c.type == "import_categories" for c in node.named_children) or any(
                c.type == "filtered_import_item" for c in imported.named_children
            ):
                env.source.unsupported(imported, "Rocq selective imports are not modeled")
                continue
            if any(c.type == "Export" for c in node.children):
                env.source.reexports(env.name, (canonical,), qualified=False)
            if node.type != "require_command" or any(
                c.type in {"Import", "Export"} for c in node.children
            ):
                opens.extend((canonical, local))
        return replace(env, opens=tuple(opens))

    def _assumptions(self, node: Node, context: _Context) -> _Context:
        names = [n for n in walk(node) if n.type == "ident_decl"]
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

    def _record(self, node: Node, context: _Context) -> None:
        name = node.child_by_field_name("name")
        if name is None:
            return
        regions = tuple(n for n in node.named_children if n != name and n.type != "record_field")
        owner = self._declaration(node, name, context, role="record", regions=regions)
        nested = replace(context, env=replace(context.env, parent=owner))
        for field in node.named_children:
            declared = field.child_by_field_name("name")
            if field.type == "record_field" and declared is not None:
                self._declaration(
                    field,
                    declared,
                    nested,
                    role="field",
                    regions=tuple(n for n in field.named_children if n != declared),
                )

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
            kind=NodeKind.ENUM
            if role == "inductive"
            else NodeKind.STRUCT
            if role == "record"
            else NodeKind.FUNCTION,
            parameters=_parameters(regions),
            attributes=(("executable", False), ("assumed", role == "assumption")),
        )
        constructors = frozenset(
            n.name
            for n in env.source.graph.nodes.values()
            if dict(n.attributes).get("declaration_role") == "constructor"
        )
        scopes = (
            *_scopes(regions, constructors),
            Scope(context.bound, node.start_byte, node.end_byte),
        )
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
