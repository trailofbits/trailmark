"""Tests for the Trailmark CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

from trailmark import __version__
from trailmark.cli import main
from trailmark.query.api import QueryEngine


def _write_finding_report(report: Path, source: str, source_file: str) -> None:
    if source == "sarif":
        payload = {
            "runs": [
                {
                    "results": [
                        {
                            "locations": [
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {"uri": source_file},
                                        "region": {"startLine": 1},
                                    }
                                }
                            ]
                        }
                    ]
                }
            ],
        }
    else:
        payload = {
            "treeEntries": [
                {
                    "label": "review main",
                    "locations": [
                        {"path": source_file, "startLine": 0, "endLine": 1},
                    ],
                }
            ]
        }
    report.write_text(json.dumps(payload))


@pytest.mark.parametrize("source", ["sarif", "weaudit"])
@pytest.mark.parametrize("as_json", [False, True])
def test_augment_output_streams(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    source: str,
    as_json: bool,
) -> None:
    (tmp_path / "app.py").write_text("def main():\n    pass\n")
    report = tmp_path / "findings.json"
    _write_finding_report(report, source, "app.py")
    argv = ["trailmark", "augment", str(tmp_path), f"--{source}", str(report)]
    if as_json:
        argv.append("--json")
    monkeypatch.setattr(sys, "argv", argv)

    main()

    captured = capsys.readouterr()
    summary = captured.err if as_json else captured.out
    assert "Matched: 1" in summary
    assert "Subgraphs:" in summary
    if as_json:
        graph = json.loads(captured.out)
        assert "app:main" in graph["nodes"]
        assert graph["subgraphs"]
    else:
        assert captured.err == ""


@pytest.mark.parametrize("source", ["sarif", "weaudit"])
def test_repeated_augment_flags_retain_findings_from_every_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    source: str,
) -> None:
    argv = ["trailmark", "augment", str(tmp_path), "--json"]
    for name in ("first", "second"):
        source_file = f"{name}.py"
        (tmp_path / source_file).write_text(f"def {name}():\n    pass\n")
        report = tmp_path / f"{name}.json"
        _write_finding_report(report, source, source_file)
        argv.extend([f"--{source}", str(report)])
    monkeypatch.setattr(sys, "argv", argv)

    main()

    captured = capsys.readouterr()
    graph = json.loads(captured.out)
    members = {node_id for ids in graph["subgraphs"].values() for node_id in ids}
    assert {"first:first", "second:second"} <= members
    assert captured.err.count("Matched: 1") == 2


@pytest.mark.parametrize("command", ["analyze", "entrypoints", "augment", "diagram"])
@pytest.mark.parametrize("missing", [False, True])
def test_source_commands_reject_uninspected_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
    missing: bool,
) -> None:
    (tmp_path / "app.rs").write_text("fn main() {}")
    target = str(tmp_path / "missing") if missing else str(tmp_path)
    argv = ["trailmark", command, target]
    if command == "diagram":
        argv = ["trailmark", command, "--target", target, "--type", "call-graph"]
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "Not a directory" in output.err if missing else "No source files inspected" in output.err
    assert "Traceback" not in output.err


@pytest.mark.parametrize("empty_side", ["before", "after"])
def test_diff_rejects_zero_source_files_on_either_side(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    empty_side: str,
) -> None:
    before, after = tmp_path / "before", tmp_path / "after"
    for path in (before, after):
        path.mkdir()
        (path / "app.rs").write_text("fn main() {}")
        if path.name != empty_side:
            (path / "app.py").write_text("def f(): pass")
    argv = ["trailmark", "diff", str(before), str(after), "--json"]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "No source files inspected" in output.err
    assert str(tmp_path / empty_side) in output.err

    monkeypatch.setattr(sys, "argv", [*argv, "--allow-empty"])
    main()
    diff = json.loads(capsys.readouterr().out)
    assert diff["source_coverage"][empty_side] == {"python": 0}
    assert diff["source_coverage"]["after" if empty_side == "before" else "before"] == {"python": 1}
    assert diff["nodes"]["added" if empty_side == "before" else "removed"]


@pytest.mark.parametrize("language,counts", [("python", {"python": 0}), ("auto", {})])
def test_analyze_explicit_empty_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    language: str,
    counts: dict[str, int],
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["trailmark", "analyze", str(tmp_path), "--language", language, "--allow-empty"],
    )
    main()
    output = capsys.readouterr()
    graph = json.loads(output.out)
    assert graph["summary"]["source_file_counts"] == counts
    assert graph["summary"]["source_files"] == 0
    assert output.err == ""


def test_diff_git_refs_allow_intentional_empty_snapshot_and_clean_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def git(*args: str) -> str:
        return subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=tmp_path,
            check=True,
            text=True,
            capture_output=True,
        ).stdout

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    git("commit", "--allow-empty", "-m", "empty snapshot")
    git("tag", "empty-snapshot")
    (tmp_path / "app.rs").write_text("fn main() {}")
    git("add", "app.rs")
    git("commit", "-m", "add Rust source")
    git("tag", "rust-snapshot")
    argv = [
        "trailmark",
        "diff",
        "empty-snapshot",
        "rust-snapshot",
        "--repo",
        str(tmp_path),
        "--language",
        "auto",
        "--json",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert capsys.readouterr().out == ""
    assert git("worktree", "list", "--porcelain").count("worktree ") == 1

    monkeypatch.setattr(sys, "argv", [*argv, "--allow-empty"])
    main()
    diff = json.loads(capsys.readouterr().out)
    assert diff["source_coverage"] == {"before": {}, "after": {"rust": 1}}
    assert "app:main" in {node["id"] for node in diff["nodes"]["added"]}
    assert git("worktree", "list", "--porcelain").count("worktree ") == 1


def test_rust_diff_rejects_python_default_and_auto_detects_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    before, after = tmp_path / "before", tmp_path / "after"
    before.mkdir()
    after.mkdir()
    (before / "app.rs").write_text("fn main() {}")
    (after / "app.rs").write_text("fn main() { extra(); }\nfn extra() {}")
    argv = ["trailmark", "diff", str(before), str(after), "--json"]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "No source files inspected" in output.err

    monkeypatch.setattr(sys, "argv", [*argv, "--language", "auto"])
    main()
    diff = json.loads(capsys.readouterr().out)
    assert diff["source_coverage"] == {"before": {"rust": 1}, "after": {"rust": 1}}
    assert "app:extra" in {node["id"] for node in diff["nodes"]["added"]}


class TestResolutionReport:
    def test_cli_reports_call_resolution_as_json(self, tmp_path: Path) -> None:
        (tmp_path / "app.rs").write_text(
            "fn caller() { value.unwrap(); value.unwrap(); other.expect(0); }"
        )
        with (
            patch.object(
                sys,
                "argv",
                ["trailmark", "analyze", str(tmp_path), "--language", "rust", "--resolution"],
            ),
            patch("sys.stdout", new_callable=StringIO) as output,
        ):
            main()
        report = json.loads(output.getvalue())
        assert report["call_sites"] == report["unresolved_call_sites"] == 3
        assert report["resolved_call_sites"] == 0
        assert report["unresolved_targets"] == 2
        assert report["unresolved_by_callee"] == [
            {"callee": "unwrap", "call_sites": 2, "targets": 1},
            {"callee": "expect", "call_sites": 1, "targets": 1},
        ]

    @pytest.mark.parametrize("other_flag", ["--summary", "--complexity=1"])
    def test_resolution_report_rejects_conflicting_output_flags(self, other_flag: str) -> None:
        with (
            patch.object(sys, "argv", ["trailmark", "analyze", ".", "--resolution", other_flag]),
            pytest.raises(SystemExit) as exc,
        ):
            main()
        assert exc.value.code == 2


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
            "trailmark_version",
            "schema_version",
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
            "languages",
            "source_files",
            "source_file_counts",
        }
        # Nodes is a dict, edges is a list
        assert isinstance(data["nodes"], dict)
        assert isinstance(data["edges"], list)
        assert data["trailmark_version"] == __version__
        assert data["schema_version"] == 1

    @pytest.mark.parametrize("empty", [False, True])
    def test_analyze_summary_json_matches_query_api(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        empty: bool,
    ) -> None:
        if not empty:
            (tmp_path / "app.py").write_text("def main():\n    helper()\ndef helper():\n    pass\n")
            (tmp_path / "app.rs").write_text("fn run() {}\n")
        argv = ["trailmark", "analyze", str(tmp_path), "--language", "auto", "--summary", "--json"]
        if empty:
            argv.append("--allow-empty")
        monkeypatch.setattr(sys, "argv", argv)
        main()
        output = capsys.readouterr()
        assert output.err == ""
        summary = json.loads(output.out)
        expected = QueryEngine.from_directory(
            str(tmp_path), language="auto", allow_empty=empty
        ).summary()
        assert summary == expected
        assert summary["source_file_counts"] == ({} if empty else {"python": 1, "rust": 1})
        assert summary["source_files"] == (0 if empty else 2)

    @pytest.mark.parametrize("threshold", ["0", "1"])
    def test_analyze_json_rejects_complexity_before_reading_source(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        threshold: str,
    ) -> None:
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "trailmark",
                "analyze",
                str(tmp_path / "missing"),
                "--json",
                "--complexity",
                threshold,
            ],
        )
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 2
        output = capsys.readouterr()
        assert output.out == ""
        assert "--json cannot be combined with --complexity" in output.err

    def test_analyze_explicit_json_matches_default_graph(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        (tmp_path / "app.py").write_text("def main():\n    pass\n")
        argv = ["trailmark", "analyze", str(tmp_path)]
        monkeypatch.setattr(sys, "argv", argv)
        main()
        default_output = capsys.readouterr()
        monkeypatch.setattr(sys, "argv", [*argv, "--json"])
        main()
        explicit_output = capsys.readouterr()
        assert explicit_output.err == default_output.err == ""
        assert json.loads(explicit_output.out) == json.loads(default_output.out)

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
        assert len(lines) == 9
        # Verify exact line format with exact string matching
        assert lines[0] == "Nodes: 3"
        assert lines[1] == "  Functions: 1"
        assert lines[2] == "  Classes: 1"
        assert lines[3] == "  Proxies: 0"
        assert lines[4] == "Call edges: 0"
        assert lines[5] == "Dependencies: "
        assert lines[6] == "Entrypoints: 0"
        assert lines[7] == "Languages: python"
        assert lines[8] == "Source files: 1"

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

    def test_analyze_without_output_flags_prints_json(
        self,
        tmp_path: Path,
    ) -> None:
        """Omitting the complexity flag preserves the default JSON output."""
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

    @pytest.mark.parametrize("flag", ["--complexity", "-c"])
    def test_analyze_explicit_zero_prints_all_recorded_complexities(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        flag: str,
    ) -> None:
        (tmp_path / "app.py").write_text(
            "def simple(): pass\ndef branching(x):\n    if x: return 1\n"
        )
        monkeypatch.setattr(sys, "argv", ["trailmark", "analyze", str(tmp_path), flag, "0"])
        main()
        output = capsys.readouterr().out
        assert "app:simple  complexity=1" in output
        assert "app:branching  complexity=2" in output
        assert len(output.splitlines()) == 2

    @pytest.mark.parametrize("threshold", ["-1", "-20"])
    def test_analyze_rejects_negative_complexity_before_parsing_source(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        threshold: str,
    ) -> None:
        monkeypatch.setattr(
            sys, "argv", ["trailmark", "analyze", "/nonexistent", "--complexity", threshold]
        )
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "complexity threshold must be nonnegative" in captured.err

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


class TestDiagramCommand:
    """Exercise the `trailmark diagram` subcommand end-to-end."""

    def _write_call_chain(self, tmp_path: Path) -> None:
        sample = tmp_path / "app.py"
        sample.write_text(
            "def helper():\n    return 1\n\ndef main():\n    return helper()\n",
        )

    def test_call_graph_emits_flowchart(self, tmp_path: Path) -> None:
        self._write_call_chain(tmp_path)
        with (
            patch.object(
                sys,
                "argv",
                ["trailmark", "diagram", "-t", str(tmp_path), "-T", "call-graph"],
            ),
            patch("sys.stdout", new_callable=StringIO) as mock_out,
        ):
            main()
        output = mock_out.getvalue()
        assert output.startswith("flowchart TB")
        assert "helper" in output

    def test_direction_flag_changes_header(self, tmp_path: Path) -> None:
        self._write_call_chain(tmp_path)
        with (
            patch.object(
                sys,
                "argv",
                [
                    "trailmark",
                    "diagram",
                    "-t",
                    str(tmp_path),
                    "-T",
                    "call-graph",
                    "--direction",
                    "LR",
                ],
            ),
            patch("sys.stdout", new_callable=StringIO) as mock_out,
        ):
            main()
        assert mock_out.getvalue().startswith("flowchart LR")

    def test_missing_type_exits(self, tmp_path: Path) -> None:
        with (
            patch.object(
                sys,
                "argv",
                ["trailmark", "diagram", "-t", str(tmp_path)],
            ),
            pytest.raises(SystemExit),
        ):
            main()
