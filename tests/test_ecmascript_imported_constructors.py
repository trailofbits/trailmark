"""Imported construction retains its site and reaches an inspected constructor."""

from pathlib import Path

import pytest

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.nodes import NodeOrigin
from trailmark.parse import parse_directory, parse_file


@pytest.mark.parametrize("language,extension", [("javascript", ".js"), ("typescript", ".ts")])
def test_named_class_construction_reaches_its_constructor(
    tmp_path: Path, language: str, extension: str
) -> None:
    (tmp_path / f"widget{extension}").write_text(
        "export class Widget { constructor(value) { sink(value); } }\n"
    )
    (tmp_path / f"decoy{extension}").write_text("export class Widget { constructor() {} }\n")
    (tmp_path / f"make{extension}").write_text(
        "import { Widget as Selected } from './widget';\n"
        "export function make() { return new Selected(input()); }\n"
    )
    graph = parse_directory(str(tmp_path), language)
    construction = next(e for e in graph.edges if dict(e.attributes).get("constructor_call"))
    assert construction.source_id == "make:make"
    assert construction.target_id == "widget:Widget.constructor"
    assert construction.confidence == EdgeConfidence.INFERRED
    assert construction.location is not None
    assert (construction.location.start_line, construction.location.start_col) == (2, 32)
    assert construction.location.end_col == 53
    assert any(
        e.source_id == construction.target_id for e in graph.edges if e.kind == EdgeKind.CALLS
    )
    assert any(
        e.source_id == "make:make" and graph.nodes[e.target_id].name.endswith(":input")
        for e in graph.edges
        if e.kind == EdgeKind.CALLS
    )


@pytest.mark.parametrize("language,extension", [("javascript", ".js"), ("typescript", ".ts")])
@pytest.mark.parametrize(
    "declaration,target",
    [("export class Widget {}", "widget:Widget"), ("export function Widget() {}", "widget:Widget")],
)
def test_imported_implicit_class_or_function_keeps_exact_target(
    tmp_path: Path, language: str, extension: str, declaration: str, target: str
) -> None:
    (tmp_path / f"widget{extension}").write_text(declaration + "\n")
    (tmp_path / f"make{extension}").write_text(
        "import { Widget } from './widget'; function make() { return new Widget(); }\n"
    )
    graph = parse_directory(str(tmp_path), language)
    construction = next(e for e in graph.edges if dict(e.attributes).get("constructor_call"))
    assert construction.target_id == target
    assert graph.nodes[construction.target_id].origin == NodeOrigin.SOURCE


@pytest.mark.parametrize("language,extension", [("javascript", ".js"), ("typescript", ".ts")])
@pytest.mark.parametrize(
    "binding,parameters,expression",
    [
        ("import { Widget } from './widget';", "Widget", "Widget"),
        ("import Widget from './widget';", "", "Widget"),
        ("import * as ns from './widget';", "", "ns.Widget"),
    ],
)
def test_unproven_imported_construction_remains_proxy(
    tmp_path: Path,
    language: str,
    extension: str,
    binding: str,
    parameters: str,
    expression: str,
) -> None:
    (tmp_path / f"widget{extension}").write_text("export class Widget { constructor() {} }\n")
    (tmp_path / f"make{extension}").write_text(
        f"{binding}\nfunction make({parameters}) {{ return new {expression}(); }}\n"
    )
    graph = parse_directory(str(tmp_path), language)
    construction = next(e for e in graph.edges if dict(e.attributes).get("constructor_call"))
    assert graph.nodes[construction.target_id].origin == NodeOrigin.PROXY


def test_type_only_import_cannot_bind_a_constructor(tmp_path: Path) -> None:
    (tmp_path / "widget.ts").write_text("export class Widget { constructor() {} }\n")
    (tmp_path / "make.ts").write_text(
        "import type { Widget } from './widget'; function make() { return new Widget(); }\n"
    )
    graph = parse_directory(str(tmp_path), "typescript")
    construction = next(e for e in graph.edges if dict(e.attributes).get("constructor_call"))
    assert graph.nodes[construction.target_id].origin == NodeOrigin.PROXY
    assert dict(construction.attributes)["binding_blocked"]


def test_mixed_language_import_binds_after_both_frontends_merge(tmp_path: Path) -> None:
    (tmp_path / "widget.js").write_text("export class Widget { constructor() { sink(); } }\n")
    (tmp_path / "make.ts").write_text(
        "import { Widget } from './widget.js'; function make() { return new Widget(); }\n"
    )
    graph = parse_directory(str(tmp_path), "javascript,typescript")
    construction = next(e for e in graph.edges if dict(e.attributes).get("constructor_call"))
    assert construction.target_id == "widget:Widget.constructor"


@pytest.mark.parametrize("language,extension", [("javascript", ".js"), ("typescript", ".ts")])
def test_single_file_import_retains_a_proxy_without_inspected_context(
    tmp_path: Path, language: str, extension: str
) -> None:
    (tmp_path / f"widget{extension}").write_text("export class Widget { constructor() {} }\n")
    source = tmp_path / f"make{extension}"
    source.write_text(
        "import { Widget } from './widget'; function make() { return new Widget(); }\n"
    )
    graph = parse_file(str(source), language)
    construction = next(e for e in graph.edges if dict(e.attributes).get("constructor_call"))
    assert graph.nodes[construction.target_id].origin == NodeOrigin.PROXY


@pytest.mark.parametrize("language,extension", [("javascript", ".js"), ("typescript", ".ts")])
@pytest.mark.parametrize("imported", [True, False])
def test_static_method_named_constructor_is_not_a_construction_body(
    tmp_path: Path, language: str, extension: str, imported: bool
) -> None:
    declaration = "export class Widget { static constructor() { wrong(); } }\n"
    source = tmp_path / f"make{extension}"
    if imported:
        (tmp_path / f"widget{extension}").write_text(declaration)
        source.write_text(
            "import { Widget } from './widget'; function make() { return new Widget(); }\n"
        )
        graph = parse_directory(str(tmp_path), language)
        expected = "widget:Widget"
    else:
        source.write_text(declaration + "function make() { return new Widget(); }\n")
        graph = parse_file(str(source), language)
        expected = "make:Widget"
    construction = next(e for e in graph.edges if dict(e.attributes).get("constructor_call"))
    assert construction.target_id == expected


@pytest.mark.parametrize("language,extension", [("javascript", ".js"), ("typescript", ".ts")])
def test_static_name_does_not_hide_the_actual_constructor(
    tmp_path: Path, language: str, extension: str
) -> None:
    (tmp_path / f"widget{extension}").write_text(
        "export class Widget {\n static constructor() { wrong(); }\n"
        " constructor() { right(); }\n}\n"
    )
    (tmp_path / f"make{extension}").write_text(
        "import { Widget } from './widget'; function make() { return new Widget(); }\n"
    )
    graph = parse_directory(str(tmp_path), language)
    construction = next(e for e in graph.edges if dict(e.attributes).get("constructor_call"))
    target = graph.nodes[construction.target_id]
    assert target.location.start_line == 3
    assert not dict(target.attributes).get("ecmascript_static")
