"""Source-anchored Ruby aliases with a bounded same-body binding proof."""

from collections import Counter

from tree_sitter import Node

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit, NodeKind
from trailmark.parsers._common import make_location, node_text

_BOUNDARIES = frozenset({"class", "module", "singleton_class", "method", "singleton_method"})
_VISIBILITY = frozenset({"private", "protected", "public"})
_HOOKS = _VISIBILITY | {"alias_method", "method_added", "singleton_method_added"}


def _method_name(node: Node) -> str:
    method = node.child_by_field_name("method")
    return node_text(method) if method is not None else ""


def _literal(node: Node | None, *, keyword: bool = False) -> str | None:
    if node is None:
        return None
    text = node_text(node)
    if node.type == "simple_symbol":
        return text[1:]
    if keyword and node.type in {"identifier", "operator"}:
        return text
    if node.type == "string" and all(c.type == "string_content" for c in node.named_children):
        value = text[1:-1]
        return value if value and "\\" not in value else None
    return None


def _operation(node: Node) -> tuple[str | None, str | None] | None:
    if node.type == "alias":
        return (
            _literal(node.child_by_field_name("name"), keyword=True),
            _literal(node.child_by_field_name("alias"), keyword=True),
        )
    if node.type != "call" or _method_name(node) != "alias_method":
        return None
    arguments = node.child_by_field_name("arguments")
    items = arguments.named_children if arguments is not None else []
    if len(items) != 2:
        return None, None
    return _literal(items[0]), _literal(items[1])


def _owned_nodes(body: Node) -> list[Node]:
    result = []
    pending = list(reversed(body.named_children))
    while pending:
        node = pending.pop()
        result.append(node)
        if node.type not in _BOUNDARIES:
            pending.extend(reversed(node.named_children))
    return result


def _safe_statement(node: Node) -> bool:
    if node.type in {"comment", "method", "singleton_method", "alias"}:
        return True
    if node.type == "identifier":
        return node_text(node) in _VISIBILITY
    if node.type != "call" or node.child_by_field_name("receiver") is not None:
        return False
    if _method_name(node) == "alias_method":
        return True  # Literal arity and names are checked for every alias operation.
    if _method_name(node) not in _VISIBILITY:
        return False
    arguments = node.child_by_field_name("arguments")
    return arguments is None or all(
        child.type in {"method", "singleton_method"} or _literal(child) is not None
        for child in arguments.named_children
    )


def record_aliases(body: Node, owner: str, singleton: bool, path: str, graph: CodeGraph) -> None:
    """Record direct literal aliases; retain unresolved facts for unproven names."""
    nodes = _owned_nodes(body)
    operations = [(node, names) for node in nodes if (names := _operation(node)) is not None]
    if not operations:
        return
    receiver = "singleton" if singleton else "instance"
    definitions = [node for node in nodes if node.type in {"method", "singleton_method"}]
    counts = Counter(
        (
            node_text(name),
            "singleton" if singleton or node.type == "singleton_method" else "instance",
        )
        for node in definitions
        if (name := node.child_by_field_name("name")) is not None
    )
    names = Counter(name for _, (name, _) in operations if name is not None)
    blocked = body.has_error or any(
        name is None or target is None or node.child_by_field_name("receiver") is not None
        for node, (name, target) in operations
    )
    blocked = blocked or not all(_safe_statement(node) for node in body.named_children)
    owners = {e.target_id: e.source_id for e in graph.edges if e.kind == EdgeKind.CONTAINS}
    blocked = (
        blocked
        or any(e.kind == EdgeKind.INHERITS and e.source_id == owner for e in graph.edges)
        or any(node.type == "undef" for node in nodes)
    )
    blocked = blocked or any(
        node.type == "singleton_method"
        and (receiver_node := node.child_by_field_name("object")) is not None
        and receiver_node.type != "self"
        for node in nodes
    )
    blocked = blocked or any(
        owners.get(unit.id) == owner
        and unit.name in _HOOKS
        and dict(unit.attributes).get("ruby_receiver") == "singleton"
        for unit in graph.nodes.values()
    )
    methods: dict[str, CodeUnit] = {}
    for unit in graph.nodes.values():
        if (
            owners.get(unit.id) == owner
            and unit.kind == NodeKind.METHOD
            and dict(unit.attributes).get("ruby_receiver") == receiver
            and counts[unit.name, receiver] == 1
        ):
            methods[unit.name] = unit
    aliases: dict[str, CodeUnit] = {}
    for node, (name, target_name) in operations:
        if name is None:
            continue
        target = methods.get(target_name or "") or aliases.get(target_name or "")
        proven = (
            not blocked
            and node.parent == body
            and names[name] == 1
            and not counts[name, receiver]
            and target is not None
            and (target.location.start_line, target.location.start_col or 0)
            < (node.start_point.row + 1, node.start_point.column)
        )
        if proven and target is not None:
            aliases[name] = target
        graph.edges.append(
            CodeEdge(
                source_id=owner,
                target_id=target.id if proven and target else f"{owner}.{name}@alias-unresolved",
                kind=EdgeKind.REFERENCES,
                confidence=EdgeConfidence.INFERRED if proven else EdgeConfidence.UNCERTAIN,
                location=make_location(node, path),
                attributes=(
                    ("ruby_alias_name", name),
                    ("ruby_alias_receiver", receiver),
                    ("ruby_alias_blocked", not proven),
                    ("ruby_alias_target_line", target.location.start_line if target else 0),
                    ("ruby_alias_target_col", target.location.start_col if target else 0),
                ),
            )
        )


def alias_index(graph: CodeGraph) -> dict[tuple[str, str, str], str | None]:
    """Reject stale snapshots, reopened replacements and duplicate alias names."""
    aliases: dict[tuple[str, str, str], str | None] = {}
    for edge in graph.edges:
        attributes = dict(edge.attributes)
        name = attributes.get("ruby_alias_name")
        if edge.kind != EdgeKind.REFERENCES or not isinstance(name, str):
            continue
        key = edge.source_id, name, str(attributes["ruby_alias_receiver"])
        target = graph.nodes.get(edge.target_id)
        owner = graph.nodes.get(edge.source_id)
        proven = (
            key not in aliases
            and not attributes.get("ruby_alias_blocked")
            and target is not None
            and edge.location is not None
            and owner is not None
            and owner.location.file_path == edge.location.file_path
            and owner.location.start_line <= edge.location.start_line <= owner.location.end_line
            and target.location.file_path == edge.location.file_path
            and target.location.start_line == attributes["ruby_alias_target_line"]
            and target.location.start_col == attributes["ruby_alias_target_col"]
        )
        aliases[key] = edge.target_id if proven else None
    return aliases
