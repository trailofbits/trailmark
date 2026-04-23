"""Tests for structural diff between two code graphs."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from trailmark.analysis.diff import compute_diff, format_diff, git_worktree
from trailmark.models.graph import CodeGraph
from trailmark.query.api import QueryEngine


def _build(path: Path, language: str = "python") -> QueryEngine:
    return QueryEngine.from_directory(str(path), language=language)


def _graph(engine: QueryEngine) -> CodeGraph:
    return engine._store._graph  # noqa: SLF001


class TestNodeDiff:
    def test_added_node_shows_up(self, tmp_path: Path) -> None:
        before = tmp_path / "before"
        after = tmp_path / "after"
        before.mkdir()
        after.mkdir()
        (before / "app.py").write_text("def existing():\n    pass\n")
        (after / "app.py").write_text(
            "def existing():\n    pass\n\ndef new_function():\n    pass\n",
        )
        diff = compute_diff(_graph(_build(before)), _graph(_build(after)))
        added_ids = {n["id"] for n in diff["nodes"]["added"]}
        assert "app:new_function" in added_ids

    def test_removed_node_shows_up(self, tmp_path: Path) -> None:
        before = tmp_path / "before"
        after = tmp_path / "after"
        before.mkdir()
        after.mkdir()
        (before / "app.py").write_text(
            "def keep():\n    pass\n\ndef doomed():\n    pass\n",
        )
        (after / "app.py").write_text("def keep():\n    pass\n")
        diff = compute_diff(_graph(_build(before)), _graph(_build(after)))
        removed_ids = {n["id"] for n in diff["nodes"]["removed"]}
        assert "app:doomed" in removed_ids

    def test_complexity_change_is_flagged(self, tmp_path: Path) -> None:
        before = tmp_path / "before"
        after = tmp_path / "after"
        before.mkdir()
        after.mkdir()
        (before / "app.py").write_text("def f(x):\n    return x\n")
        (after / "app.py").write_text(
            "def f(x):\n"
            "    if x > 0:\n"
            "        if x > 10:\n"
            "            return 2\n"
            "        return 1\n"
            "    return 0\n",
        )
        diff = compute_diff(_graph(_build(before)), _graph(_build(after)))
        modified = {m["id"]: m for m in diff["nodes"]["modified"]}
        assert "app:f" in modified
        assert "cyclomatic_complexity" in modified["app:f"]["changes"]
        cc = modified["app:f"]["changes"]["cyclomatic_complexity"]
        assert cc["after"] > cc["before"]

    def test_parameter_change_is_flagged(self, tmp_path: Path) -> None:
        before = tmp_path / "before"
        after = tmp_path / "after"
        before.mkdir()
        after.mkdir()
        (before / "app.py").write_text("def f(x):\n    pass\n")
        (after / "app.py").write_text("def f(x, y, z):\n    pass\n")
        diff = compute_diff(_graph(_build(before)), _graph(_build(after)))
        modified = {m["id"]: m for m in diff["nodes"]["modified"]}
        assert "parameters" in modified["app:f"]["changes"]
        assert modified["app:f"]["changes"]["parameters"]["after"] == ["x", "y", "z"]


class TestEdgeDiff:
    def test_new_call_edge_detected(self, tmp_path: Path) -> None:
        before = tmp_path / "before"
        after = tmp_path / "after"
        before.mkdir()
        after.mkdir()
        (before / "app.py").write_text(
            "def main():\n    pass\n\ndef helper():\n    return 1\n",
        )
        (after / "app.py").write_text(
            "def main():\n    return helper()\n\ndef helper():\n    return 1\n",
        )
        diff = compute_diff(_graph(_build(before)), _graph(_build(after)))
        added_edges = diff["edges"]["added"]
        assert any(
            e["source"] == "app:main" and e["target"] == "app:helper" and e["kind"] == "calls"
            for e in added_edges
        )


class TestEntrypointDiff:
    def test_new_entrypoint_tracked(self, tmp_path: Path) -> None:
        before = tmp_path / "before"
        after = tmp_path / "after"
        before.mkdir()
        after.mkdir()
        (before / "app.py").write_text("def helper():\n    pass\n")
        (after / "app.py").write_text(
            "from flask import Flask\n"
            "app = Flask(__name__)\n"
            "\n"
            "@app.route('/login')\n"
            "def login():\n"
            "    return 'ok'\n",
        )
        diff = compute_diff(_graph(_build(before)), _graph(_build(after)))
        added_eps = diff["entrypoints"]["added"]
        assert any(ep["id"] == "app:login" for ep in added_eps), added_eps
        login_ep = next(ep for ep in added_eps if ep["id"] == "app:login")
        assert login_ep["kind"] == "api"
        assert login_ep["trust_level"] == "untrusted_external"

    def test_removed_entrypoint_tracked(self, tmp_path: Path) -> None:
        before = tmp_path / "before"
        after = tmp_path / "after"
        before.mkdir()
        after.mkdir()
        (before / "app.py").write_text(
            "from flask import Flask\n"
            "app = Flask(__name__)\n"
            "\n"
            "@app.route('/old')\n"
            "def old():\n"
            "    return 'ok'\n",
        )
        (after / "app.py").write_text(
            "from flask import Flask\napp = Flask(__name__)\n\ndef old():\n    return 'ok'\n",
        )
        diff = compute_diff(_graph(_build(before)), _graph(_build(after)))
        removed_eps = diff["entrypoints"]["removed"]
        assert any(ep["id"] == "app:old" for ep in removed_eps), removed_eps

    def test_trust_level_change_detected(self, tmp_path: Path) -> None:
        """Override file change that tightens trust level shows up as modified."""
        before = tmp_path / "before"
        after = tmp_path / "after"
        before.mkdir()
        after.mkdir()
        for d in (before, after):
            (d / ".trailmark").mkdir()
            (d / "svc.py").write_text("def dispatch(req):\n    return req\n")
        (before / ".trailmark" / "entrypoints.toml").write_text(
            "[[entrypoint]]\n"
            'node = "svc:dispatch"\n'
            'kind = "api"\n'
            'trust = "semi_trusted_external"\n'
            'asset_value = "low"\n',
        )
        (after / ".trailmark" / "entrypoints.toml").write_text(
            "[[entrypoint]]\n"
            'node = "svc:dispatch"\n'
            'kind = "api"\n'
            'trust = "untrusted_external"\n'
            'asset_value = "high"\n',
        )
        diff = compute_diff(_graph(_build(before)), _graph(_build(after)))
        modified = diff["entrypoints"]["modified"]
        assert any(m["id"] == "svc:dispatch" for m in modified), modified
        dispatch_mod = next(m for m in modified if m["id"] == "svc:dispatch")
        assert dispatch_mod["before"]["trust_level"] == "semi_trusted_external"
        assert dispatch_mod["after"]["trust_level"] == "untrusted_external"


class TestQueryEngineIntegration:
    def test_diff_against_method(self, tmp_path: Path) -> None:
        before = tmp_path / "before"
        after = tmp_path / "after"
        before.mkdir()
        after.mkdir()
        (before / "app.py").write_text("def a():\n    pass\n")
        (after / "app.py").write_text("def a():\n    pass\n\ndef b():\n    pass\n")
        before_engine = _build(before)
        after_engine = _build(after)
        diff = after_engine.diff_against(before_engine)
        added_ids = {n["id"] for n in diff["nodes"]["added"]}
        assert "app:b" in added_ids


class TestFormatDiff:
    """Precise assertions on format_diff's human-readable output.

    These tests lock in every visible token — headers, line prefixes,
    separators, truncation wording — so string-mutating mutants don't
    silently survive.
    """

    def test_empty_diff_returns_no_changes(self) -> None:
        assert format_diff({}) == "No structural changes."

    def test_empty_dict_with_no_changes_returns_no_changes(self) -> None:
        diff = {
            "summary_delta": {},
            "nodes": {"added": [], "removed": [], "modified": []},
            "edges": {"added": [], "removed": []},
            "entrypoints": {"added": [], "removed": [], "modified": []},
        }
        assert format_diff(diff) == "No structural changes."

    def test_summary_header_present(self) -> None:
        diff = {
            "summary_delta": {"nodes": {"before": 1, "after": 2, "delta": 1}},
            "nodes": {"added": [], "removed": [], "modified": []},
            "edges": {"added": [], "removed": []},
            "entrypoints": {"added": [], "removed": [], "modified": []},
        }
        assert "Summary:" in format_diff(diff)

    def test_summary_renders_each_metric(self) -> None:
        diff = {
            "summary_delta": {
                "nodes": {"before": 10, "after": 12, "delta": 2},
                "edges": {"before": 5, "after": 3, "delta": -2},
                "entrypoints": {"before": 0, "after": 4, "delta": 4},
            },
            "nodes": {"added": [], "removed": [], "modified": []},
            "edges": {"added": [], "removed": []},
            "entrypoints": {"added": [], "removed": [], "modified": []},
        }
        out = format_diff(diff)
        assert "nodes: 10 -> 12 (+2)" in out
        assert "edges: 5 -> 3 (-2)" in out
        assert "entrypoints: 0 -> 4 (+4)" in out

    def test_positive_delta_gets_plus_sign(self) -> None:
        diff = {
            "summary_delta": {"nodes": {"before": 1, "after": 3, "delta": 2}},
            "nodes": {"added": [], "removed": [], "modified": []},
            "edges": {"added": [], "removed": []},
            "entrypoints": {"added": [], "removed": [], "modified": []},
        }
        assert "(+2)" in format_diff(diff)

    def test_zero_delta_still_shown_with_plus(self) -> None:
        # `delta == 0` meets the `>= 0` guard so it gets the plus sign.
        diff = {
            "summary_delta": {"nodes": {"before": 5, "after": 5, "delta": 0}},
            "nodes": {"added": [], "removed": [], "modified": []},
            "edges": {"added": [], "removed": []},
            "entrypoints": {"added": [], "removed": [], "modified": []},
        }
        assert "(+0)" in format_diff(diff)

    def test_negative_delta_has_no_extra_plus(self) -> None:
        diff = {
            "summary_delta": {"nodes": {"before": 5, "after": 3, "delta": -2}},
            "nodes": {"added": [], "removed": [], "modified": []},
            "edges": {"added": [], "removed": []},
            "entrypoints": {"added": [], "removed": [], "modified": []},
        }
        out = format_diff(diff)
        assert "(-2)" in out
        assert "(+-2)" not in out

    def test_added_nodes_section(self) -> None:
        diff = {
            "summary_delta": {},
            "nodes": {
                "added": [
                    {"id": "mod:new", "kind": "function", "file": "mod.py"},
                ],
                "removed": [],
                "modified": [],
            },
            "edges": {"added": [], "removed": []},
            "entrypoints": {"added": [], "removed": [], "modified": []},
        }
        out = format_diff(diff)
        assert "Added nodes (1):" in out
        assert "+ mod:new  (function, mod.py)" in out

    def test_removed_nodes_section(self) -> None:
        diff = {
            "summary_delta": {},
            "nodes": {
                "added": [],
                "removed": [
                    {"id": "mod:gone", "kind": "method", "file": "old.py"},
                ],
                "modified": [],
            },
            "edges": {"added": [], "removed": []},
            "entrypoints": {"added": [], "removed": [], "modified": []},
        }
        out = format_diff(diff)
        assert "Removed nodes (1):" in out
        assert "- mod:gone  (method, old.py)" in out

    def test_modified_nodes_section_with_complexity(self) -> None:
        diff = {
            "summary_delta": {},
            "nodes": {
                "added": [],
                "removed": [],
                "modified": [
                    {
                        "id": "mod:grew",
                        "changes": {
                            "cyclomatic_complexity": {
                                "before": 2,
                                "after": 9,
                            },
                        },
                    },
                ],
            },
            "edges": {"added": [], "removed": []},
            "entrypoints": {"added": [], "removed": [], "modified": []},
        }
        out = format_diff(diff)
        assert "Modified nodes (1):" in out
        assert "~ mod:grew  (cyclomatic_complexity)" in out
        assert "complexity: 2 -> 9" in out

    def test_modified_nodes_truncates_after_twenty(self) -> None:
        modified = [
            {"id": f"mod:n{i}", "changes": {"parameters": {"before": [], "after": []}}}
            for i in range(25)
        ]
        diff = {
            "summary_delta": {},
            "nodes": {"added": [], "removed": [], "modified": modified},
            "edges": {"added": [], "removed": []},
            "entrypoints": {"added": [], "removed": [], "modified": []},
        }
        out = format_diff(diff)
        assert "Modified nodes (25):" in out
        assert "... and 5 more" in out
        # First 20 shown, last five collapsed.
        assert "~ mod:n19  " in out
        assert "~ mod:n20  " not in out

    def test_added_nodes_truncates_after_twenty(self) -> None:
        added = [{"id": f"mod:a{i}", "kind": "function", "file": "f.py"} for i in range(22)]
        diff = {
            "summary_delta": {},
            "nodes": {"added": added, "removed": [], "modified": []},
            "edges": {"added": [], "removed": []},
            "entrypoints": {"added": [], "removed": [], "modified": []},
        }
        out = format_diff(diff)
        assert "Added nodes (22):" in out
        assert "... and 2 more" in out

    def test_entrypoint_added_section(self) -> None:
        diff = {
            "summary_delta": {},
            "nodes": {"added": [], "removed": [], "modified": []},
            "edges": {"added": [], "removed": []},
            "entrypoints": {
                "added": [
                    {
                        "id": "app:login",
                        "kind": "api",
                        "trust_level": "untrusted_external",
                        "asset_value": "high",
                    },
                ],
                "removed": [],
                "modified": [],
            },
        }
        out = format_diff(diff)
        assert "Attack surface:" in out
        assert "+ entrypoint app:login  (api, trust=untrusted_external, asset=high)" in out

    def test_entrypoint_removed_section(self) -> None:
        diff = {
            "summary_delta": {},
            "nodes": {"added": [], "removed": [], "modified": []},
            "edges": {"added": [], "removed": []},
            "entrypoints": {
                "added": [],
                "removed": [
                    {
                        "id": "app:old",
                        "kind": "user_input",
                        "trust_level": "trusted_internal",
                        "asset_value": "low",
                    },
                ],
                "modified": [],
            },
        }
        out = format_diff(diff)
        assert "Attack surface:" in out
        assert "- entrypoint app:old  (user_input, trust=trusted_internal, asset=low)" in out

    def test_entrypoint_modified_trust_change(self) -> None:
        diff = {
            "summary_delta": {},
            "nodes": {"added": [], "removed": [], "modified": []},
            "edges": {"added": [], "removed": []},
            "entrypoints": {
                "added": [],
                "removed": [],
                "modified": [
                    {
                        "id": "svc:dispatch",
                        "before": {
                            "kind": "api",
                            "trust_level": "semi_trusted_external",
                            "asset_value": "low",
                        },
                        "after": {
                            "kind": "api",
                            "trust_level": "untrusted_external",
                            "asset_value": "low",
                        },
                    },
                ],
            },
        }
        out = format_diff(diff)
        assert "~ entrypoint svc:dispatch" in out
        assert "trust: semi_trusted_external -> untrusted_external" in out
        assert "asset:" not in out  # unchanged

    def test_entrypoint_modified_asset_change(self) -> None:
        diff = {
            "summary_delta": {},
            "nodes": {"added": [], "removed": [], "modified": []},
            "edges": {"added": [], "removed": []},
            "entrypoints": {
                "added": [],
                "removed": [],
                "modified": [
                    {
                        "id": "svc:upgrade",
                        "before": {
                            "kind": "api",
                            "trust_level": "untrusted_external",
                            "asset_value": "low",
                        },
                        "after": {
                            "kind": "api",
                            "trust_level": "untrusted_external",
                            "asset_value": "high",
                        },
                    },
                ],
            },
        }
        out = format_diff(diff)
        assert "asset: low -> high" in out
        assert "trust:" not in out  # unchanged

    def test_edges_count_line(self) -> None:
        diff = {
            "summary_delta": {},
            "nodes": {"added": [], "removed": [], "modified": []},
            "edges": {
                "added": [
                    {"source": "a", "target": "b", "kind": "calls"},
                    {"source": "a", "target": "c", "kind": "calls"},
                ],
                "removed": [
                    {"source": "x", "target": "y", "kind": "calls"},
                ],
            },
            "entrypoints": {"added": [], "removed": [], "modified": []},
        }
        out = format_diff(diff)
        assert "Edges: +2  -1" in out

    def test_output_has_no_trailing_whitespace(self) -> None:
        diff = {
            "summary_delta": {"nodes": {"before": 1, "after": 2, "delta": 1}},
            "nodes": {"added": [], "removed": [], "modified": []},
            "edges": {"added": [], "removed": []},
            "entrypoints": {"added": [], "removed": [], "modified": []},
        }
        out = format_diff(diff)
        assert out == out.rstrip()
        assert not out.endswith("\n")


class TestGitWorktree:
    def test_worktree_materializes_a_ref(self, tmp_path: Path) -> None:
        """``git_worktree`` adds a detached worktree and cleans it up."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "test@example.com")
        _git(repo, "config", "user.name", "Test")
        (repo / "app.py").write_text("def main():\n    pass\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "initial")

        with git_worktree(repo, "HEAD") as worktree:
            assert (worktree / "app.py").exists()
            assert (worktree / "app.py").read_text() == "def main():\n    pass\n"

    def test_worktree_rejects_non_repo(self, tmp_path: Path) -> None:
        with (
            pytest.raises(ValueError, match="Not a git repository"),
            git_worktree(tmp_path, "HEAD"),
        ):
            pass


def _git(cwd: Path, *args: str) -> None:
    """Run a git command using the absolute `git` path to satisfy ruff S607."""
    import shutil

    git_bin = shutil.which("git")
    assert git_bin is not None, "git must be available on PATH for these tests"
    subprocess.run([git_bin, *args], cwd=cwd, check=True, capture_output=True)  # noqa: S603
