"""Builds regenerate stale Verus sources and reuse self-contained distributions."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

import hatch_build as build


@pytest.fixture
def source_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Mock]:
    grammar = tmp_path / build.GRAMMAR_PATH
    grammar.mkdir(parents=True)
    (grammar / "grammar.js").write_text("original grammar")
    (grammar / "tree-sitter.json").write_text("{}")

    def generate(command: list[str], *, cwd: Path, check: bool) -> None:
        for name in build.GENERATED_FILES:
            path = cwd / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"generated {name}")

    runner = Mock(side_effect=generate)
    monkeypatch.setattr(build, "_generator", Mock(return_value=Path("/tree-sitter")))
    monkeypatch.setattr(build.subprocess, "run", runner)
    return tmp_path, runner


def test_bundled_generated_sources_need_no_generator(source_tree: tuple[Path, Mock]) -> None:
    root, runner = source_tree
    build.generate_verus(root)
    with patch.object(build, "_generator", side_effect=AssertionError("generator unavailable")):
        build.generate_verus(root)
    assert runner.call_count == 1
    assert all((root / build.GRAMMAR_PATH / name).is_file() for name in build.GENERATED_FILES)


@pytest.mark.parametrize(
    "name",
    [
        "grammar.js",
        "tree-sitter.json",
        "src/parser.c",
        "src/tree_sitter/parser.h",
        ".generated.json",
    ],
)
def test_changed_sources_or_damaged_artifacts_regenerate(
    source_tree: tuple[Path, Mock], name: str
) -> None:
    root, runner = source_tree
    build.generate_verus(root)
    (root / build.GRAMMAR_PATH / name).write_text("changed")
    build.generate_verus(root)
    build.generate_verus(root)
    assert runner.call_count == 2


def test_missing_generated_header_regenerates(source_tree: tuple[Path, Mock]) -> None:
    root, runner = source_tree
    build.generate_verus(root)
    (root / build.GRAMMAR_PATH / "src/tree_sitter/alloc.h").unlink()
    build.generate_verus(root)
    assert runner.call_count == 2


def test_failed_generation_preserves_previous_sources(source_tree: tuple[Path, Mock]) -> None:
    root, runner = source_tree
    build.generate_verus(root)
    grammar = root / build.GRAMMAR_PATH
    before = {name: (grammar / name).read_bytes() for name in build.GENERATED_FILES}
    stamp = (grammar / ".generated.json").read_bytes()
    (grammar / "grammar.js").write_text("changed grammar")
    runner.side_effect = subprocess.CalledProcessError(1, "tree-sitter")
    with pytest.raises(subprocess.CalledProcessError):
        build.generate_verus(root)
    assert before == {name: (grammar / name).read_bytes() for name in build.GENERATED_FILES}
    assert (grammar / ".generated.json").read_bytes() == stamp


def test_checkout_without_generator_reports_setup_command(tmp_path: Path) -> None:
    with (
        patch.object(build.shutil, "which", return_value=None),
        pytest.raises(RuntimeError, match="npm ci"),
    ):
        build._generator(tmp_path)


def test_wrong_generator_version_is_rejected(tmp_path: Path) -> None:
    with (
        patch.object(build.shutil, "which", return_value="/tree-sitter"),
        patch.object(
            build.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0, "tree-sitter 0.26.0\n"),
        ),
        pytest.raises(RuntimeError, match=build.GENERATOR_VERSION),
    ):
        build._generator(tmp_path)


def test_generator_version_matches_locked_build_dependency() -> None:
    root = Path(__file__).parents[1]
    package = json.loads((root / "package.json").read_text())
    lock = json.loads((root / "package-lock.json").read_text())
    assert package["devDependencies"]["tree-sitter-cli"] == build.GENERATOR_VERSION
    assert lock["packages"]["node_modules/tree-sitter-cli"]["version"] == build.GENERATOR_VERSION
