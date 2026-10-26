"""Replayable explicit dependency, cache and overlapping source selection."""

from pathlib import Path
from typing import cast

from trailmark.parse import parse_directory, parse_roots
from trailmark.query.api import QueryEngine
from trailmark.source_policy import SourcePolicy, SourceRoot


def test_policy_deduplicates_explicit_dependency_roots_and_caches(tmp_path: Path) -> None:
    for name in (
        "app.py",
        "vendor/selected/plugin.py",
        "vendor/other/ignored.py",
        "var/cache/cached.py",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def handler(): pass\n")
    policy = SourcePolicy(
        (
            SourceRoot(str(tmp_path), "app"),
            SourceRoot(str(tmp_path / "vendor/selected"), "plugin", "dependency"),
            SourceRoot(str(tmp_path / "vendor/selected"), "overlap", "dependency"),
        )
    )
    first = parse_roots(policy, "python")
    second = parse_roots(policy, "python")
    assert first.source_policy == second.source_policy
    assert first.source_file_counts == {"python": 2}
    assert {n.id for n in first.nodes.values() if n.kind.value == "function"} == {
        "app::app:handler",
        "plugin::plugin:handler",
    }
    reasons = {f["reason"] for f in cast(list[dict], first.source_policy["files"])}
    assert {"duplicate", "excluded_pattern", "default_directory", "selected"} <= reasons
    assert parse_directory(str(tmp_path)).source_file_counts == {"python": 1}


def test_auxiliary_cache_is_not_parsed_and_include_can_override(tmp_path: Path) -> None:
    cache = tmp_path / "var/cache"
    cache.mkdir(parents=True)
    (cache / "routes.py").write_text("def generated(): pass\n")
    policy = SourcePolicy((SourceRoot(str(tmp_path), "app"),), auxiliary=("var/cache/routes.py",))
    graph = parse_roots(policy, "python", allow_empty=True)
    assert not graph.nodes
    assert cast(list[dict], graph.source_policy["files"])[0]["status"] == "auxiliary"
    opted_in = SourcePolicy(policy.roots, include=("var/cache/routes.py",))
    assert parse_roots(opted_in, "python").source_file_counts == {"python": 1}


def test_symlinked_roots_are_deduplicated(tmp_path: Path) -> None:
    original = tmp_path / "original"
    original.mkdir()
    (original / "code.py").write_text("def handler(): pass\n")
    alias = tmp_path / "alias"
    alias.symlink_to(original, target_is_directory=True)
    policy = SourcePolicy((SourceRoot(str(original), "original"), SourceRoot(str(alias), "alias")))
    graph = parse_roots(policy, "python")
    assert graph.source_file_counts == {"python": 1}


def test_nested_selected_root_retains_registration_owner(tmp_path: Path) -> None:
    plugin = tmp_path / "vendor" / "plugin"
    plugin.mkdir(parents=True)
    (tmp_path / "app.py").write_text("def app(): pass\n")
    (plugin / "routes.py").write_text(
        "from flask import Flask\napp = Flask(__name__)\n@app.get('/')\ndef handler(): pass\n"
    )
    policy = SourcePolicy((SourceRoot(str(tmp_path), "app"), SourceRoot(str(plugin), "plugin")))
    engine = QueryEngine.from_directory(str(tmp_path), source_policy=policy)
    record = engine.endpoint_inventory()["registrations"][0]
    assert record["owning_root"] == str(plugin)
    assert record["bindings"][0]["node_id"].startswith("plugin::")
