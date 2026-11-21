"""Behavioral tests for the shared native grammar loader."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from tree_sitter import Language, Parser

from trailmark.tree_sitter_custom import _native
from trailmark.tree_sitter_custom import verus as verus_mod
from trailmark.tree_sitter_custom._native import NativeGrammar, bundled_grammar


@pytest.mark.parametrize("suffix", [None, "", 1])
def test_extension_suffix_requires_a_native_python_abi(suffix: object) -> None:
    with (
        patch.object(_native.sysconfig, "get_config_var", return_value=suffix),
        pytest.raises(ImportError, match="native extension suffix"),
    ):
        _native.extension_suffix()


def test_extension_suffix_preserves_python_abi() -> None:
    with patch.object(_native.sysconfig, "get_config_var", return_value=".cpython-test.so"):
        assert _native.extension_suffix() == ".cpython-test.so"


@pytest.mark.parametrize("field", ["directory", "sources"])
def test_native_grammar_configuration_is_immutable(tmp_path: Path, field: str) -> None:
    grammar = NativeGrammar(tmp_path, ("binding.c", "src/parser.c"))
    with pytest.raises(FrozenInstanceError):
        setattr(grammar, field, None)


@pytest.mark.parametrize("name", ["circom", "masm", "move", "rust", "verus"])
def test_bundled_manifest_includes_every_compiled_source_and_header(name: str) -> None:
    grammar = bundled_grammar(name)
    actual = {
        p.relative_to(grammar.directory).as_posix()
        for p in grammar.directory.rglob("*")
        if p.suffix in {".c", ".h"}
    }
    assert set(grammar.sources) == actual | {"../binding.c", "../nested_comment.h"}


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
        assert f"-I{bundled_grammar('verus').directory / 'src'}" in cmd
        assert f"-I{_native.sysconfig.get_path('include')}" in cmd
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
        bundled_grammar("verus").build(output)
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
        bundled_grammar("verus").build(output)
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
        bundled_grammar("verus").build(output)
    assert list(tmp_path.iterdir()) == []


def test_read_only_directory_does_not_invoke_compiler(tmp_path: Path) -> None:
    with (
        patch.object(
            _native.tempfile, "NamedTemporaryFile", side_effect=PermissionError("read-only")
        ),
        patch.object(subprocess, "run") as compiler,
        pytest.raises(PermissionError, match="read-only"),
    ):
        bundled_grammar("verus").build(tmp_path / "binding.so")
    compiler.assert_not_called()


def test_missing_compiler_names_grammar_and_build_requirements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    with (
        patch.object(NativeGrammar, "binding_path", return_value=tmp_path / "binding.so"),
        pytest.raises(ImportError, match="bundled grammar 'rego'") as caught,
    ):
        _native.bundled_language("rego")
    assert isinstance(caught.value.__cause__, FileNotFoundError)
    assert "cc" in str(caught.value)
    assert "Python development headers" in str(caught.value)
    assert "write access" in str(caught.value)
    assert "docs/installation.md#bundled-grammars" in str(caught.value)
    assert list(tmp_path.iterdir()) == []


def test_read_only_install_names_grammar_and_retains_cause(tmp_path: Path) -> None:
    with (
        patch.object(NativeGrammar, "binding_path", return_value=tmp_path / "binding.so"),
        patch.object(
            _native.tempfile, "NamedTemporaryFile", side_effect=PermissionError("read-only")
        ),
        pytest.raises(ImportError, match="bundled grammar 'cairo'") as caught,
    ):
        _native.bundled_language("cairo")
    assert isinstance(caught.value.__cause__, PermissionError)
    assert "read-only" in str(caught.value)
    assert "write access" in str(caught.value)


@pytest.fixture
def grammar_sources(tmp_path: Path) -> NativeGrammar:
    # Use independent contents, not generated megabyte-sized C files, for cache tests.
    directory = tmp_path / "grammar"
    for relative in bundled_grammar("verus").sources:
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"original {relative}")
    return NativeGrammar(directory, bundled_grammar("verus").sources, "tree_sitter_verus")


@pytest.mark.parametrize(
    "relative",
    [
        "../binding.c",
        "../nested_comment.h",
        "src/parser.c",
        "src/scanner.c",
        "src/tree_sitter/alloc.h",
        "src/tree_sitter/array.h",
        "src/tree_sitter/parser.h",
    ],
)
def test_each_compiled_input_invalidates_binding_even_with_same_mtime(
    grammar_sources: NativeGrammar, relative: str
) -> None:
    original = grammar_sources.binding_path()
    source = grammar_sources.directory / relative
    stat = source.stat()
    source.write_text("changed contents")
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    changed = grammar_sources.binding_path()
    assert changed != original
    assert changed.parent == grammar_sources.directory
    assert changed.suffix == original.suffix
    assert grammar_sources.binding_path() == changed


def test_touching_unchanged_source_reuses_binding(grammar_sources: NativeGrammar) -> None:
    original = grammar_sources.binding_path()
    source = grammar_sources.directory / "src/parser.c"
    os.utime(source, (1, 1))
    assert grammar_sources.binding_path() == original


def test_build_configuration_invalidates_binding(grammar_sources: NativeGrammar) -> None:
    original = grammar_sources.binding_path()
    with patch.object(_native.sysconfig, "get_config_var", return_value="different-python-abi"):
        assert grammar_sources.binding_path() != original
    with patch.object(_native.sysconfig, "get_platform", return_value="different-platform"):
        assert grammar_sources.binding_path() != original
    with patch.object(NativeGrammar, "compiler_flags", return_value=["cc", "-different-option"]):
        assert grammar_sources.binding_path() != original


def test_cold_warm_and_changed_source_loading(grammar_sources: NativeGrammar) -> None:
    capsule = object()
    module = SimpleNamespace(language=Mock(return_value=capsule))
    loader = Mock()
    spec = SimpleNamespace(loader=loader)
    with (
        patch.object(
            NativeGrammar, "build", side_effect=lambda p: p.write_bytes(b"complete")
        ) as build,
        patch.object(
            _native.importlib.util, "spec_from_file_location", return_value=spec
        ) as spec_for,
        patch.object(_native.importlib.util, "module_from_spec", return_value=module),
    ):
        for _ in range(2):
            assert grammar_sources.language() is capsule
        assert build.call_count == 1
        (grammar_sources.directory / "src/scanner.c").write_text("new scanner")
        assert grammar_sources.language() is capsule
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
        patch.object(NativeGrammar, "binding_path", return_value=binding),
        patch.object(NativeGrammar, "build") as build,
        patch.object(_native.importlib.util, "spec_from_file_location", return_value=spec),
        pytest.raises(ImportError, match=str(binding)),
    ):
        verus_mod.language()
    build.assert_not_called()


@pytest.mark.parametrize("module", [SimpleNamespace(), SimpleNamespace(language=None)])
def test_missing_entrypoint_is_an_actionable_load_error(tmp_path: Path, module: object) -> None:
    binding = tmp_path / "cached.so"
    binding.touch()
    with (
        patch.object(NativeGrammar, "binding_path", return_value=binding),
        patch.object(_native.importlib.util, "spec_from_file_location", return_value=Mock()),
        patch.object(_native.importlib.util, "module_from_spec", return_value=module),
        pytest.raises(ImportError, match="no callable language entrypoint"),
    ):
        verus_mod.language()


def test_language_capsule_parses_verus_syntax() -> None:
    tree = Parser(Language(verus_mod.language())).parse(b"verus! { proof fn verified() {} }")
    assert not tree.root_node.has_error
    assert tree.root_node.named_children[0].type == "verus_block"
    assert tree.root_node.text is not None
    assert b"verified" in tree.root_node.text


@pytest.mark.parametrize(
    ("name", "source", "declaration"),
    [
        ("circom", b"template Circuit() { signal input value; }", "template_definition"),
        ("verus", b"verus! { proof fn verified() {} }", "verus_block"),
    ],
)
def test_shared_binding_loads_the_requested_grammar(
    name: str, source: bytes, declaration: str
) -> None:
    tree = Parser(Language(_native.bundled_language(name))).parse(source)
    assert not tree.root_node.has_error
    assert tree.root_node.named_children[0].type == declaration
    assert tree.root_node.text == source


@pytest.mark.parametrize("name", ["", "../circom", "circom/../verus", "with-dash"])
def test_shared_binding_rejects_invalid_grammar_names(name: str) -> None:
    with pytest.raises(ValueError, match="Invalid bundled grammar name"):
        _native.bundled_language(name)


def test_shared_binding_reports_missing_generated_sources(tmp_path: Path) -> None:
    with (
        patch.object(_native, "__file__", str(tmp_path / "_native.py")),
        pytest.raises(ImportError, match="unbuilt is not generated; run npm ci and uv sync"),
    ):
        _native.bundled_language("unbuilt")


@pytest.mark.parametrize("slash_comments", [False, True])
def test_shared_scanner_nests_comments_and_has_no_serialized_state(
    tmp_path: Path, slash_comments: bool
) -> None:
    grammar_root = Path(_native.__file__).parent
    fixture = Path(__file__).parent / "fixtures/nested_comment_scanner.c"
    executable = tmp_path / "scanner"
    flags = (
        ["-DTRAILMARK_SLASH_TEST", "-DTRAILMARK_COMMENT_OPEN='/'", "-DTRAILMARK_COMMENT_CLOSE='/'"]
        if slash_comments
        else ["-DTRAILMARK_COMMENT_OPERATOR"]
    )
    compiler = shutil.which("cc")
    assert compiler is not None
    subprocess.run(  # noqa: S603
        [
            compiler,
            "-std=c11",
            f"-I{grammar_root}",
            f"-I{grammar_root / 'verus/src'}",
            *flags,
            str(fixture),
            "-o",
            str(executable),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run([str(executable)], check=True, capture_output=True)  # noqa: S603


def test_precompilation_discovers_all_generated_grammars(tmp_path: Path) -> None:
    for name in ("legacy", "shared"):
        directory = tmp_path / name / "src"
        directory.mkdir(parents=True)
        (directory / "parser.c").write_text("generated")
    (tmp_path / "legacy/__init__.py").write_text("")
    (tmp_path / "unbuilt").mkdir()
    with (
        patch.object(_native, "__file__", str(tmp_path / "_native.py")),
        patch.object(_native, "bundled_language") as shared,
    ):
        _native.precompile_all()
    assert [call.args for call in shared.call_args_list] == [("legacy",), ("shared",)]
