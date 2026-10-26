"""PHP include execution for literal absolute paths and __DIR__/__FILE__ expressions."""

from dataclasses import replace
from pathlib import Path

from tree_sitter import Node

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import Attribute

INCLUDE_NODES = frozenset(
    {
        "include_expression",
        "include_once_expression",
        "require_expression",
        "require_once_expression",
    }
)


def include_attributes(site: Node, file_path: str) -> tuple[Attribute, ...]:
    expression = site.named_children[0] if site.named_children else None
    value = _literal(expression, file_path) if expression is not None else None
    attributes: tuple[Attribute, ...] = (
        ("php_include_expression", (site.text or b"").decode(errors="replace")),
    )
    if value is not None and Path(value).is_absolute():
        attributes += (("php_include_path", str(Path(value).resolve())),)
    return attributes


def _literal(node: Node, file_path: str, depth: int = 0) -> str | None:
    if depth >= 64:
        return None
    text = (node.text or b"").decode(errors="replace")
    if node.type == "name" and text.upper() in {"__DIR__", "__FILE__"}:
        file = Path(file_path).resolve()
        return str(file.parent if text.upper() == "__DIR__" else file)
    if node.type == "parenthesized_expression" and len(node.named_children) == 1:
        return _literal(node.named_children[0], file_path, depth + 1)
    if node.type == "binary_expression":
        left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
        operator = node.child_by_field_name("operator")
        if (
            left is not None
            and right is not None
            and operator is not None
            and operator.text == b"."
        ):
            a, b = _literal(left, file_path, depth + 1), _literal(right, file_path, depth + 1)
            return a + b if a is not None and b is not None else None
    if (
        node.type in {"string", "encapsed_string"}
        and all(child.type == "string_content" for child in node.named_children)
        and len(text) >= 2
        and text[0] == text[-1]
        and text[0] in {"'", '"'}
        and "\\" not in text
    ):
        return text[1:-1]
    return None


def link_php_includes(graph: CodeGraph) -> None:
    """Bind includes only to inspected executable file bodies; preserve unknown paths."""
    bodies: dict[str, list[str]] = {}
    for unit in graph.nodes.values():
        if dict(unit.attributes).get("php_execution_kind") in {"script", "template"}:
            bodies.setdefault(str(Path(unit.location.file_path).resolve()), []).append(unit.id)
    for index, edge in enumerate(graph.edges):
        if edge.kind != EdgeKind.CALLS:
            continue
        path = dict(edge.attributes).get("php_include_path")
        targets = bodies.get(path, []) if isinstance(path, str) else []
        if len(targets) == 1:
            graph.edges[index] = replace(
                edge, target_id=targets[0], confidence=EdgeConfidence.INFERRED
            )
