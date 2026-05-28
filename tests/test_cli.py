"""Tests for the Trailmark CLI."""

from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

from trailmark.cli import main


class TestMainNoArgs:
    def test_no_command_prints_help_and_exits(self) -> None:
        with patch.object(sys, "argv", ["trailmark"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_no_command_shows_usage(self) -> None:
        with (
            patch.object(sys, "argv", ["trailmark"]),
            patch("sys.stdout", new_callable=StringIO) as mock_out,
            pytest.raises(SystemExit),
        ):
            main()
        output = mock_out.getvalue()
        assert "trailmark" in output
        # Verify it's the parser help text
        assert "analyze" in output

    def test_unknown_command_exits(self) -> None:
        """Unknown subcommand should cause SystemExit."""
        with (
            patch.object(sys, "argv", ["trailmark", "notanalyze"]),
            pytest.raises(SystemExit),
        ):
            main()


class TestMainHelpText:
    """Verify argparse configuration strings survive mutation testing."""

    def test_prog_name(self) -> None:
        with (
            patch.object(sys, "argv", ["trailmark", "--help"]),
            patch("sys.stdout", new_callable=StringIO) as mock_out,
            pytest.raises(SystemExit),
        ):
            main()
        output = mock_out.getvalue()
        assert "trailmark" in output

    def test_description(self) -> None:
        with (
            patch.object(sys, "argv", ["trailmark", "--help"]),
            patch("sys.stdout", new_callable=StringIO) as mock_out,
            pytest.raises(SystemExit),
        ):
            main()
        assert "queryable graphs" in mock_out.getvalue()

    def test_analyze_help(self) -> None:
        with (
            patch.object(sys, "argv", ["trailmark", "analyze", "--help"]),
            patch("sys.stdout", new_callable=StringIO) as mock_out,
            pytest.raises(SystemExit),
        ):
            main()
        output = mock_out.getvalue()
        # path argument
        assert "Directory to analyze" in output
        # --language help
        assert "python" in output
        # --summary help
        assert "summary" in output.lower()
        # --complexity help
        assert "complexity" in output.lower()

    def test_analyze_argument_aliases(self) -> None:
        """Short flags -l, -s, -c must be registered."""
        with (
            patch.object(sys, "argv", ["trailmark", "analyze", "--help"]),
            patch("sys.stdout", new_callable=StringIO) as mock_out,
            pytest.raises(SystemExit),
        ):
            main()
        output = mock_out.getvalue()
        assert "-l" in output
        assert "-s" in output
        assert "-c" in output


class TestAnalyzeCommand:
    def test_analyze_json_output(self, tmp_path: Path) -> None:
        sample = tmp_path / "hello.py"
        sample.write_text("def greet():\n    return 'hi'\n")
        with (
            patch.object(
                sys,
                "argv",
                ["trailmark", "analyze", str(tmp_path)],
            ),
            patch("sys.stdout", new_callable=StringIO) as mock_out,
        ):
            main()
        data = json.loads(mock_out.getvalue())
        assert data["language"] == "python"
        assert "nodes" in data
        assert "edges" in data
        assert "summary" in data
        assert data["root_path"] == str(tmp_path)
        node_names = [n["name"] for n in data["nodes"].values()]
        assert "greet" in node_names

    def test_analyze_json_full_structure(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify exact JSON top-level keys and summary fields."""
        sample = tmp_path / "a.py"
        sample.write_text("def f():\n    pass\n")
        with (
            patch.object(
                sys,
                "argv",
                ["trailmark", "analyze", str(tmp_path)],
            ),
            patch("sys.stdout", new_callable=StringIO) as mock_out,
        ):
            main()
        data = json.loads(mock_out.getvalue())
        # Exact top-level keys
        assert set(data.keys()) == {
            "language",
            "root_path",
            "summary",
            "nodes",
            "edges",
            "subgraphs",
        }
        # Summary has exact keys
        assert set(data["summary"].keys()) == {
            "total_nodes",
            "functions",
            "classes",
            "proxies",
            "call_edges",
            "dependencies",
            "entrypoints",
        }
        # Nodes is a dict, edges is a list
        assert isinstance(data["nodes"], dict)
        assert isinstance(data["edges"], list)

    def test_analyze_summary_exact_format(
        self,
        tmp_path: Path,
    ) -> None:
        sample = tmp_path / "app.py"
        sample.write_text(
            "class Foo:\n    pass\n\ndef bar():\n    pass\n",
        )
        with (
            patch.object(
                sys,
                "argv",
                ["trailmark", "analyze", str(tmp_path), "--summary"],
            ),
            patch("sys.stdout", new_callable=StringIO) as mock_out,
        ):
            main()
        lines = mock_out.getvalue().strip().split("\n")
        assert len(lines) == 7
        # Verify exact line format with exact string matching
        assert lines[0] == "Nodes: 3"
        assert lines[1] == "  Functions: 1"
        assert lines[2] == "  Classes: 1"
        assert lines[3] == "  Proxies: 0"
        assert lines[4] == "Call edges: 0"
        assert lines[5] == "Dependencies: "
        assert lines[6] == "Entrypoints: 0"

    def test_analyze_summary_with_dependencies(
        self,
        tmp_path: Path,
    ) -> None:
        """Dependencies line should include comma-joined names."""
        sample = tmp_path / "app.py"
        sample.write_text("import os\nfrom pathlib import Path\n")
        with (
            patch.object(
                sys,
                "argv",
                ["trailmark", "analyze", str(tmp_path), "--summary"],
            ),
            patch("sys.stdout", new_callable=StringIO) as mock_out,
        ):
            main()
        lines = mock_out.getvalue().strip().split("\n")
        dep_line = lines[5]
        assert dep_line.startswith("Dependencies: ")
        # Should contain actual dependency names
        assert "os" in dep_line
        assert "pathlib" in dep_line

    def test_analyze_summary_line_prefixes(
        self,
        tmp_path: Path,
    ) -> None:
        """Every summary line must start with the exact expected label."""
        sample = tmp_path / "x.py"
        sample.write_text("def f(): pass\n")
        with (
            patch.object(
                sys,
                "argv",
                ["trailmark", "analyze", str(tmp_path), "--summary"],
            ),
            patch("sys.stdout", new_callable=StringIO) as mock_out,
        ):
            main()
        lines = mock_out.getvalue().strip().split("\n")
        assert lines[0].startswith("Nodes: ")
        assert lines[1].startswith("  Functions: ")
        assert lines[2].startswith("  Classes: ")
        assert lines[3].startswith("  Proxies: ")
        assert lines[4].startswith("Call edges: ")
        assert lines[5].startswith("Dependencies: ")
        assert lines[6].startswith("Entrypoints: ")

    def test_analyze_summary_short_flag(
        self,
        tmp_path: Path,
    ) -> None:
        sample = tmp_path / "s.py"
        sample.write_text("def f():\n    pass\n")
        with (
            patch.object(
                sys,
                "argv",
                ["trailmark", "analyze", str(tmp_path), "-s"],
            ),
            patch("sys.stdout", new_callable=StringIO) as mock_out,
        ):
            main()
        output = mock_out.getvalue()
        assert "Nodes:" in output
        assert "Functions:" in output
        assert "Classes:" in output

    def test_analyze_complexity_exact_output(
        self,
        tmp_path: Path,
    ) -> None:
        code = (
            "def branchy(x):\n"
            "    if x > 0:\n"
            "        if x > 10:\n"
            "            return 1\n"
            "        elif x > 5:\n"
            "            return 2\n"
            "    return 0\n"
        )
        sample = tmp_path / "complex.py"
        sample.write_text(code)
        with (
            patch.object(
                sys,
                "argv",
                [
                    "trailmark",
                    "analyze",
                    str(tmp_path),
                    "--complexity",
                    "2",
                ],
            ),
            patch("sys.stdout", new_callable=StringIO) as mock_out,
        ):
            main()
        output = mock_out.getvalue()
        assert "complexity=" in output
        assert "branchy" in output
        assert "complex.py:" in output
        # Format: "  {id}  complexity={n}  {file}:{line}"
        lines = [x for x in output.split("\n") if x.strip()]
        assert len(lines) >= 1
        for line in lines:
            assert line.startswith("  ")
            assert "complexity=" in line
            # After stripping, 3 parts: id, complexity=N, file:line
            parts = line.strip().split("  ")
            assert len(parts) == 3

    def test_analyze_complexity_short_flag(
        self,
        tmp_path: Path,
    ) -> None:
        """The -c flag should work the same as --complexity."""
        code = "def f(x):\n    if x: return 1\n    return 0\n"
        sample = tmp_path / "c.py"
        sample.write_text(code)
        with (
            patch.object(
                sys,
                "argv",
                ["trailmark", "analyze", str(tmp_path), "-c", "1"],
            ),
            patch("sys.stdout", new_callable=StringIO) as mock_out,
        ):
            main()
        output = mock_out.getvalue()
        assert "complexity=" in output

    def test_analyze_complexity_no_hotspots(
        self,
        tmp_path: Path,
    ) -> None:
        sample = tmp_path / "simple.py"
        sample.write_text("def noop():\n    pass\n")
        with (
            patch.object(
                sys,
                "argv",
                [
                    "trailmark",
                    "analyze",
                    str(tmp_path),
                    "--complexity",
                    "100",
                ],
            ),
            patch("sys.stdout", new_callable=StringIO) as mock_out,
        ):
            main()
        exact = "No functions with complexity >= 100"
        assert mock_out.getvalue().strip() == exact

    def test_analyze_default_language_is_python(
        self,
        tmp_path: Path,
    ) -> None:
        sample = tmp_path / "m.py"
        sample.write_text("x = 1\n")
        with (
            patch.object(
                sys,
                "argv",
                ["trailmark", "analyze", str(tmp_path)],
            ),
            patch("sys.stdout", new_callable=StringIO) as mock_out,
        ):
            main()
        data = json.loads(mock_out.getvalue())
        assert data["language"] == "python"

    def test_analyze_explicit_language_flag(
        self,
        tmp_path: Path,
    ) -> None:
        """--language python should produce same output as default."""
        sample = tmp_path / "x.py"
        sample.write_text("y = 1\n")
        with (
            patch.object(
                sys,
                "argv",
                [
                    "trailmark",
                    "analyze",
                    str(tmp_path),
                    "--language",
                    "python",
                ],
            ),
            patch("sys.stdout", new_callable=StringIO) as mock_out,
        ):
            main()
        data = json.loads(mock_out.getvalue())
        assert data["language"] == "python"

    def test_analyze_language_short_flag(
        self,
        tmp_path: Path,
    ) -> None:
        """The -l flag should work for language."""
        sample = tmp_path / "z.py"
        sample.write_text("z = 1\n")
        with (
            patch.object(
                sys,
                "argv",
                [
                    "trailmark",
                    "analyze",
                    str(tmp_path),
                    "-l",
                    "python",
                ],
            ),
            patch("sys.stdout", new_callable=StringIO) as mock_out,
        ):
            main()
        data = json.loads(mock_out.getvalue())
        assert data["language"] == "python"

    def test_analyze_summary_takes_priority_over_json(
        self,
        tmp_path: Path,
    ) -> None:
        """When --summary is set, output should not be JSON."""
        sample = tmp_path / "t.py"
        sample.write_text("def t(): pass\n")
        with (
            patch.object(
                sys,
                "argv",
                ["trailmark", "analyze", str(tmp_path), "--summary"],
            ),
            patch("sys.stdout", new_callable=StringIO) as mock_out,
        ):
            main()
        output = mock_out.getvalue()
        # Should not be parseable as JSON
        with pytest.raises(json.JSONDecodeError):
            json.loads(output)
        assert "Nodes:" in output

    def test_analyze_complexity_takes_priority_over_json(
        self,
        tmp_path: Path,
    ) -> None:
        """When --complexity is set, output is not JSON."""
        code = "def f(x):\n    if x: return 1\n    return 0\n"
        sample = tmp_path / "t2.py"
        sample.write_text(code)
        with (
            patch.object(
                sys,
                "argv",
                [
                    "trailmark",
                    "analyze",
                    str(tmp_path),
                    "--complexity",
                    "1",
                ],
            ),
            patch("sys.stdout", new_callable=StringIO) as mock_out,
        ):
            main()
        output = mock_out.getvalue()
        # Should not be parseable as JSON
        with pytest.raises(json.JSONDecodeError):
            json.loads(output)

    def test_analyze_path_argument_passed(
        self,
        tmp_path: Path,
    ) -> None:
        """The path argument should be the analyzed directory."""
        sample = tmp_path / "p.py"
        sample.write_text("def p(): pass\n")
        with (
            patch.object(
                sys,
                "argv",
                ["trailmark", "analyze", str(tmp_path)],
            ),
            patch("sys.stdout", new_callable=StringIO) as mock_out,
        ):
            main()
        data = json.loads(mock_out.getvalue())
        assert data["root_path"] == str(tmp_path)

    def test_analyze_complexity_zero_means_json(
        self,
        tmp_path: Path,
    ) -> None:
        """Default complexity=0 should output JSON, not complexity."""
        sample = tmp_path / "c0.py"
        sample.write_text("def f(): pass\n")
        with (
            patch.object(
                sys,
                "argv",
                ["trailmark", "analyze", str(tmp_path)],
            ),
            patch("sys.stdout", new_callable=StringIO) as mock_out,
        ):
            main()
        # Should be parseable as JSON (default path)
        data = json.loads(mock_out.getvalue())
        assert "nodes" in data

    def test_analyze_complexity_one_not_json(
        self,
        tmp_path: Path,
    ) -> None:
        """Complexity=1 should show complexity output, not JSON."""
        code = "def f(x):\n    if x: return 1\n    return 0\n"
        sample = tmp_path / "c1.py"
        sample.write_text(code)
        with (
            patch.object(
                sys,
                "argv",
                ["trailmark", "analyze", str(tmp_path), "-c", "1"],
            ),
            patch("sys.stdout", new_callable=StringIO) as mock_out,
        ):
            main()
        output = mock_out.getvalue()
        assert "complexity=" in output
        with pytest.raises(json.JSONDecodeError):
            json.loads(output)
