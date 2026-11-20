"""Recovery keeps original byte columns and lines after leading whitespace."""

from pathlib import Path

import pytest

from trailmark.models.edges import EdgeKind
from trailmark.parse import parse_file


@pytest.mark.parametrize("prefix", ["\n", " \n\t\n", "\n\n\n"])
@pytest.mark.parametrize("language", ["whyml", "rocq"])
def test_recovered_declaration_and_reference_sites_use_original_source(
    tmp_path: Path, prefix: str, language: str
) -> None:
    if language == "whyml":
        text = (
            "module Broken\nlet invalid () = ???\nend\n"
            "module Good\nlet target (x:int) = x\nlet run () = target 0\nend\n"
        )
        extension, kind = "mlw", EdgeKind.CALLS
    else:
        text = (
            "Definition invalid := { unsupported ??? }.\n"
            "Definition target := 0.\nDefinition run := target.\n"
        )
        extension, kind = "v", EdgeKind.REFERENCES
    source = tmp_path / f"Recovered.{extension}"
    source.write_text(prefix + text)
    graph = parse_file(str(source), language)
    assert graph.parse_diagnostics

    target = next(unit for unit in graph.nodes.values() if unit.name == "target")
    run = next(unit for unit in graph.nodes.values() if unit.name == "run")
    lines = (prefix + text).splitlines()
    target_line = next(i + 1 for i, line in enumerate(lines) if "target" in line)
    run_line = next(i + 1 for i, line in enumerate(lines) if "run" in line)
    assert target.location.start_line == target_line
    assert run.location.start_line == run_line
    edge = next(
        edge
        for edge in graph.edges
        if edge.kind == kind and edge.source_id == run.id and edge.target_id == target.id
    )
    assert edge.location is not None
    assert edge.location.start_line == run_line
    assert edge.location.start_col == lines[run_line - 1].rindex("target")
