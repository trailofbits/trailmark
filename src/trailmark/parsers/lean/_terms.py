"""Iterative, scope-aware extraction of Lean term and signature evidence."""

from __future__ import annotations

from dataclasses import replace

from tree_sitter import Node

from trailmark.models._parsing import Lookup, PendingReference
from trailmark.models.diagnostics import ParseDiagnostic
from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import BranchInfo, CodeUnit, NodeKind
from trailmark.parsers._common import add_contains_edge, make_location, node_text
from trailmark.parsers._simple import first_child_type
from trailmark.parsers.lean._resolution import candidates
from trailmark.parsers.lean._syntax import (
    DECLARATIONS,
    SKIP_TERMS,
    Binding,
    Context,
    Declaration,
    Task,
    application,
    binder_items,
    binder_names,
    binding_names,
    field,
    fields,
    parameters,
    type_ref,
)


class TermExtractor:
    def __init__(self, graph: CodeGraph, module: str, path: str) -> None:
        self.graph, self.module, self.path = graph, module, path
        self.namespace = ""
        self.opens: tuple[str, ...] = ()
        self.variables: dict[str, Binding] = {}
        self.mutual_group: str | None = None

    def run(self, tasks: list[Task]) -> None:
        """Process all nested expressions and callables without Python recursion."""
        pending = list(reversed(tasks))
        while pending:
            task = pending.pop()
            handler = getattr(self, f"_{task.action}")
            pending.extend(reversed(handler(task)))

    def _term(self, task: Task) -> list[Task]:
        handlers = {
            "identifier": self._identifier,
            "fun": self._lambda,
            "app": self._application,
            "let": self._let,
            "have": self._let,
            "match_alt": self._match_arm,
            "if_then_else": self._conditional,
            "proj": self._projection,
            "struct_lit": self._record,
            "struct_field": self._record_field,
            "forall": self._quantifier,
            "exists": self._quantifier,
            "sigma": self._quantifier,
            "big_op_binder": self._quantifier,
            "set_builder": self._set_builder,
            "subtype_lit": self._set_builder,
        }
        if task.node.type in SKIP_TERMS:
            return []
        return handlers.get(task.node.type, self._children)(task)

    def _children(self, task: Task) -> list[Task]:
        node, ctx = task.node, task.context
        skipped = {n.id for n in fields(node, "arg_name") + fields(node, "level")}
        types = {n.id for n in fields(node, "type")}
        result = []
        for child in node.named_children:
            if child.id in skipped:
                continue
            local = ctx if node.type == "do_block" else ctx.fork()
            if child.id in types:
                local = replace(local, calls=False)
            result.append(Task(child, local))
        return result

    def lookup(self, node: Node, name: str) -> Lookup:
        return candidates(
            node, name, namespace=self.namespace, opens=self.opens, group=self.mutual_group
        )

    def type_candidates(self, node: Node | None, ctx: Context) -> Lookup | None:
        while node is not None:
            if node.type in {"paren", "universe_app", "explicit_mode"}:
                node = next(iter(node.named_children), None)
            elif node.type == "app":
                node = field(node, "fn")
            else:
                break
        if node is None or node.type != "identifier":
            return None
        name = node_text(node)
        if name.split(".", 1)[0] in ctx.bindings:
            return None
        return self.lookup(node, name)

    def annotations(self, nodes: list[Node], ctx: Context) -> list[Task]:
        return [Task(n, replace(ctx.fork(), calls=False)) for n in nodes]

    def binders(self, node: Node, ctx: Context, *, trailing_type: bool = False) -> list[Task]:
        result = []
        common = field(node, "type") if trailing_type else None
        common_candidates, visited = None, False
        for binder in binder_items(node):
            if binder.type in {"identifier", "hole"}:
                if not visited:
                    result += self.annotations([common] if common else [], ctx)
                    common_candidates, visited = self.type_candidates(common, ctx), True
                typed = common_candidates
            else:
                result += self.annotations(fields(binder, "type") + fields(binder, "default"), ctx)
                typed = self.type_candidates(field(binder, "type"), ctx)
            ctx.bindings.update(
                (name, Binding(type_candidates=typed)) for name in binder_names(binder)
            )
        if common is not None and not visited:
            result += self.annotations([common], ctx)
        return result

    def signature(self, node: Node, ctx: Context) -> list[Task]:
        if node.type == "fun":
            return self.binders(node, ctx, trailing_type=True)
        return self.binders(node, ctx) + self.annotations(fields(node, "type"), ctx)

    def _function(self, task: Task) -> list[Task]:
        node, ctx, declaration = task.node, task.context, task.declaration
        name = field(node, "name")
        self.graph.nodes[ctx.owner] = CodeUnit(
            ctx.owner,
            node_text(name) if name else ctx.owner.rsplit(".", 1)[-1].rsplit(":", 1)[-1],
            NodeKind.FUNCTION,
            make_location(node, self.path),
            parameters=parameters(node),
            return_type=type_ref(node),
            docstring=declaration.doc,
            attributes=(*declaration.attributes, ("lean_declaration", node.type)),
        )
        add_contains_edge(self.graph, declaration.parent, ctx.owner)
        header = replace(
            ctx,
            calls=False,
            type_uses=True,
            reference_context="signature" if node.type in DECLARATIONS else "body",
        )
        result = self.signature(node, header)
        result += self._helpers(node, ctx)
        bodies = fields(node, "value") + ([] if node.type == "let" else fields(node, "body"))
        bodies += [c for c in node.named_children if c.type in {"match_alt", "where_struct"}]
        return result + [Task(body, ctx) for body in bodies] + [Task(node, ctx, "finish")]

    def _finish(self, task: Task) -> list[Task]:
        ctx = task.context
        self.graph.nodes[ctx.owner] = replace(
            self.graph.nodes[ctx.owner],
            branches=tuple(ctx.branches),
            cyclomatic_complexity=1 + len(ctx.branches),
        )
        return []

    def _helpers(self, node: Node, ctx: Context) -> list[Task]:
        helpers = first_child_type(node, {"where_decls"})
        if helpers is None:
            return []
        named = [(h, field(h, "name")) for h in helpers.named_children]
        for _helper, name in named:
            if name is not None:
                ctx.bindings[node_text(name)] = Binding(target=f"{ctx.owner}.{node_text(name)}")
        return [
            self.nested(helper, ctx, f"{ctx.owner}.{node_text(name)}")
            for helper, name in named
            if name is not None
        ]

    def nested(self, node: Node, ctx: Context, ident: str) -> Task:
        self.graph.edges.append(
            CodeEdge(
                ctx.owner,
                ident,
                EdgeKind.CALLS,
                EdgeConfidence.UNCERTAIN,
                make_location(node, self.path),
                (("deferred", True),),
            )
        )
        local = replace(ctx.fork(), owner=ident, branches=[])
        return Task(node, local, "function", Declaration(ctx.owner))

    def _lambda(self, task: Task) -> list[Task]:
        node, ctx = task.node, task.context
        if ctx.calls:
            return [self.nested(node, ctx, f"{ctx.owner}.<lambda>@{node.start_byte}")]
        local = ctx.fork()
        result = self.binders(node, local, trailing_type=True)
        bodies = fields(node, "body") + [c for c in node.named_children if c.type == "match_alt"]
        return result + [Task(body, local.fork()) for body in bodies]

    def _quantifier(self, task: Task) -> list[Task]:
        node, ctx = task.node, task.context.fork()
        result = self.binders(node, ctx, trailing_type=True)
        excluded = {n.id for n in fields(node, "type")}
        return result + [
            Task(child, ctx.fork())
            for child in node.named_children
            if child.type != "binders" and child.id not in excluded
        ]

    def _set_builder(self, task: Task) -> list[Task]:
        node, ctx = task.node, task.context.fork()
        result = self.annotations(fields(node, "type"), ctx)
        binder = field(node, "binder")
        if binder is not None:
            ctx.bindings[node_text(binder)] = Binding(
                type_candidates=self.type_candidates(field(node, "type"), ctx)
            )
        return result + [Task(predicate, ctx.fork()) for predicate in fields(node, "pred")]

    def _application(self, task: Task) -> list[Task]:
        node, ctx = task.node, task.context
        callee, args = application(node)
        if callee.type == "identifier" and node_text(callee) == "for":
            return self._loop(task, args)
        result = []
        while callee.type in {"paren", "explicit_mode", "universe_app"} and callee.named_children:
            result += self.annotations(fields(callee, "type"), ctx)
            callee = callee.named_children[0]
        if callee.type == "identifier" and ctx.calls:
            self.call(node, node_text(callee), ctx)
        result.append(Task(callee, ctx.fork()))
        return result + [Task(arg, ctx.fork()) for arg in args]

    def _loop(self, task: Task, args: list[Node]) -> list[Task]:
        if (
            len(args) != 4
            or args[0].type != "identifier"
            or node_text(args[1]) != "in"
            or args[3].type != "do_block"
        ):
            self.graph.parse_diagnostics.append(
                ParseDiagnostic(
                    "lean",
                    "lean",
                    make_location(task.node, self.path),
                    "unsupported_syntax",
                    "Loop shape is not preserved by the grammar; its terms were not analyzed",
                )
            )
            return []
        local = task.context.fork()
        local.bindings[node_text(args[0])] = Binding()
        return [Task(args[2], task.context.fork()), Task(args[3], local)]

    def _let(self, task: Task) -> list[Task]:
        node, ctx = task.node, task.context
        name, value = field(node, "name"), field(node, "value")
        if value is None:
            return []
        if ctx.calls and first_child_type(node, {"binders"}) is not None and name is not None:
            ident = f"{ctx.owner}.{node_text(name)}@{node.start_byte}"
            ctx.bindings[node_text(name)] = Binding(target=ident)
            result = [self.nested(node, ctx, ident)]
        else:
            result = self._let_value(task, value)
        return result + [Task(body, ctx.fork()) for body in fields(node, "body")]

    def _let_value(self, task: Task, value: Node) -> list[Task]:
        node, ctx = task.node, task.context
        local = ctx.fork()
        name = field(node, "name")
        if name is not None and any(c.type == "rec" for c in node.children):
            local.bindings.update((n, Binding()) for n in binding_names(name))
        result = self.signature(node, replace(local, calls=False))
        result.append(Task(value, local))
        if name is not None:
            typed = self.type_candidates(field(node, "type"), ctx)
            ctx.bindings.update((n, Binding(type_candidates=typed)) for n in binding_names(name))
        return result

    def _match_arm(self, task: Task) -> list[Task]:
        node, ctx = task.node, task.context.fork()
        patterns = fields(node, "pattern")
        if ctx.calls:
            ctx.branches.append(
                BranchInfo(make_location(node, self.path), ", ".join(map(node_text, patterns)))
            )
        ctx.bindings.update((name, Binding()) for p in patterns for name in binding_names(p))
        return [Task(body, ctx) for body in fields(node, "body")]

    def _conditional(self, task: Task) -> list[Task]:
        node, ctx = task.node, task.context
        conditions = fields(node, "cond")
        if conditions and ctx.calls:
            ctx.branches.append(
                BranchInfo(make_location(node, self.path), node_text(conditions[0]))
            )
        result = [Task(condition, ctx.fork()) for condition in conditions]
        local = ctx.fork()
        hypothesis = field(node, "hyp")
        if hypothesis is not None:
            local.bindings[node_text(hypothesis)] = Binding()
        return result + [
            Task(arm, local.fork()) for arm in fields(node, "then") + fields(node, "else")
        ]

    def _projection(self, task: Task) -> list[Task]:
        node, ctx = task.node, task.context
        receiver, name = field(node, "term"), field(node, "field")
        if receiver is None:
            return []
        if name is not None and name.type == "identifier":
            self.project(name, node_text(name), ctx, self.receiver(receiver, ctx), "projection")
        return [Task(receiver, ctx.fork())]

    def receiver(self, node: Node, ctx: Context) -> Binding | None:
        while node.type == "paren" and node.named_children:
            node = node.named_children[0]
        return ctx.bindings.get(node_text(node)) if node.type == "identifier" else None

    def _record(self, task: Task) -> list[Task]:
        node, ctx = task.node, task.context
        sources = fields(node, "source")
        receiver = self.receiver(sources[0], ctx) if len(sources) == 1 else None
        for child in node.named_children:
            name = field(child, "name") if child.type == "struct_field" else None
            if name is not None:
                self.project(name, node_text(name), ctx, receiver, "record_update")
        return [Task(child, ctx.fork()) for child in node.named_children]

    def _record_field(self, task: Task) -> list[Task]:
        node, ctx = task.node, task.context
        name, value = field(node, "name"), field(node, "value")
        if ctx.calls and name is not None and first_child_type(node, {"binders"}) is not None:
            return [self.nested(node, ctx, f"{ctx.owner}.{node_text(name)}@{node.start_byte}")]
        if value is not None:
            local = ctx.fork()
            return self.binders(node, local) + [Task(value, local)]
        if name is not None:
            return [Task(name, ctx)]
        return []

    def _identifier(self, task: Task) -> list[Task]:
        node, ctx = task.node, task.context
        name = node_text(node)
        first, dot, suffix = name.partition(".")
        binding = ctx.bindings.get(first)
        if binding is not None and dot:
            self.project(node, suffix, ctx, binding, "projection")
        elif binding is not None:
            if binding.target is not None:
                self.reference(node, binding.target, ctx)
        else:
            self.reference(node, name, ctx, PendingReference(lookup=self.lookup(node, name)))
        return []

    def reference(
        self, node: Node, target: str, ctx: Context, pending: PendingReference | None = None
    ) -> None:
        edge = CodeEdge(
            ctx.owner,
            target,
            EdgeKind.REFERENCES,
            EdgeConfidence.INFERRED,
            make_location(node, self.path),
            (("lean_reference_context", ctx.reference_context),),
        )
        if pending is not None:
            edge = self.graph._parse_state.stage(edge, pending)
        self.graph.edges.append(edge)
        if ctx.type_uses:
            self.graph.edges.append(
                replace(
                    edge,
                    kind=EdgeKind.TYPE_USES,
                    attributes=tuple((k, v) for k, v in edge.attributes if k == "_lookup")
                    + (("type_name", node_text(node)),),
                )
            )

    def project(
        self, node: Node, name: str, ctx: Context, binding: Binding | None, context: str
    ) -> None:
        if binding is None or binding.type_candidates is None or "." in name:
            return
        self.reference(
            node,
            name,
            replace(ctx, reference_context=context, type_uses=False),
            PendingReference(receiver=binding.type_candidates, field=name),
        )

    def call(self, node: Node, name: str, ctx: Context) -> None:
        binding = ctx.bindings.get(name.split(".", 1)[0])
        if binding is None:
            pending = PendingReference(lookup=self.lookup(node, name))
            attributes = ()
            target = name
        else:
            pending = None
            target = binding.target if "." not in name else None
            attributes = (
                ()
                if target
                else (("binding_blocked", True), ("binding_reason", "lexical_callable"))
            )
        edge = CodeEdge(
            ctx.owner,
            target or name,
            EdgeKind.CALLS,
            EdgeConfidence.INFERRED if binding and target else EdgeConfidence.UNCERTAIN,
            make_location(node, self.path),
            attributes,
        )
        self.graph.edges.append(
            self.graph._parse_state.stage(edge, pending) if pending is not None else edge
        )
