"""Source selection is independent of parser language and directory conventions."""

import json
from pathlib import Path
from typing import cast

import pytest

from tests.kat_runner import fixture_pair
from tests.test_kat_parsers import PARSER_FIXTURES
from trailmark.analysis.entrypoints import detect_entrypoints
from trailmark.models.entrypoints import EntrypointEvidence
from trailmark.models.nodes import NodeKind
from trailmark.parse import parse_directory, parse_roots, supported_languages
from trailmark.query.api import QueryEngine
from trailmark.source_policy import SourcePolicy, SourceRoot
from trailmark.storage.graph_store import GraphStore


def _write(root: Path, name: str, source: str = "def handler(): pass\n") -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    return path


def test_explicit_roots_deduplicate_and_bypass_parent_exclusions(tmp_path: Path) -> None:
    for name in (
        "app.py",
        "vendor/selected/plugin.py",
        "vendor/other/ignored.py",
        "var/cache/c.py",
    ):
        _write(tmp_path, name)
    policy = SourcePolicy(
        (
            SourceRoot(str(tmp_path), "app"),
            SourceRoot(str(tmp_path / "vendor/selected"), "plugin", "dependency"),
            SourceRoot(str(tmp_path / "vendor/selected"), "overlap", "dependency"),
        ),
        exclude=("var/cache/**",),
    )
    graph = parse_roots(policy, "python")
    assert graph.source_policy == parse_roots(policy, "python").source_policy
    assert graph.source_file_counts == {"python": 2}
    assert {n.id for n in graph.nodes.values() if n.kind.value == "function"} == {
        "app::app:handler",
        "plugin::plugin:handler",
    }
    reasons = {f["reason"] for f in cast(list[dict], graph.source_policy["files"])}
    assert {"duplicate", "excluded_pattern", "default_directory", "selected"} <= reasons
    assert parse_directory(str(tmp_path)).source_file_counts == {"python": 2}


def test_explicit_includes_and_unparsed_files(tmp_path: Path) -> None:
    _write(tmp_path, "var/cache/generated.py")
    _write(tmp_path, "data.txt", "not source")
    roots = (SourceRoot(str(tmp_path), "app"),)
    policy = SourcePolicy(roots, exclude=("var/cache/**",))
    graph = parse_roots(policy, "python", allow_empty=True)
    assert not graph.nodes
    files = cast(list[dict], graph.source_policy["files"])
    assert next(f for f in files if f["path"].endswith("data.txt"))["status"] == "unparsed"
    included = parse_roots(
        SourcePolicy(roots, exclude=policy.exclude, include=("var/cache/generated.py",)), "python"
    )
    assert included.source_file_counts == {"python": 1}


def test_default_exclusions_prune_directories_without_layout_assumptions(tmp_path: Path) -> None:
    _write(tmp_path, "generated/code.py")
    _write(tmp_path, "var/cache/code.py")
    _write(tmp_path, "node_modules/package/deep/code.py")
    graph = parse_directory(str(tmp_path))
    assert graph.source_file_counts == {"python": 2}
    files = cast(list[dict], graph.source_policy["files"])
    excluded = [f for f in files if f["status"] == "skipped"]
    assert len(excluded) == 1
    assert excluded[0]["unit"] == "directory"
    assert excluded[0]["reason"] == "default_directory"


@pytest.mark.parametrize("pattern", ["build", "build/", "build/**"])
def test_directory_exclusions_still_apply_when_include_keeps_traversal_open(
    pattern: str, tmp_path: Path
) -> None:
    for name in ("build/keep.py", "build/drop.py", "build/nested/drop.py"):
        _write(tmp_path, name)
    graph = parse_roots(
        SourcePolicy(
            (SourceRoot(str(tmp_path), "app"),),
            exclude=(pattern,),
            include=("build/keep.py",),
            default_exclusions=False,
        )
    )
    assert graph.source_file_counts == {"python": 1}
    assert {unit.location.file_path for unit in graph.nodes.values()} == {
        str(tmp_path / "build/keep.py")
    }


def test_single_star_include_cannot_cross_directory_segments(tmp_path: Path) -> None:
    _write(tmp_path, "selected.gen.py")
    _write(tmp_path, "node_modules/deep/ignored.gen.py")
    graph = parse_roots(SourcePolicy((SourceRoot(str(tmp_path), "root"),), include=("*.gen.py",)))
    assert graph.source_file_counts == {"python": 1}
    assert any(
        file["unit"] == "directory" and file["reason"] == "default_directory"
        for file in cast(list[dict], graph.source_policy["files"])
    )


def test_symlinks_cannot_escape_or_duplicate_selected_files(tmp_path: Path) -> None:
    original = tmp_path / "original"
    _write(original, "code.py")
    _write(tmp_path, "outside.py")
    (original / "duplicate.py").symlink_to(original / "code.py")
    (original / "escape.py").symlink_to(tmp_path / "outside.py")
    alias = tmp_path / "alias"
    alias.symlink_to(original, target_is_directory=True)
    graph = parse_roots(
        SourcePolicy((SourceRoot(str(original), "original"), SourceRoot(str(alias), "alias")))
    )
    assert graph.source_file_counts == {"python": 1}
    reasons = {f["reason"] for f in cast(list[dict], graph.source_policy["files"])}
    assert {"duplicate", "symlink_out_of_root"} <= reasons


def test_policy_is_detached_at_query_and_storage_layers(tmp_path: Path) -> None:
    _write(tmp_path, "app.py")
    graph = parse_roots(SourcePolicy((SourceRoot(str(tmp_path), "root"),)))
    store = GraphStore(graph)
    manifest = store.source_policy()
    cast(list, manifest["files"]).clear()
    engine = QueryEngine.from_graph(graph)
    assert engine.source_policy()["files"]
    assert json.loads(engine.to_json())["source_policy"] == graph.source_policy


def test_symlinked_root_preserves_lexical_paths(tmp_path: Path) -> None:
    real, alias = tmp_path / "real", tmp_path / "alias"
    _write(real, "app.py")
    alias.symlink_to(real, target_is_directory=True)
    graph = parse_directory(str(alias))
    assert graph.root_path == str(alias)
    assert {unit.location.file_path for unit in graph.nodes.values()} == {str(alias / "app.py")}
    assert cast(list[dict], graph.source_policy["roots"])[0]["path"] == str(alias)


@pytest.mark.parametrize("names", [(), ("same", "same"), ("",), ("has:colon",)])
def test_invalid_roots(names: tuple[str, ...], tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        SourcePolicy(tuple(SourceRoot(str(tmp_path), name) for name in names))


_FIXTURES = dict(reversed(PARSER_FIXTURES))


def test_source_contract_covers_every_registered_language() -> None:
    assert set(_FIXTURES) == set(supported_languages())


@pytest.mark.parametrize("language", supported_languages())
def test_source_contract_preserves_existing_entrypoints(language: str, tmp_path: Path) -> None:
    fixture, _ = fixture_pair(language, _FIXTURES[language])
    selected = tmp_path / "vendor" / "selected"
    _write(selected, fixture.name, fixture.read_text())
    original = parse_directory(str(selected), language)
    original_tags = detect_entrypoints(original, str(selected))
    graph = parse_roots(
        SourcePolicy(
            (
                SourceRoot(str(tmp_path), "app"),
                SourceRoot(str(selected), "selected"),
                SourceRoot(str(selected), "overlap"),
            )
        ),
        language,
    )
    assert sum(graph.source_file_counts.values()) == 1
    assert set(graph.nodes) == {f"selected::{node}" for node in original.nodes}
    tags = detect_entrypoints(graph, str(selected))
    assert tags == {f"selected::{node}": tag for node, tag in original_tags.items()}
    graph.entrypoints.update(tags)
    engine = QueryEngine.from_graph(graph)
    module = next(unit for unit in graph.nodes.values() if unit.kind == NodeKind.MODULE)
    # A supplied fact exercises the shared API; it is not automatic discovery output.
    fact = EntrypointEvidence(
        module.id,
        "reference",
        "supplied_contract",
        module.location,
        unresolved=("invocation_context",),
    )
    engine.add_entrypoint_evidence(fact)
    assert engine.entrypoint_candidates(module.id)[0]["unresolved"] == ["invocation_context"]
    assert engine.entrypoint_evidence("missing") == []
    assert json.loads(engine.to_json())["entrypoint_evidence"] == engine.entrypoint_evidence()
