"""Retain literal macro template source without claiming an expansion."""

from collections.abc import Callable
from dataclasses import replace

from tree_sitter import Node, Parser, Range

from trailmark.models.diagnostics import ParseDiagnostic
from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit, NodeKind, NodeOrigin
from trailmark.parsers._bindings import namespace_receiver_matches
from trailmark.parsers._common import add_contains_edge, make_location, node_text


def extract_templates(
    root: Node,
    source: bytes,
    path: str,
    module: str,
    graph: CodeGraph,
    parser: Parser,
    visit: Callable[[Node, str, str], None],
) -> dict[str, tuple[str, Node]]:
    retained: dict[str, tuple[str, Node]] = {}
    pending = [root]
    while pending:
        macro = pending.pop()
        if macro.type != "macro_definition":
            pending.extend(reversed(macro.named_children))
            continue
        name = macro.child_by_field_name("name")
        if name is None:
            continue
        owners = [
            n
            for n in graph.nodes.values()
            if n.kind in {NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.MODULE}
            and n.location.file_path == path
            and (n.location.start_line - 1, n.location.start_col or 0)
            <= macro.start_point
            < (n.location.end_line - 1, n.location.end_col or 0)
        ]
        owner = max(
            owners,
            key=lambda n: (
                n.location.start_line,
                n.location.start_col or 0,
                -n.location.end_line,
                -(n.location.end_col or 0),
                n.kind != NodeKind.MODULE,
            ),
            default=None,
        )
        namespace = owner.id.partition(":")[0] if owner else module
        prefix = owner.id + "." if owner and owner.kind != NodeKind.MODULE else namespace + ":"
        ident = f"{prefix}<macro@{macro.start_byte}>.{node_text(name)}"
        _unit(graph, ident, node_text(name), macro, path, owner.id if owner else module)
        retained[ident] = (ident, macro)
        for rule in macro.named_children:
            if rule.type != "macro_rule" or (body := rule.child_by_field_name("right")) is None:
                continue
            arm = f"{ident}.<arm@{rule.start_byte}>"
            _unit(graph, arm, "<arm>", rule, path, ident, role="arm")
            retained[arm] = (arm, macro)
            data = bytearray(source)
            nodes = [body]
            opaque = False
            metavariables = []
            while nodes:
                node = nodes.pop()
                if node.type == "metavariable":
                    metavariables.append(node)
                    data[node.start_byte] = ord(" ")
                elif node.type in {"token_repetition", "metavariable_expression"}:
                    opaque = True
                nodes.extend(node.named_children)
            bounded = Parser(parser.language)
            bounded.included_ranges = [
                Range(
                    start_byte=body.start_byte + 1,
                    end_byte=body.end_byte - 1,
                    start_point=(body.start_point.row, body.start_point.column + 1),
                    end_point=(body.end_point.row, body.end_point.column - 1),
                )
            ]
            tree = bounded.parse(bytes(data))
            if opaque or tree.root_node.has_error:
                graph.parse_diagnostics.append(
                    ParseDiagnostic(
                        "rust",
                        "rust",
                        make_location(body, path),
                        "unsupported",
                        "Macro template arm remains opaque: not a literal Rust declaration region",
                    )
                )
                continue
            before = set(graph.nodes)
            edge_start = len(graph.edges)
            visit(tree.root_node, arm, namespace)
            retained.update(dict.fromkeys(set(graph.nodes) - before, (arm, macro)))
            declarations = {}
            nodes = [tree.root_node]
            while nodes:
                node = nodes.pop()
                name = node.child_by_field_name("name")
                if name is not None and any(
                    meta.start_byte < name.end_byte and name.start_byte < meta.end_byte
                    for meta in metavariables
                ):
                    declarations[make_location(node, path)] = node_text(name)
                nodes.extend(node.named_children)
            dynamic_names = False
            for new_id in set(graph.nodes) - before:
                unit = graph.nodes[new_id]
                if unit.location in declarations:
                    dynamic_names = True
                    graph.nodes[new_id] = replace(
                        unit,
                        name="$" + unit.name,
                        attributes=(*unit.attributes, ("template_name_parameter", True)),
                    )
            dynamic = {}
            bare = {}
            nodes = [tree.root_node]
            while nodes:
                node = nodes.pop()
                if node.type == "call_expression":
                    head = node.child_by_field_name("function")
                    if head is not None and head.type == "identifier":
                        bare[make_location(node, path)] = node_text(head)
                    if head is not None and (
                        (dynamic_names and head.type == "identifier")
                        or any(
                            meta.start_byte < head.end_byte and head.start_byte < meta.end_byte
                            for meta in metavariables
                        )
                    ):
                        location = make_location(node, path)
                        dynamic[location] = (
                            replace(location, start_col=(location.start_col or 0) - 1)
                            if node.start_byte > 0 and source[node.start_byte - 1] == ord("$")
                            else location
                        )
                nodes.extend(node.named_children)
            for index in range(edge_start, len(graph.edges)):
                edge = graph.edges[index]
                if edge.kind == EdgeKind.CALLS and edge.location in bare:
                    edge = replace(
                        edge,
                        attributes=(*edge.attributes, ("template_bare_name", bare[edge.location])),
                    )
                    graph.edges[index] = edge
                if edge.kind == EdgeKind.CALLS and edge.location in dynamic:
                    graph.edges[index] = replace(
                        edge,
                        target_id=f"{edge.source_id}.$template_dynamic.{edge.target_id}",
                        confidence=EdgeConfidence.UNCERTAIN,
                        location=dynamic[edge.location],
                        attributes=(*edge.attributes, ("binding_blocked", True)),
                    )
            # An impl of a metavariable has no corresponding declared source type.
            for edge in tuple(graph.edges):
                if (
                    edge.kind == EdgeKind.CONTAINS
                    and edge.source_id.startswith(arm + ".")
                    and edge.source_id not in graph.nodes
                ):
                    _unit(
                        graph,
                        edge.source_id,
                        edge.source_id.rsplit(".", 1)[-1],
                        body,
                        path,
                        arm,
                        role="impl",
                    )
                    retained[edge.source_id] = (arm, macro)
    return retained


def _unit(
    graph: CodeGraph,
    ident: str,
    name: str,
    node: Node,
    path: str,
    owner: str,
    *,
    role: str = "macro",
) -> None:
    graph.nodes[ident] = CodeUnit(
        ident,
        name,
        NodeKind.TEMPLATE,
        make_location(node, path),
        attributes=(
            ("macro_template", True),
            ("executable", False),
            ("template_declaration_kind", role),
        ),
    )
    add_contains_edge(graph, owner, ident)


def finalize_templates(graph: CodeGraph, retained: dict[str, tuple[str, Node]]) -> None:
    for ident in retained:
        unit = graph.nodes[ident]
        attrs = dict(unit.attributes)
        attrs.setdefault("template_declaration_kind", unit.kind.value)
        attrs.update(macro_template=True, executable=False)
        graph.nodes[ident] = replace(unit, kind=NodeKind.TEMPLATE, attributes=tuple(attrs.items()))
    edges = []
    for edge in graph.edges:
        if edge.source_id not in retained or edge.kind == EdgeKind.CONTAINS:
            edges.append(edge)
            continue
        target = edge.target_id
        arm, macro = retained[edge.source_id]
        name = dict(edge.attributes).get("template_bare_name")
        if isinstance(name, str) and not target.startswith(arm + "."):
            candidate = f"{edge.source_id.partition(':')[0]}:{name}"
            if not namespace_receiver_matches(edge, macro, graph, candidate):
                target = f"{edge.source_id}.$template_shadowed.{name}"
        if target not in graph.nodes:
            target = f"proxy.unresolved:{edge.source_id}.$template.{edge.target_id}"
            graph.nodes.setdefault(
                target,
                CodeUnit(
                    target,
                    edge.target_id,
                    NodeKind.PROXY,
                    edge.location or graph.nodes[edge.source_id].location,
                    origin=NodeOrigin.PROXY,
                    attributes=(("macro_template", True), ("executable", False)),
                ),
            )
        attrs = dict(edge.attributes)
        attrs.update(reference_context="macro_template", template_edge_kind=edge.kind.value)
        edges.append(
            replace(
                edge,
                target_id=target,
                kind=EdgeKind.REFERENCES,
                confidence=EdgeConfidence.UNCERTAIN,
                attributes=tuple(attrs.items()),
            )
        )
    graph.edges[:] = edges
