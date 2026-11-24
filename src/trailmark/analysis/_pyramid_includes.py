"""Literal include dependencies rooted in a lexically bound Pyramid configurator."""

from __future__ import annotations

import ast
import importlib.util
from dataclasses import dataclass
from pathlib import Path

from tree_sitter import Node, Parser
from tree_sitter_language_pack import get_language

from trailmark.analysis._python_decorators import expression_bindings
from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.entrypoints import EntrypointEvidence
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit, NodeKind, SourceLocation
from trailmark.parsers._binding_index import syntax_nodes
from trailmark.parsers._common import make_location, node_text

_CONFIGURATOR = "pyramid.config.Configurator"


@dataclass(frozen=True)
class _Include:
    source: str
    module: str
    package: str
    symbol: str
    location: SourceLocation
    known: bool
    parameter: str | None


def discover_pyramid_includes(graph: CodeGraph) -> None:
    """Follow literal includes only after the registrar binding is established.

    An included callable receives a fresh Configurator from Pyramid. Propagate
    that evidence to its first parameter, without inferring arbitrary methods
    called ``include`` or promoting referenced functions to entrypoints.
    """
    occurrences = []
    parser = Parser(get_language("python"))
    for module in list(graph.nodes.values()):
        if module.kind != NodeKind.MODULE or Path(module.location.file_path).suffix != ".py":
            continue
        path = module.location.file_path
        try:
            root = parser.parse(Path(path).read_bytes()).root_node
        except OSError:
            continue
        calls = syntax_nodes(root, frozenset({"call"}))
        callees = [
            callee for call in calls if (callee := call.child_by_field_name("function")) is not None
        ]
        bindings = expression_bindings(root, path, callees)
        # A nondefault package changes the base for relative dotted names.
        # Until constructor argument provenance is tracked, keep those opaque.
        custom_package = any(
            bindings[callee].path == _CONFIGURATOR and _custom_package(call)
            for call in calls
            if (callee := call.child_by_field_name("function")) is not None and callee in bindings
        )
        members_written = any(
            (left := node.child_by_field_name("left")) is not None
            and left.type == "attribute"
            and node_text(left.child_by_field_name("attribute") or left) in {"include", "package"}
            for node in syntax_nodes(root, frozenset({"assignment", "augmented_assignment"}))
        )
        if members_written:
            continue
        units = [
            unit
            for unit in graph.nodes.values()
            if unit.kind == NodeKind.FUNCTION and unit.location.file_path == path
        ]
        for call in calls:
            callee = call.child_by_field_name("function")
            if callee is None or callee.type != "attribute":
                continue
            member = callee.child_by_field_name("attribute")
            arguments = call.child_by_field_name("arguments")
            if (
                member is None
                or member.text != b"include"
                or arguments is None
                or not arguments.named_children
            ):
                continue
            argument = arguments.named_children[0]
            if argument.type != "string":
                continue
            try:
                symbol = ast.literal_eval(node_text(argument))
            except (ValueError, SyntaxError):
                continue
            if not isinstance(symbol, str):
                continue
            location = make_location(call, path)
            owner = _owner(location, units, module)
            value = bindings[callee]
            known = value.path == _CONFIGURATOR + "().include"
            receiver = callee.child_by_field_name("object")
            parameter = (
                node_text(receiver)
                if receiver is not None
                and receiver.type == "identifier"
                and value.binding == "parameter"
                else None
            )
            package = module.id if Path(path).stem == "__init__" else module.id.rpartition(".")[0]
            if known and custom_package and symbol.startswith("."):
                continue
            occurrences.append(
                _Include(owner.id, module.id, package, symbol, location, known, parameter)
            )
    accepted: dict[str, str] = {}
    existing = set(graph.edges)
    pending = occurrences
    while pending:
        remaining = []
        changed = False
        for occurrence in pending:
            if not occurrence.known and (
                occurrence.parameter is None
                or accepted.get(occurrence.source) != occurrence.parameter
            ):
                remaining.append(occurrence)
                continue
            targets = _targets(graph, occurrence)
            if len(targets) != 1:
                continue
            target = targets[0]
            edge = CodeEdge(
                occurrence.source,
                target.id,
                EdgeKind.REFERENCES,
                EdgeConfidence.INFERRED,
                occurrence.location,
                (
                    ("reference_context", "pyramid_include"),
                    ("symbol", occurrence.symbol),
                    ("registrar_binding", _CONFIGURATOR),
                ),
            )
            if edge not in existing:
                graph.edges.append(edge)
                existing.add(edge)
            graph.add_entrypoint_evidence(
                EntrypointEvidence(
                    target.id,
                    "reference",
                    "pyramid_include",
                    occurrence.location,
                    source_id=occurrence.source,
                    symbol=occurrence.symbol,
                    details=(("registrar_binding", _CONFIGURATOR),),
                    unresolved=("invocation_context", "configuration_activation"),
                )
            )
            if target.parameters and target.id not in accepted:
                accepted[target.id] = target.parameters[0].name
                changed = True
        if not changed:
            break
        pending = remaining


def _owner(location: SourceLocation, units: list[CodeUnit], module: CodeUnit) -> CodeUnit:
    position = location.start_line, location.start_col or 0
    return min(
        (
            unit
            for unit in units
            if (unit.location.start_line, unit.location.start_col or 0)
            <= position
            <= (unit.location.end_line, unit.location.end_col or 0)
        ),
        key=lambda unit: unit.location.end_line - unit.location.start_line,
        default=module,
    )


def _custom_package(call: Node) -> bool:
    arguments = call.child_by_field_name("arguments")
    return arguments is not None and any(
        node.type != "keyword_argument"
        or node_text(node.child_by_field_name("name") or node) == "package"
        for node in arguments.named_children
    )


def _targets(graph: CodeGraph, occurrence: _Include) -> list[CodeUnit]:
    symbol = occurrence.symbol
    root, separator, module = occurrence.module.rpartition("::")
    prefix = root + separator if separator else ""
    if symbol.startswith("."):
        try:
            symbol = importlib.util.resolve_name(symbol, occurrence.package.removeprefix(prefix))
        except (ImportError, ValueError):
            return []
    if ":" in symbol:
        candidates = {prefix + symbol}
    else:
        parts = symbol.split(".")
        candidates = {f"{prefix}{symbol}:includeme"} | {
            f"{prefix}{'.'.join(parts[:index])}:{'.'.join(parts[index:])}"
            for index in range(1, len(parts))
        }
    return [
        unit
        for candidate in sorted(candidates)
        if (unit := graph.nodes.get(candidate)) is not None and unit.kind == NodeKind.FUNCTION
    ]
