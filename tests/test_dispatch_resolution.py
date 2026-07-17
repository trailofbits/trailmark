"""Regression tests for conservative TypeScript interface dispatch."""

from __future__ import annotations

from pathlib import Path

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.parse import parse_file


def test_constructor_assigned_interface_dispatch_resolves(tmp_path: Path) -> None:
    source = tmp_path / "handler.ts"
    source.write_text(
        "interface Handler { process(): void }\n"
        "class HandlerImpl implements Handler { process(): void {} }\n"
        "function entry(): void {\n"
        "  const handler: Handler = new HandlerImpl();\n"
        "  handler.process();\n"
        "}\n",
    )
    graph = parse_file(str(source), "typescript")
    edge = next(
        edge
        for edge in graph.edges
        if edge.source_id == "handler:entry" and edge.kind == EdgeKind.CALLS
    )
    assert edge.target_id == "handler:HandlerImpl.process"
    assert edge.confidence == EdgeConfidence.INFERRED


def test_dynamic_property_dispatch_remains_unresolved(tmp_path: Path) -> None:
    source = tmp_path / "dynamic.ts"
    source.write_text("function entry(handlers: any, key: string) { handlers[key](); }\n")
    graph = parse_file(str(source), "typescript")
    assert not any(
        edge.source_id == "dynamic:entry" and "HandlerImpl" in edge.target_id
        for edge in graph.edges
    )


def test_nested_arrow_assignment_does_not_resolve_outer_call(tmp_path: Path) -> None:
    source = tmp_path / "handler.ts"
    source.write_text(
        "interface Handler { process(): void }\n"
        "class HandlerImpl implements Handler { process(): void {} }\n"
        "class OtherImpl implements Handler { process(): void {} }\n"
        "function entry(): void {\n"
        "  let handler: Handler = new HandlerImpl();\n"
        "  const replace = () => { handler = new OtherImpl(); };\n"
        "  handler.process();\n"
        "}\n",
    )
    graph = parse_file(str(source), "typescript")

    edge = next(
        edge
        for edge in graph.edges
        if edge.source_id == "handler:entry"
        and edge.kind == EdgeKind.CALLS
        and edge.target_id.endswith(".process")
    )
    assert edge.target_id == "handler:HandlerImpl.process"
    assert edge.confidence == EdgeConfidence.INFERRED
