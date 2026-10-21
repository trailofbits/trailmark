"""Tests for the public parse-only API."""

from __future__ import annotations

from pathlib import Path

import pytest

from trailmark.models.nodes import NodeKind
from trailmark.parse import (
    detect_languages,
    parse_directory,
    parse_file,
    supported_languages,
)


class TestSupportedLanguages:
    def test_includes_common_languages(self) -> None:
        languages = supported_languages()
        assert isinstance(languages, tuple)
        assert "python" in languages
        assert "rust" in languages
        assert "typescript" in languages


class TestParseFile:
    def test_parse_file_infers_language_from_extension(self, tmp_path: Path) -> None:
        path = tmp_path / "auth.py"
        path.write_text("def login(user: str) -> bool:\n    return True\n")

        graph = parse_file(str(path))

        assert graph.language == "python"
        assert graph.root_path == str(path)
        assert "auth:login" in graph.nodes
        assert graph.nodes["auth:login"].kind == NodeKind.FUNCTION

    def test_parse_file_accepts_explicit_language(self, tmp_path: Path) -> None:
        path = tmp_path / "lib.rs"
        path.write_text("fn check() -> bool { true }\n")

        graph = parse_file(str(path), language="rust")

        assert graph.language == "rust"
        assert "lib:check" in graph.nodes

    def test_parse_file_rejects_unknown_extension_without_language(self, tmp_path: Path) -> None:
        path = tmp_path / "build.foo"
        path.write_text("whatever\n")

        with pytest.raises(ValueError, match="Could not infer supported language"):
            parse_file(str(path))

    def test_parse_file_rejects_multi_language_spec(self, tmp_path: Path) -> None:
        path = tmp_path / "auth.py"
        path.write_text("def login():\n    pass\n")

        with pytest.raises(ValueError, match="Single-file parse requires one language"):
            parse_file(str(path), language="python,rust")


class TestParseDirectory:
    @pytest.mark.parametrize("language", ["python", "auto", "python,rust"])
    @pytest.mark.parametrize("allow_empty", [False, True])
    def test_invalid_root_is_rejected(
        self, tmp_path: Path, language: str, allow_empty: bool
    ) -> None:
        missing = tmp_path / "missing"
        file = tmp_path / "file.py"
        file.write_text("")
        for path in (missing, file):
            with pytest.raises(ValueError, match="Not a directory"):
                parse_directory(str(path), language, allow_empty=allow_empty)

    def test_wrong_language_requires_explicit_empty_opt_in(self, tmp_path: Path) -> None:
        (tmp_path / "app.rs").write_text("fn main() {}")
        with pytest.raises(ValueError, match="No source files inspected.*python"):
            parse_directory(str(tmp_path))
        assert parse_directory(str(tmp_path), allow_empty=True).source_file_counts == {"python": 0}

    def test_allow_empty_auto(self, tmp_path: Path) -> None:
        graph = parse_directory(str(tmp_path), "auto", allow_empty=True)
        assert graph.nodes == {}
        assert graph.source_file_counts == {}

    def test_counts_inspected_files_without_declarations(self, tmp_path: Path) -> None:
        (tmp_path / "empty.py").write_text("")
        (tmp_path / "comments.PY").write_text("# no declarations\n")
        (tmp_path / "ignored.rs").write_text("fn main() {}")
        vendor = tmp_path / "vendor"
        vendor.mkdir()
        (vendor / "excluded.py").write_text("")
        graph = parse_directory(str(tmp_path), "python,go")
        assert graph.source_file_counts == {"python": 2, "go": 0}
        assert parse_file(str(tmp_path / "empty.py")).source_file_counts == {"python": 1}

    def test_counts_owned_headers_once_and_uses_public_language_keys(self, tmp_path: Path) -> None:
        (tmp_path / "shared.h").write_text("void helper(void);")
        (tmp_path / "empty.cs").write_text("")
        graph = parse_directory(str(tmp_path), "c,objc,c_sharp")
        assert graph.source_file_counts == {"c": 0, "objc": 1, "c_sharp": 1}

    def test_parse_directory_single_language(self, tmp_path: Path) -> None:
        path = tmp_path / "main.py"
        path.write_text("def main():\n    pass\n")

        graph = parse_directory(str(tmp_path), language="python")

        assert graph.language == "python"
        assert "main:main" in graph.nodes

    def test_parse_directory_auto_merges_languages(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("def main():\n    pass\n")
        (tmp_path / "lib.rs").write_text("fn check() {}\n")

        graph = parse_directory(str(tmp_path), language="auto")

        assert graph.language == "polyglot"
        assert "main:main" in graph.nodes
        assert "lib:check" in graph.nodes

    def test_auto_detection_and_parsing_agree_on_uppercase_extensions(self, tmp_path: Path) -> None:
        (tmp_path / "app.PY").write_text("def run():\n    pass\n")
        (tmp_path / "lib.Rs").write_text("fn check() {}\n")

        assert detect_languages(str(tmp_path)) == ["python", "rust"]
        graph = parse_directory(str(tmp_path), language="auto")

        assert "app:run" in graph.nodes
        assert "lib:check" in graph.nodes

    @pytest.mark.parametrize("spec", ["python,python", " python, ,python,", " python "])
    def test_repeated_language_does_not_duplicate_graph(self, tmp_path: Path, spec: str) -> None:
        (tmp_path / "app.py").write_text("def run():\n    helper()\ndef helper():\n    pass\n")

        expected = parse_directory(str(tmp_path), language="python")

        assert parse_directory(str(tmp_path), language=spec) == expected

    @pytest.mark.parametrize("spec", ["", " ", ",,,", " , , "])
    def test_empty_language_list_raises(self, tmp_path: Path, spec: str) -> None:
        with pytest.raises(ValueError, match="At least one language"):
            parse_directory(str(tmp_path), language=spec)

    def test_detect_languages_is_public(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x = 1\n")
        (tmp_path / "b.rs").write_text("fn x() {}\n")

        assert detect_languages(str(tmp_path)) == ["python", "rust"]

    def test_detect_languages_uses_shared_skip_rules(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("x = 1\n")
        vendor = tmp_path / "vendor"
        vendor.mkdir()
        (vendor / "dep.go").write_text("package dep\n")
        cache = tmp_path / ".mypy_cache"
        cache.mkdir()
        (cache / "stub.py").write_text("x = 1\n")

        assert detect_languages(str(tmp_path)) == ["python"]

    def test_detect_languages_matches_javascript_parser_extensions(self, tmp_path: Path) -> None:
        (tmp_path / "module.mjs").write_text("export default {};\n")
        (tmp_path / "module.cjs").write_text("module.exports = {};\n")

        # The JavaScript parser walks .mjs/.cjs as well as .js/.jsx, so
        # detect_languages should mirror that and report "javascript".
        assert detect_languages(str(tmp_path)) == ["javascript"]

    def test_detect_languages_matches_new_parser_extensions(self, tmp_path: Path) -> None:
        (tmp_path / "a.move").write_text("")
        (tmp_path / "b.tact").write_text("")
        (tmp_path / "c.fc").write_text("")
        (tmp_path / "d.func").write_text("")
        (tmp_path / "e.sw").write_text("")
        (tmp_path / "f.rego").write_text("")
        (tmp_path / "g.proto").write_text("")
        (tmp_path / "h.thrift").write_text("")
        (tmp_path / "i.graphql").write_text("")
        (tmp_path / "j.gql").write_text("")

        detected = detect_languages(str(tmp_path))

        assert "move" in detected
        assert "tact" in detected
        assert "func" in detected
        assert "sway" in detected
        assert "rego" in detected
        assert "proto" in detected
        assert "thrift" in detected
        assert "graphql" in detected


class TestFileExtensionHelper:
    """Direct coverage of trailmark.parse._file_extension."""

    def test_returns_lowercased_extension(self) -> None:
        from trailmark.parse import _file_extension

        assert _file_extension("auth.py") == ".py"

    def test_lowercases_uppercase_extension(self) -> None:
        """Kills the .upper() / case-preserving mutations."""
        from trailmark.parse import _file_extension

        assert _file_extension("README.MD") == ".md"
        assert _file_extension("Foo.PY") == ".py"

    def test_picks_rightmost_dot(self) -> None:
        """Kills the rfind→find mutation: must take the LAST dot."""
        from trailmark.parse import _file_extension

        assert _file_extension("archive.tar.gz") == ".gz"
        assert _file_extension("a.b.c.d.py") == ".py"

    def test_no_extension_returns_empty_string(self) -> None:
        """Kills the '' → 'XXXX' mutation."""
        from trailmark.parse import _file_extension

        assert _file_extension("README") == ""
        assert _file_extension("Makefile") == ""

    def test_dotfile_has_extension_starting_at_zero(self) -> None:
        """Kills the `dot < 0` → `dot <= 0` and `dot < 1` mutations.

        For ".hidden", rfind returns 0; the guard must be `< 0`, not `<= 0`,
        so the function returns ".hidden" (the leading dot IS the extension
        boundary), not "".
        """
        from trailmark.parse import _file_extension

        assert _file_extension(".hidden") == ".hidden"
        assert _file_extension(".bashrc") == ".bashrc"


class TestResolveFileLanguage:
    """Direct coverage of trailmark.parse._resolve_file_language."""

    def test_auto_keyword_triggers_detection(self, tmp_path: Path) -> None:
        """Kills the 'auto' / 'AUTO' / 'XXautoXX' string mutations."""
        path = tmp_path / "main.py"
        path.write_text("def main():\n    pass\n")

        graph = parse_file(str(path), language="auto")
        assert graph.language == "python"

    def test_unsupported_language_error_message_names_language(self, tmp_path: Path) -> None:
        """Kills the `msg = None` and `raise ValueError(None)` mutations."""
        path = tmp_path / "anything.py"
        path.write_text("x = 1\n")
        with pytest.raises(ValueError, match="Unsupported language: notalang"):
            parse_file(str(path), language="notalang")
