"""Header visibility prefers verified includes and respects extern declarations."""

from pathlib import Path

import pytest

from trailmark.models import EdgeKind
from trailmark.parse import parse_directory, parse_file


@pytest.mark.parametrize("extensions", [("c", "h"), ("cpp", "hpp")])
@pytest.mark.parametrize("reverse", [False, True])
def test_header_static_tie_uses_include_closure(
    tmp_path: Path, extensions: tuple[str, str], reverse: bool
) -> None:
    source, header = extensions
    include = "" if reverse else f'#include "first.{header}"\n'
    (tmp_path / f"app.{source}").write_text(include + "int run(){return helper();}")
    for name in ("first", "second"):
        prefix = f'#include "app.{source}"\n' if reverse and name == "first" else ""
        (tmp_path / f"{name}.{header}").write_text(prefix + "static int helper(){return 1;}")
    graph = parse_directory(str(tmp_path), "auto")
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS and e.source_id == "app:run")
    assert call.target_id == "first:helper"
    assert not dict(call.attributes).get("include_unverified")


@pytest.mark.parametrize("extensions", [("c", "h"), ("cpp", "hpp")])
def test_extern_header_prevents_unverified_static_fallback(
    tmp_path: Path, extensions: tuple[str, str]
) -> None:
    source, header = extensions
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / f"a/util.{header}").write_text("static int helper(int x){return x;}")
    (tmp_path / f"b/ext.{header}").write_text("int helper(int);")
    (tmp_path / f"b/main.{source}").write_text(
        f'#include "ext.{header}"\nint run(){{return helper(1);}}'
    )
    graph = parse_directory(str(tmp_path), "auto")
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS and e.source_id == "b.main:run")
    assert call.target_id.startswith("proxy.")


def test_unverified_header_fallback_is_marked(tmp_path: Path) -> None:
    (tmp_path / "app.c").write_text("int run(){return helper();}")
    (tmp_path / "util.h").write_text("static int helper(){return 1;}")
    graph = parse_directory(str(tmp_path), "c")
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)
    assert call.target_id == "util:helper"
    assert dict(call.attributes)["include_unverified"] is True


@pytest.mark.parametrize("storage", ["", "extern "])
@pytest.mark.parametrize("extension", ["c", "cpp"])
def test_block_function_prototype_is_an_external_alias(
    tmp_path: Path, storage: str, extension: str
) -> None:
    path = tmp_path / f"app.{extension}"
    path.write_text(
        "int helper(int x){return x;} int run(){" + storage + "int helper(int);return helper(1);}"
    )
    graph = parse_file(str(path))
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)
    assert call.target_id == "app:helper" and call.confidence.value == "certain"


def test_function_pointer_still_shadows_global(tmp_path: Path) -> None:
    path = tmp_path / "app.c"
    path.write_text("int helper(int x){return x;} int run(){int (*helper)(int);return helper(1);}")
    graph = parse_file(str(path))
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)
    assert dict(call.attributes)["binding_blocked"]
