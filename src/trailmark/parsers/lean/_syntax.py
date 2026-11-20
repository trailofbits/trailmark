"""Lean syntax helpers and traversal state; no elaboration or name guessing."""

from __future__ import annotations

from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field

from tree_sitter import Node

from trailmark.models._parsing import Lookup
from trailmark.models.nodes import Attribute, BranchInfo, NodeKind, Parameter, TypeRef
from trailmark.parsers._common import node_text
from trailmark.parsers._simple import first_child_type

DECLARATIONS = frozenset(
    {"def", "abbrev", "opaque", "theorem", "axiom", "constant", "instance", "example"}
)
TYPES = {"structure": NodeKind.STRUCT, "inductive": NodeKind.ENUM}
TRIVIA = frozenset({"line_comment", "block_comment", "doc_comment", "module_doc_comment"})
SKIP_TERMS = TRIVIA | {
    "by",
    "quotation",
    "antiquot",
    "name_lit",
    "dot_ident",
    "pct_macro",
    "type_const",
    "sort_const",
}


@dataclass(frozen=True)
class Binding:
    target: str | None = None
    type_candidates: Lookup | None = None


@dataclass(frozen=True)
class Context:
    owner: str
    bindings: dict[str, Binding] = dataclass_field(default_factory=dict)
    branches: list[BranchInfo] = dataclass_field(default_factory=list)
    reference_context: str = "body"
    calls: bool = True
    type_uses: bool = False

    def fork(self) -> Context:
        return replace(self, bindings=self.bindings.copy())


@dataclass(frozen=True)
class Declaration:
    parent: str = ""
    attributes: tuple[Attribute, ...] = ()
    doc: str | None = None


@dataclass(frozen=True)
class Task:
    node: Node
    context: Context
    action: str = "term"
    declaration: Declaration = Declaration()


def fields(node: Node, name: str) -> list[Node]:
    # Fields may include punctuation from grammar sequences (`: type`, `:= body`).
    return [child for child in node.children_by_field_name(name) if child.is_named]


def field(node: Node, name: str) -> Node | None:
    return next(iter(fields(node, name)), None)


def binding_names(node: Node) -> list[str]:
    names = []
    pending = [node]
    while pending:
        child = pending.pop()
        if child.type in {"identifier", "hole"}:
            names.append(node_text(child))
        else:
            pending.extend(reversed(child.named_children))
    return names


def binder_names(binder: Node) -> list[str]:
    if binder.type in {"identifier", "hole", "tuple_binder", "anon_ctor_binder"}:
        return binding_names(binder)
    return [name for child in fields(binder, "name") for name in binding_names(child)]


def binder_items(node: Node) -> list[Node]:
    binders = first_child_type(node, {"binders"})
    if binders is not None:
        return binders.named_children
    return [child for child in node.named_children if child.type.endswith("_binder")]


def parameters(node: Node) -> tuple[Parameter, ...]:
    result = []
    for binder in binder_items(node):
        type_node, default = field(binder, "type"), field(binder, "default")
        names = binder_names(binder)
        if not names and binder.type == "instance_binder":
            names = [f"$inst{len(result)}"]
        result.extend(
            Parameter(
                name,
                TypeRef(node_text(type_node)) if type_node else None,
                node_text(default) if default else None,
            )
            for name in names
        )
    return tuple(result)


def docstring(node: Node) -> str | None:
    previous = node.prev_named_sibling
    while previous is not None and previous.type in TRIVIA:
        if previous.type == "doc_comment":
            return node_text(previous)[3:-2].strip()
        previous = previous.prev_named_sibling
    return None


def qualify(namespace: str, name: str) -> str:
    if name.startswith("_root_."):
        return name.removeprefix("_root_.")
    return f"{namespace}.{name}" if namespace else name


def type_ref(node: Node) -> TypeRef | None:
    result = field(node, "type")
    return TypeRef(node_text(result)) if result else None


def application(node: Node) -> tuple[Node, list[Node]]:
    args = []
    while node.type == "app":
        args.extend(reversed(fields(node, "arg")))
        head = field(node, "fn")
        if head is None:
            break
        node = head
    return node, list(reversed(args))
