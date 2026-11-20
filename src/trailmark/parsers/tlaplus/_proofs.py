"""Physical proof-step owners and conservative source citations, without checking."""

from collections import Counter
from collections.abc import Callable
from dataclasses import replace

from tree_sitter import Node

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import AttributeValue, CodeUnit, NodeKind, NodeOrigin, SourceLocation
from trailmark.parsers._common import make_location, node_text
from trailmark.parsers._formal import Namespace, walk
from trailmark.parsers._formal_linking import link_symbols

_STEPS = frozenset({"proof_step", "qed_step"})
_DATA = frozenset({"comment", "block_comment", "string"})
_SKIP = _DATA | {"non_terminal_proof", "proof_step_id", "proof_step_ref", "prefixed_op"}
_BINDERS = frozenset(
    {
        "new",
        "take_proof_step",
        "pick_proof_step",
        "definition_proof_step",
        "let_in",
        "quantifier_bound",
        "choose",
        "unbounded_quantification",
        "lambda",
    }
)


def _label(node: Node) -> str | None:
    name = next((c for c in node.named_children if c.type == "name"), None)
    if name is None or not node_text(name) or node.has_error:
        return None
    return "".join(node_text(node).split()).rstrip(".")


def _references(
    node: Node,
    owner: str,
    visible: dict[str, str | None],
    env: Namespace,
    blocked: bool,
    spelling: Callable[[Node], str | None],
) -> None:
    source = env.source
    for part in walk(node, _SKIP, include_skipped=True):
        if part.type == "proof_step_ref":
            name = _label(part) or node_text(part)
            target = visible.get(name)
            source.graph.edges.append(
                CodeEdge(
                    owner,
                    target or f"{owner}.<proof-citation>.{name}",
                    EdgeKind.REFERENCES,
                    EdgeConfidence.INFERRED if target else EdgeConfidence.UNCERTAIN,
                    make_location(part, source.path),
                    attributes=(("reference_context", "proof"), ("spelling", name)),
                )
            )
        elif part.type == "identifier_ref" and (name := spelling(part)) is not None:
            if blocked:
                # A proof binder may shadow even a DEF/BY spelling. Preserve the
                # written site without falling back to a module-level namesake.
                source.graph.edges.append(
                    CodeEdge(
                        owner,
                        f"{owner}.<proof-citation>.{name}",
                        EdgeKind.REFERENCES,
                        EdgeConfidence.UNCERTAIN,
                        make_location(part, source.path),
                        attributes=(("reference_context", "proof"), ("spelling", name)),
                    )
                )
                continue
            start = len(source.graph.edges)
            source.references(
                owner,
                part,
                frozenset({"identifier_ref"}),
                namespace=env.name,
                opens=env.opens,
                context="proof",
                spelling=spelling,
            )
            for i in range(start, len(source.graph.edges)):
                edge = source.graph.edges[i]
                source.graph.edges[i] = replace(
                    edge, attributes=(*edge.attributes, ("tlaplus_proof_reference", True))
                )


def extract_proof(
    declaration: Node,
    proof: Node,
    owner: str,
    env: Namespace,
    spelling: Callable[[Node], str | None],
) -> None:
    """Keep hierarchy and level-local step names; never interpret a proof rule."""
    blocked = declaration.has_error or any(n.type in _BINDERS for n in walk(declaration, _DATA))
    pending: list[tuple[Node, str, dict[str, str | None], int]] = [(proof, owner, {}, 0)]
    while pending:
        current, parent, inherited, depth = pending.pop()
        if depth > 128:
            env.source.unsupported(current, "Proof nesting exceeds the supported depth of 128")
            continue
        if current.type != "non_terminal_proof":
            _references(current, parent, inherited, env, blocked, spelling)
            continue
        steps = [n for n in current.named_children if n.type in _STEPS]
        labels = [_label(c) for n in steps for c in n.named_children if c.type == "proof_step_id"]
        duplicates = {label for label, count in Counter(labels).items() if count > 1}
        visible = inherited.copy()
        nested = []
        for step in steps:
            ident = next((c for c in step.named_children if c.type == "proof_step_id"), None)
            label = _label(ident) if ident is not None else None
            name = label or f"proof@{step.start_point.row + 1}:{step.start_point.column}"
            qualified = str(dict(env.source.graph.nodes[parent].attributes)["qualified_name"])
            target = env.source.declare(
                step,
                name,
                "proof_step",
                namespace=qualified,
                parent=parent,
                kind=NodeKind.TEMPLATE,
                attributes=(("executable", False), ("private", True)),
            )
            if label is not None:
                visible[label] = None if label in duplicates else target
            _references(step, target, visible, env, blocked, spelling)
            for part in walk(step, _DATA | {"non_terminal_proof"}, include_skipped=True):
                if part.type == "non_terminal_proof":
                    nested.append((part, target, visible.copy(), depth + 1))
        pending.extend(reversed(nested))


def _key(edge: CodeEdge) -> tuple[str, SourceLocation | None, AttributeValue]:
    return edge.source_id, edge.location, dict(edge.attributes).get("spelling")


def link_proofs(graph: CodeGraph) -> None:
    """Keep unresolved proof source references after ordinary visibility linking."""
    pending = [e for e in graph.edges if dict(e.attributes).get("tlaplus_proof_reference")]
    _materialize_proxies(graph)
    link_symbols(graph)
    retained = {_key(e) for e in graph.edges}
    for edge in pending:
        if _key(edge) in retained:
            continue
        attrs = {k: v for k, v in edge.attributes if k != "_lookup"}
        graph.edges.append(
            replace(
                edge,
                target_id=f"{edge.source_id}.<proof-citation>.{attrs['spelling']}",
                confidence=EdgeConfidence.UNCERTAIN,
                attributes=tuple(attrs.items()),
            )
        )
    _materialize_proxies(graph)


def _materialize_proxies(graph: CodeGraph) -> None:
    for edge in graph.edges:
        if (
            edge.target_id not in graph.nodes
            and edge.location is not None
            and dict(edge.attributes).get("reference_context") == "proof"
            and graph._parse_state.reference(edge) is None
        ):
            spelling = str(dict(edge.attributes)["spelling"])
            graph.nodes[edge.target_id] = CodeUnit(
                edge.target_id,
                spelling,
                NodeKind.PROXY,
                edge.location,
                origin=NodeOrigin.PROXY,
                attributes=(("proxy_kind", "unresolved"), ("raw_symbol", spelling)),
            )
