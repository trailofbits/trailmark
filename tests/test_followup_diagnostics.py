"""Strict-mode coverage, diagnostic scaling, and Python lambda call regressions."""

from __future__ import annotations

from pathlib import Path

import pytest

from trailmark.models import EdgeKind
from trailmark.models.diagnostics import ParseDiagnostic
from trailmark.parse import parse_directory, parse_file


@pytest.mark.parametrize(
    "text", ["", " \n\t", "# hello\n", "<# hello\nthere #>", "# one\n<# two #>\n"]
)
def test_empty_or_comment_only_powershell_is_valid(tmp_path: Path, text: str) -> None:
    source = tmp_path / "empty.ps1"
    source.write_text(text)
    assert parse_file(str(source), strict=True).parse_diagnostics == []
    assert parse_directory(str(tmp_path), "powershell", strict=True).parse_diagnostics == []


@pytest.mark.parametrize("text", ["", "\n# empty schema\n"])
def test_empty_or_comment_only_graphql_is_valid(tmp_path: Path, text: str) -> None:
    source = tmp_path / "empty.graphql"
    source.write_text(text)
    assert parse_file(str(source), strict=True).parse_diagnostics == []


def test_python_lambda_calls_keep_their_enclosing_owner(tmp_path: Path) -> None:
    source = tmp_path / "callbacks.py"
    source.write_text(
        "def validate(i): return i\ndef cb(): pass\n"
        "def handler(items): return sorted(items, key=lambda i: validate(i))\n"
        "def reg(app): app.add(lambda: cb())\n"
    )
    graph = parse_file(str(source), strict=True)
    calls = {(e.source_id, e.target_id) for e in graph.edges if e.kind == EdgeKind.CALLS}
    assert ("callbacks:handler", "callbacks:validate") in calls
    assert ("callbacks:reg", "callbacks:cb") in calls


def test_skipped_julia_macro_is_visible_to_strict_mode(tmp_path: Path) -> None:
    source = tmp_path / "macro.jl"
    source.write_text("@unknown function hidden()\n helper()\nend\n")
    diagnostics = parse_file(str(source), "julia").parse_diagnostics
    assert any(d.kind == "unsupported_syntax" for d in diagnostics)
    with pytest.raises(ValueError, match="diagnostic"):
        parse_file(str(source), "julia", strict=True)


def test_tsx_diagnostics_name_the_actual_grammar(tmp_path: Path) -> None:
    source = tmp_path / "broken.tsx"
    source.write_text("function broken( { <div>\n")
    diagnostics = parse_file(str(source), "typescript").parse_diagnostics
    assert diagnostics
    assert all(d.language == "typescript" and d.grammar == "tsx" for d in diagnostics)


def test_diagnostic_comparisons_do_not_grow_with_all_preceding_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    comparisons = 0
    original = ParseDiagnostic.__eq__

    def compare(self: ParseDiagnostic, other: object) -> bool:
        nonlocal comparisons
        comparisons += 1
        return original(self, other)

    monkeypatch.setattr(ParseDiagnostic, "__eq__", compare)
    for number in range(16):
        (tmp_path / f"broken{number}.py").write_text("def bad(:\n    pass\n" * 32)
    graph = parse_directory(str(tmp_path), "python")
    assert len(graph.parse_diagnostics) >= 512
    assert comparisons < len(graph.parse_diagnostics) * 4
