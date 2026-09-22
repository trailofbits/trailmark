"""Behavioral tests for native compilation, cache freshness, and loading."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from tree_sitter import Language, Parser

from trailmark.tree_sitter_custom import verus as verus_mod


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_build_publishes_complete_output_atomically(tmp_path: Path, platform: str) -> None:
    output = tmp_path / "binding.so"
    output.write_bytes(b"previous complete binding")

    def compile_binding(cmd: list[str], **kwargs: object) -> None:
        assert cmd[0] == "cc"
        assert {Path(v).name for v in cmd if v.endswith(".c")} == {
            "binding.c",
            "parser.c",
            "scanner.c",
        }
        assert "-shared" in cmd
        assert "-fPIC" in cmd
        assert f"-I{verus_mod._DIR / 'src'}" in cmd
        assert f"-I{verus_mod.sysconfig.get_path('include')}" in cmd
        assert ("-undefined" in cmd) == (platform == "darwin")
        if platform == "darwin":
            assert cmd[cmd.index("-undefined") + 1] == "dynamic_lookup"
        assert kwargs == {"check": True, "capture_output": True, "text": True}
        temporary = Path(cmd[cmd.index("-o") + 1])
        assert temporary != output
        assert temporary.parent == output.parent
        assert temporary.is_file()
        temporary.write_bytes(b"partial")
        assert output.read_bytes() == b"previous complete binding"
        temporary.write_bytes(b"new complete binding")

    with (
        patch.object(subprocess, "run", side_effect=compile_binding) as compiler,
        patch.object(sys, "platform", platform),
    ):
        verus_mod._build(output)
    compiler.assert_called_once()
    assert output.read_bytes() == b"new complete binding"
    assert list(tmp_path.iterdir()) == [output]


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError("cc is unavailable"),
        PermissionError("compiler cannot execute"),
        subprocess.CalledProcessError(
            17, ["cc"], stderr="cannot write output: read-only filesystem"
        ),
    ],
)
def test_failed_build_preserves_previous_output_and_cleans_temporary(
    tmp_path: Path, error: Exception
) -> None:
    output = tmp_path / "binding.so"
    output.write_bytes(b"previous complete binding")

    def fail(cmd: list[str], **kwargs: object) -> None:
        Path(cmd[cmd.index("-o") + 1]).write_bytes(b"partial output")
        raise error

    expected = ImportError if isinstance(error, subprocess.CalledProcessError) else type(error)
    with patch.object(subprocess, "run", side_effect=fail), pytest.raises(expected) as caught:
        verus_mod._build(output)
    if isinstance(error, subprocess.CalledProcessError):
        assert "exit 17" in str(caught.value)
        assert "read-only filesystem" in str(caught.value)
    assert output.read_bytes() == b"previous complete binding"
    assert list(tmp_path.iterdir()) == [output]


def test_failed_publish_cleans_temporary(tmp_path: Path) -> None:
    output = tmp_path / "binding.so"
    with (
        patch.object(subprocess, "run"),
        patch.object(os, "replace", side_effect=PermissionError("read-only destination")),
        pytest.raises(PermissionError, match="read-only destination"),
    ):
        verus_mod._build(output)
    assert list(tmp_path.iterdir()) == []


def test_read_only_directory_does_not_invoke_compiler(tmp_path: Path) -> None:
    with (
        patch.object(
            verus_mod.tempfile, "NamedTemporaryFile", side_effect=PermissionError("read-only")
        ),
        patch.object(subprocess, "run") as compiler,
        pytest.raises(PermissionError, match="read-only"),
    ):
        verus_mod._build(tmp_path / "binding.so")
    compiler.assert_not_called()


@pytest.fixture
def grammar_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Use independent contents, not generated megabyte-sized C files, for cache tests.
    for relative in verus_mod._SOURCES:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"original {relative}")
    monkeypatch.setattr(verus_mod, "_DIR", tmp_path)
    return tmp_path


@pytest.mark.parametrize(
    "relative",
    [
        "binding.c",
        "src/parser.c",
        "src/scanner.c",
        "src/tree_sitter/alloc.h",
        "src/tree_sitter/array.h",
        "src/tree_sitter/parser.h",
    ],
)
def test_each_compiled_input_invalidates_binding_even_with_same_mtime(
    grammar_sources: Path, relative: str
) -> None:
    original = verus_mod._binding_path()
    source = grammar_sources / relative
    stat = source.stat()
    source.write_text("changed contents")
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    changed = verus_mod._binding_path()
    assert changed != original
    assert changed.parent == grammar_sources
    assert changed.suffix == original.suffix
    assert verus_mod._binding_path() == changed


def test_touching_unchanged_source_reuses_binding(grammar_sources: Path) -> None:
    original = verus_mod._binding_path()
    source = grammar_sources / "src/parser.c"
    os.utime(source, (1, 1))
    assert verus_mod._binding_path() == original


def test_build_configuration_invalidates_binding(grammar_sources: Path) -> None:
    original = verus_mod._binding_path()
    with patch.object(verus_mod.sysconfig, "get_config_var", return_value="different-python-abi"):
        assert verus_mod._binding_path() != original
    with patch.object(verus_mod.sysconfig, "get_platform", return_value="different-platform"):
        assert verus_mod._binding_path() != original
    with patch.object(verus_mod, "_compiler_flags", return_value=["cc", "-different-option"]):
        assert verus_mod._binding_path() != original


def test_cold_warm_and_changed_source_loading(grammar_sources: Path) -> None:
    capsule = object()
    module = SimpleNamespace(language=Mock(return_value=capsule))
    loader = Mock()
    spec = SimpleNamespace(loader=loader)
    with (
        patch.object(
            verus_mod, "_build", side_effect=lambda p: p.write_bytes(b"complete")
        ) as build,
        patch.object(
            verus_mod.importlib.util, "spec_from_file_location", return_value=spec
        ) as spec_for,
        patch.object(verus_mod.importlib.util, "module_from_spec", return_value=module),
    ):
        for _ in range(2):
            assert verus_mod.language() is capsule
        assert build.call_count == 1
        (grammar_sources / "src/scanner.c").write_text("new scanner")
        assert verus_mod.language() is capsule
    assert build.call_count == 2
    first, second = [call.args[0] for call in build.call_args_list]
    assert first != second
    assert [call.args for call in spec_for.call_args_list] == [
        ("_binding", str(first)),
        ("_binding", str(first)),
        ("_binding", str(second)),
    ]
    assert loader.exec_module.call_args_list == [((module,),)] * 3
    assert module.language.call_count == 3


@pytest.mark.parametrize("spec", [None, SimpleNamespace(loader=None)])
def test_missing_spec_or_loader_reports_binding_path(tmp_path: Path, spec: object) -> None:
    binding = tmp_path / "cached.so"
    binding.touch()
    with (
        patch.object(verus_mod, "_binding_path", return_value=binding),
        patch.object(verus_mod, "_build") as build,
        patch.object(verus_mod.importlib.util, "spec_from_file_location", return_value=spec),
        pytest.raises(ImportError, match=str(binding)),
    ):
        verus_mod.language()
    build.assert_not_called()


@pytest.mark.parametrize("module", [SimpleNamespace(), SimpleNamespace(language=None)])
def test_missing_entrypoint_is_an_actionable_load_error(tmp_path: Path, module: object) -> None:
    binding = tmp_path / "cached.so"
    binding.touch()
    with (
        patch.object(verus_mod, "_binding_path", return_value=binding),
        patch.object(verus_mod.importlib.util, "spec_from_file_location", return_value=Mock()),
        patch.object(verus_mod.importlib.util, "module_from_spec", return_value=module),
        pytest.raises(ImportError, match="no callable language entrypoint"),
    ):
        verus_mod.language()


def test_language_capsule_parses_verus_syntax() -> None:
    tree = Parser(Language(verus_mod.language())).parse(b"verus! { proof fn verified() {} }")
    assert not tree.root_node.has_error
    assert tree.root_node.named_children[0].type == "verus_block"
    assert tree.root_node.text is not None
    assert b"verified" in tree.root_node.text
