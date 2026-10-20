"""C struct references preserve definitions and opaque declarations."""

from pathlib import Path

import pytest

from trailmark.models.edges import EdgeKind
from trailmark.parsers.c.parser import CParser


@pytest.mark.parametrize(
    "reference",
    [
        "static struct ctx global_;",
        "struct ctx *make(void);",
        "struct ctx;",
        "typedef struct ctx Ctx;",
    ],
)
def test_struct_references_preserve_definition_location(tmp_path: Path, reference: str) -> None:
    path = tmp_path / "structs.c"
    path.write_text(f"struct ctx {{\n int value;\n}};\n{reference}\n")

    graph = CParser().parse_file(str(path))

    unit = graph.nodes["structs:ctx"]
    assert (unit.location.start_line, unit.location.end_line) == (1, 3)
    contains = [e for e in graph.edges if e.kind == EdgeKind.CONTAINS and e.target_id == unit.id]
    assert len(contains) == 1


@pytest.mark.parametrize("defined", [False, True])
def test_opaque_struct_is_retained_and_upgraded_once(tmp_path: Path, *, defined: bool) -> None:
    path = tmp_path / "structs.c"
    source = "struct ctx;\nstruct ctx *make(void);\n"
    if defined:
        source += "struct ctx {int value;};\nstruct ctx;\n"
    path.write_text(source)

    graph = CParser().parse_file(str(path))

    unit = graph.nodes["structs:ctx"]
    assert unit.location.start_line == (3 if defined else 1)
    assert (
        len([e for e in graph.edges if e.kind == EdgeKind.CONTAINS and e.target_id == unit.id]) == 1
    )
