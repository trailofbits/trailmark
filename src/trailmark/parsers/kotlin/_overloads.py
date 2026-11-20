"""Resolve only uniquely shaped fixed positional overloads in lexical scope."""

from dataclasses import replace

from tree_sitter import Node

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind
from trailmark.parsers._common import first_child_by_type

_COMMENTS = frozenset({"line_comment", "multiline_comment"})


def positional_arity(node: Node) -> int | None:
    """Exclude receiver, default, variadic and unrecognized parameter syntax."""
    parameters = first_child_by_type(node, "function_value_parameters")
    if node.has_error or parameters is None or first_child_by_type(node, "receiver_type"):
        return None
    if any(c.type not in {"(", ")", ",", "parameter", *_COMMENTS} for c in parameters.children):
        return None
    return sum(c.type == "parameter" for c in parameters.named_children)


def argument_count(node: Node) -> int | None:
    """Count explicit positional arguments, excluding spread and trailing lambdas."""
    suffix = first_child_by_type(node, "call_suffix")
    if node.has_error or suffix is None:
        return None
    if any(
        c.type not in {"value_arguments", "type_arguments", *_COMMENTS}
        for c in suffix.named_children
    ):
        return None
    arguments = first_child_by_type(suffix, "value_arguments")
    if arguments is None:
        return None
    values = [c for c in arguments.named_children if c.type not in _COMMENTS]
    if any(
        value.type != "value_argument"
        or any(c.type in {"=", "spread_expression"} for c in value.children)
        for value in values
    ):
        return None
    return len(values)


def resolve_positional_overloads(graph: CodeGraph) -> None:
    """Use arity only when every competing local declaration has fixed shape."""
    owners = {e.target_id: e.source_id for e in graph.edges if e.kind == EdgeKind.CONTAINS}
    declarations: dict[tuple[str, str], list[str]] = {}
    for unit in graph.nodes.values():
        if unit.kind in {NodeKind.FUNCTION, NodeKind.METHOD} and unit.id in owners:
            declarations.setdefault((owners[unit.id], unit.name), []).append(unit.id)
    edges = []
    for edge in graph.edges:
        attrs = dict(edge.attributes)
        name, count = attrs.get("bare_name"), attrs.get("argument_count")
        if (
            edge.kind == EdgeKind.CALLS
            and isinstance(name, str)
            and isinstance(count, int)
            and not attrs.get("binding_blocked")
        ):
            owner = edge.source_id
            seen = set()
            while owner and owner not in seen:
                seen.add(owner)
                candidates = declarations.get((owner, name), [])
                if candidates:
                    scope = graph.nodes[owner]
                    if (
                        dict(scope.attributes).get("incomplete_overload_scope")
                        or scope.kind == NodeKind.ENUM
                        or (
                            scope.kind in {NodeKind.CLASS, NodeKind.INTERFACE}
                            and name in {"equals", "hashCode", "toString"}
                        )
                    ):
                        break
                    arities = {
                        key: dict(graph.nodes[key].attributes).get("positional_arity")
                        for key in candidates
                    }
                    matching = [key for key, arity in arities.items() if arity == count]
                    if len(candidates) > 1 and None not in arities.values() and len(matching) == 1:
                        edge = replace(
                            edge, target_id=matching[0], confidence=EdgeConfidence.INFERRED
                        )
                    break
                owner = owners.get(owner, "")
        edges.append(edge)
    graph.edges[:] = edges
