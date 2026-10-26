"""Compatibility recovery preserves valid PHP and genuine syntax diagnostics."""

from pathlib import Path

import pytest

from trailmark.parse import parse_file


@pytest.mark.parametrize(
    "declaration",
    [
        "public const TRUE = 1;\npublic const FALSE = 0;",
        "const true = 'yes';\nprotected const false = 'no';",
    ],
)
def test_class_boolean_constant_names(tmp_path: Path, declaration: str) -> None:
    file = tmp_path / "Config.php"
    file.write_text(
        "<?php\nclass Config {\n"
        + declaration
        + "\npublic function enabled() { return self::TRUE; }\n}\n"
    )
    graph = parse_file(str(file), strict=True)
    assert not graph.parse_diagnostics
    method = graph.nodes["Config:Config.enabled"]
    assert method.location.start_line == 5


def test_recovery_keeps_invalid_syntax_diagnostic(tmp_path: Path) -> None:
    file = tmp_path / "Config.php"
    file.write_text(
        "<?php\nclass Config {\npublic const TRUE = 1;\n"
        "public const FALSE = ;\npublic function good() {}\n}\n"
    )
    graph = parse_file(str(file))
    assert graph.parse_diagnostics
    assert any(d.location.start_line == 4 for d in graph.parse_diagnostics)
    assert "Config:Config.good" in graph.nodes


def test_comments_and_strings_do_not_trigger_recovery(tmp_path: Path) -> None:
    file = tmp_path / "Config.php"
    file.write_text(
        "<?php\n// class X { const TRUE = 1; }\n"
        "$text = 'class X { const FALSE = 0; }';\n"
        "function good() {}\n"
    )
    graph = parse_file(str(file), strict=True)
    assert "Config:good" in graph.nodes
