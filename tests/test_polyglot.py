"""Tests for multi-language (polyglot) graph building.

``QueryEngine.from_directory(path, language="auto")`` walks the tree,
detects every supported language with at least one matching file, and
merges the resulting graphs. Explicit ``"python,rust"``-style lists
are also supported.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trailmark.query.api import QueryEngine, detect_languages


class TestDetectLanguages:
    def test_single_language_directory(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x = 1\n")
        assert detect_languages(str(tmp_path)) == ["python"]

    def test_multiple_languages_in_one_dir(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x = 1\n")
        (tmp_path / "b.rs").write_text("fn x() {}\n")
        (tmp_path / "c.sol").write_text("contract X {}\n")
        detected = set(detect_languages(str(tmp_path)))
        assert detected == {"python", "rust", "solidity"}

    def test_empty_directory_returns_empty(self, tmp_path: Path) -> None:
        assert detect_languages(str(tmp_path)) == []

    def test_unknown_extensions_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# hello\n")
        (tmp_path / "data.json").write_text("{}\n")
        assert detect_languages(str(tmp_path)) == []

    def test_skips_vendor_directories(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("x = 1\n")
        vendor = tmp_path / "node_modules" / "dep"
        vendor.mkdir(parents=True)
        (vendor / "thing.js").write_text("export default {};\n")
        detected = detect_languages(str(tmp_path))
        assert detected == ["python"], detected

    def test_missing_path_returns_empty(self, tmp_path: Path) -> None:
        assert detect_languages(str(tmp_path / "does-not-exist")) == []


class TestFromDirectoryAuto:
    def test_auto_detects_and_merges(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text(
            "def handler():\n    pass\n",
        )
        (tmp_path / "Vault.sol").write_text(
            "// SPDX-License-Identifier: MIT\n"
            "pragma solidity ^0.8.0;\n"
            "contract Vault {\n"
            "    function withdraw(uint256 amount) external {}\n"
            "}\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path), language="auto")
        summary = engine.summary()
        # Should have found nodes from both languages.
        assert summary["total_nodes"] >= 2

    def test_auto_merges_entrypoints(self, tmp_path: Path) -> None:
        """Detected entrypoints from different languages coexist."""
        (tmp_path / "cli.py").write_text("def main():\n    pass\n")
        (tmp_path / "Vault.sol").write_text(
            "// SPDX-License-Identifier: MIT\n"
            "pragma solidity ^0.8.0;\n"
            "contract Vault {\n"
            "    function withdraw() external {}\n"
            "}\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path), language="auto")
        surface = engine.attack_surface()
        descriptions = [ep.get("description") or "" for ep in surface]
        assert any("main" in d.lower() for d in descriptions), surface
        assert any("solidity" in d.lower() for d in descriptions), surface

    def test_auto_on_empty_dir_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="No supported languages"):
            QueryEngine.from_directory(str(tmp_path), language="auto")

    def test_explicit_list_merges(self, tmp_path: Path) -> None:
        """`python,rust` builds and merges both, skipping other languages."""
        (tmp_path / "a.py").write_text("def main():\n    pass\n")
        (tmp_path / "b.rs").write_text("fn main() {}\n")
        (tmp_path / "ignored.sol").write_text(
            "contract X { function y() external {} }\n",
        )
        engine = QueryEngine.from_directory(
            str(tmp_path),
            language="python,rust",
        )
        summary = engine.summary()
        # Solidity was explicitly excluded, so no contract nodes should
        # appear.
        surface_descriptions = [
            (ep.get("description") or "").lower() for ep in engine.attack_surface()
        ]
        assert not any("solidity" in d for d in surface_descriptions)
        # But python and rust mains should both be detected.
        assert summary["entrypoints"] >= 2

    def test_unsupported_language_raises(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x = 1\n")
        with pytest.raises(ValueError, match="Unsupported language"):
            QueryEngine.from_directory(str(tmp_path), language="cobol")

    def test_single_language_preserved(self, tmp_path: Path) -> None:
        """Pre-polyglot behavior intact when one language is specified."""
        (tmp_path / "a.py").write_text("def main():\n    pass\n")
        engine = QueryEngine.from_directory(str(tmp_path), language="python")
        surface = engine.attack_surface()
        assert len(surface) == 1
        assert surface[0]["node_id"] == "a:main"
