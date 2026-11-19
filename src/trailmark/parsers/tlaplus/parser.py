"""TLA+ declaration and specification dependencies, without model checking."""

from dataclasses import replace

from tree_sitter import Language, Node
from tree_sitter_language_pack import get_language

from trailmark.models.nodes import NodeKind, Parameter
from trailmark.parsers._common import node_text
from trailmark.parsers._formal import FormalParser, Namespace, Scope, Source, walk

_OPAQUE = frozenset({"terminal_proof", "non_terminal_proof"})
_SKIP = _OPAQUE | frozenset({"comment", "block_comment", "string"})
_DECLARATIONS = {
    "operator_definition": "operator",
    "function_definition": "function",
    "theorem": "theorem",
    "assumption": "assumption",
}
_IGNORED = frozenset(
    {
        "header_line",
        "single_line",
        "double_line",
        "identifier",
        "comment",
        "block_comment",
        "extends",
    }
)


def _spelling(node: Node) -> str | None:
    parent = node.parent
    if parent is not None and (
        parent.type == "except_update_record_field"
        or (parent.type == "record_value" and node == parent.named_children[-1])
        or (
            parent.type in {"record_field", "record_set_field"} and node == parent.named_children[0]
        )
    ):
        return None
    return node_text(node)


def _names(node: Node) -> frozenset[str]:
    return frozenset(node_text(n) for n in walk(node) if n.type == "identifier")


def _bound(node: Node) -> list[Scope]:
    names = frozenset(name for i in node.children_by_field_name("intro") for name in _names(i))
    parent = node.parent if node.type == "quantifier_bound" else node
    domain = node.child_by_field_name("set")
    if parent is None:
        return []
    result = [Scope(names, domain.end_byte if domain else node.start_byte, parent.end_byte)]
    if parent.type == "set_map":
        body = parent.child_by_field_name("map")
        if body:
            result.append(Scope(names, body.start_byte, body.end_byte))
    return result


def _scopes(root: Node) -> tuple[Scope, ...]:
    result = []
    for node in walk(root, _SKIP):
        if node.type in {"quantifier_bound", "choose", "unbounded_quantification"}:
            result.extend(_bound(node))
        elif node.type in {"operator_definition", "function_definition", "lambda"}:
            parameters = (
                node.children_by_field_name("parameter")
                if node.type != "lambda"
                else [n for n in node.named_children if n.type == "identifier"]
            )
            names = frozenset(name for p in parameters for name in _names(p))
            result.append(Scope(names, node.start_byte, node.end_byte))
        elif node.type == "let_in":
            names = frozenset(
                node_text(name)
                for child in node.named_children
                if child.type in _DECLARATIONS
                if (name := child.child_by_field_name("name")) is not None
            )
            result.append(Scope(names, node.start_byte, node.end_byte))
    return tuple(result)


def _parameters(node: Node) -> tuple[Parameter, ...]:
    parameters = tuple(
        Parameter(node_text(p.child_by_field_name("name") or p))
        for p in node.children_by_field_name("parameter")
        if p.is_named
    )
    if node.type == "function_definition":
        parameters = tuple(
            Parameter(node_text(name))
            for bound in node.named_children
            if bound.type == "quantifier_bound"
            for intro in bound.children_by_field_name("intro")
            for name in walk(intro)
            if name.type == "identifier"
        )
    return parameters


def _prefixed(node: Node, env: Namespace) -> dict[int, str]:
    prefixed = {}
    for part in walk(node, _SKIP):
        if part.type != "prefixed_op":
            continue
        prefix, op = part.child_by_field_name("prefix"), part.child_by_field_name("op")
        if prefix is None or op is None:
            continue
        head = op.child_by_field_name("name") or op
        instance = node_text(prefix).rstrip("!")
        if instance in env.aliases:
            prefixed[head.start_byte] = f"{env.aliases[instance]}.{node_text(head)}"
    return prefixed


class TLAPlusParser(FormalParser):
    """Retain specification dependencies with lexical and EXTENDS visibility."""

    language = "tlaplus"
    extensions = (".tla",)

    def load_language(self) -> Language:
        return get_language("tlaplus")

    def extract(self, root: Node, source: Source) -> None:
        source.ordered_declarations()
        covered_until = -1
        for node in walk(root, _OPAQUE, include_skipped=True):
            if node.start_byte < covered_until:
                continue
            if node.type.startswith("pcal_") or (
                node.type == "block_comment"
                and "--algorithm" in node_text(node).replace("--fair algorithm", "--algorithm")
            ):
                source.unsupported(node, "PlusCal algorithms are not translated")
                covered_until = node.end_byte
        for module in root.named_children:
            if module.type == "module":
                self._module(module, Namespace(source))

    def _module(self, module: Node, env: Namespace) -> None:
        name = module.child_by_field_name("name")
        if name is None:
            return
        namespace = f"{env.name}.{node_text(name)}" if env.name else node_text(name)
        own_opens = tuple(
            node_text(n)
            for c in module.named_children
            if c.type == "extends"
            for n in c.named_children
            if n.type == "identifier_ref"
        )
        env = replace(env, name=namespace, opens=(*env.opens, *own_opens))
        env.source.module_name(namespace)
        env.source.reexports(namespace, own_opens)
        instances = {}
        for child in module.named_children:
            if child.type == "module_definition":
                instance = child.child_by_field_name("definition")
                name = child.child_by_field_name("name")
                if instance and name and instance.named_children:
                    instances[node_text(name)] = node_text(instance.named_children[0])
        env = replace(env, aliases={**env.aliases, **instances})
        for node in module.named_children:
            self._member(node, env)
            inner = (
                node.named_children[0]
                if node.type == "local_definition" and node.named_children
                else node
            )
            if inner.type == "instance" and inner.named_children:
                target = node_text(inner.named_children[0])
                env = replace(env, opens=(*env.opens, target))
                if node.type != "local_definition":
                    env.source.reexports(env.name, (target,))

    def _member(self, node: Node, env: Namespace) -> None:
        handlers = {
            "extends": self._imports,
            "module": self._nested,
            "module_definition": self._instance,
            "instance": self._instance,
            **dict.fromkeys(
                ("variable_declaration", "constant_declaration", "recursive_declaration"),
                self._symbols,
            ),
        }
        if node.type in handlers:
            handlers[node.type](node, env)
        elif node.type in _DECLARATIONS:
            self._declaration(node, env)
        elif node.type == "local_definition" and node.named_children:
            inner = node.named_children[0]
            if inner.type in _DECLARATIONS:
                self._declaration(inner, env, private=True)
            else:
                self._member(inner, env)
        elif node.type not in _IGNORED:
            env.source.unsupported(node, f"TLA+ {node.type} is not modeled")

    @staticmethod
    def _imports(node: Node, env: Namespace) -> None:
        for imported in node.named_children:
            if imported.type == "identifier_ref":
                env.source.import_(imported, node_text(imported))

    def _nested(self, node: Node, env: Namespace) -> None:
        name = node.child_by_field_name("name")
        if name:
            owner = env.source.declare(
                node,
                node_text(name),
                "module",
                namespace=env.name,
                parent=env.parent,
                kind=NodeKind.NAMESPACE,
            )
            self._module(node, replace(env, parent=owner))

    @staticmethod
    def _instance(node: Node, env: Namespace) -> None:
        instance = (
            node.child_by_field_name("definition") if node.type == "module_definition" else node
        )
        if instance is None or not instance.named_children:
            return
        target = node_text(instance.named_children[0])
        env.source.import_(instance, target)
        name = node.child_by_field_name("name")
        owner = env.parent or env.source.module
        if name is not None:
            owner = env.source.declare(
                node,
                node_text(name),
                "instance",
                namespace=env.name,
                parent=env.parent,
                kind=NodeKind.NAMESPACE,
                attributes=(("instance_of", target), ("executable", False)),
            )
        for substitution in instance.named_children:
            if substitution.type == "substitution" and substitution.named_children:
                env.source.references(
                    owner,
                    substitution.named_children[-1],
                    frozenset({"identifier_ref"}),
                    namespace=env.name,
                    opens=env.opens,
                    context="specification",
                )
                env.source.unsupported(
                    substitution, "TLA+ WITH members reference the uninstantiated template"
                )

    @staticmethod
    def _symbols(node: Node, env: Namespace) -> None:
        recursive = node.type == "recursive_declaration"
        role = (
            "operator"
            if recursive
            else "variable"
            if node.type == "variable_declaration"
            else "constant"
        )
        for declared in node.named_children:
            if declared.type in {"identifier", "operator_declaration"}:
                ident = declared.child_by_field_name("name") or declared
                env.source.declare(
                    declared,
                    node_text(ident),
                    role,
                    namespace=env.name,
                    parent=env.parent,
                    kind=NodeKind.FUNCTION if recursive else NodeKind.STATE_VARIABLE,
                    attributes=(("executable", False), ("signature", recursive)),
                )

    @staticmethod
    def _declaration(node: Node, env: Namespace, *, private: bool = False) -> None:
        name = node.child_by_field_name("name")
        role = _DECLARATIONS[node.type]
        ident = node_text(name) if name else f"{role}@{node.start_point.row + 1}"
        parameters = _parameters(node)
        owner = env.source.declare(
            node,
            ident,
            role,
            namespace=env.name,
            parent=env.parent,
            parameters=parameters,
            attributes=(("executable", False), *((("private", True),) if private else ())),
        )
        for child in walk(node, _OPAQUE, include_skipped=True):
            if child.type in _OPAQUE:
                env.source.unsupported(
                    child, "TLA+ proof steps and prefixed subexpressions are omitted"
                )
        prefixed = _prefixed(node, env)
        scopes = _scopes(node)
        for body in node.named_children:
            if body != name and body not in node.children_by_field_name("parameter"):
                env.source.references(
                    owner,
                    body,
                    frozenset({"identifier_ref"}),
                    namespace=env.name,
                    opens=env.opens,
                    scopes=scopes,
                    skip=_SKIP,
                    context="specification",
                    spelling=lambda n: prefixed.get(n.start_byte, _spelling(n)),
                )
