"""Lexical imports and class members shadow unrelated global callables."""

from pathlib import Path

import pytest

from trailmark.models import EdgeKind
from trailmark.parse import parse_directory, parse_file


@pytest.mark.parametrize("extension", ["js", "ts"])
@pytest.mark.parametrize("inner", ["function inner(cb){return cb();}", "(cb)=>cb()"])
def test_require_is_shadowed_by_parameters(tmp_path: Path, extension: str, inner: str) -> None:
    path = tmp_path / f"a.{extension}"
    path.write_text('function outer(){const {cb}=require("./x");return ' + inner + "}")
    graph = parse_file(str(path))
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS and e.target_id.endswith("cb")]
    assert calls and all(dict(e.attributes).get("binding_blocked") for e in calls)


@pytest.mark.parametrize("extension", ["js", "ts"])
def test_require_destructuring_alias(tmp_path: Path, extension: str) -> None:
    (tmp_path / f"a.{extension}").write_text(
        'const {sanitize:clean}=require("./util");function run(){clean();}'
    )
    (tmp_path / f"util.{extension}").write_text(
        "export function sanitize(){} export function clean(){}"
    )
    graph = parse_directory(str(tmp_path), "auto")
    edge = next(e for e in graph.edges if e.source_id == "a:run" and e.kind == EdgeKind.CALLS)
    assert edge.target_id == "util:sanitize"


def test_whole_module_require_is_not_a_named_export(tmp_path: Path) -> None:
    (tmp_path / "a.js").write_text('const helper=require("./util");function run(){helper();}')
    (tmp_path / "util.js").write_text("export function helper(){}")
    graph = parse_directory(str(tmp_path), "javascript")
    edge = next(e for e in graph.edges if e.source_id == "a:run" and e.kind == EdgeKind.CALLS)
    assert edge.target_id.startswith("proxy.")


@pytest.mark.parametrize("symlink", [False, True])
def test_require_directory_index_and_lexical_root(tmp_path: Path, symlink: bool) -> None:
    (tmp_path / "lib").mkdir()
    (tmp_path / "app").mkdir()
    filename = "util.js" if symlink else "index.js"
    (tmp_path / "lib" / filename).write_text("export function clean(){}")
    imported = "../lib/util" if symlink else "../lib"
    (tmp_path / "app/a.js").write_text(
        f'function run(){{const {{clean}}=require("{imported}");clean();}}'
    )
    root = tmp_path
    if symlink:
        root = tmp_path.parent / (tmp_path.name + "-alias")
        root.symlink_to(tmp_path, target_is_directory=True)
    graph = parse_directory(str(root), "javascript")
    edge = next(
        e
        for e in graph.edges
        if e.source_id == "app.a:run"
        and e.kind == EdgeKind.CALLS
        and e.target_id.endswith(":clean")
    )
    assert edge.target_id == ("lib.util:clean" if symlink else "lib.index:clean")
    assert dict(edge.attributes)["import_binding"]


def test_node_require_has_valid_proxy_identity(tmp_path: Path) -> None:
    path = tmp_path / "a.js"
    path.write_text('function run(){const {join}=require("node:path");join();}')
    graph = parse_file(str(path))
    edge = next(
        e
        for e in graph.edges
        if e.source_id == "a:run" and e.kind == EdgeKind.CALLS and e.target_id.endswith(":join")
    )
    assert edge.target_id == "proxy.unresolved:node.path:join"


def test_require_resolution_is_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import trailmark.parsers._bindings as bindings

    original = bindings.module_id_from_path
    resolutions = []

    def resolve(path: str) -> str:
        resolutions.append(path)
        return original(path)

    monkeypatch.setattr(bindings, "module_id_from_path", resolve)
    path = tmp_path / "a.js"
    path.write_text('function run(){const {cb}=require("./x");' + "cb();" * 20000 + "}")
    graph = parse_file(str(path))
    assert len(resolutions) == 1
    assert len([e for e in graph.edges if dict(e.attributes).get("import_binding")]) == 20000


@pytest.mark.parametrize(
    ("extension", "source"),
    [
        ("kt", "fun g() {}\nclass A { fun m(){ g() }; val g: ()->Unit = {} }"),
        (
            "kt",
            "fun create() {}\n"
            "class A {\n"
            " fun m(){create()}\n"
            " companion object {\n"
            " val create: ()->Int = {1}\n"
            " }\n"
            "}",
        ),
        ("kt", "fun g() {}\nenum class A { X; fun m(){g()}; val g: ()->Unit = {} }"),
        ("swift", "func g() {}\nclass A { func m(){g()}; var g: ()->Void = {} }"),
        ("swift", "func g() {}\nenum A { case x; func m(){g()}; var g: ()->Void { return {} } }"),
        (
            "swift",
            "func g() {}\nclass A {}\nextension A { func m(){g()}; var g: ()->Void {return {}} }",
        ),
        ("dart", "void g() {}\nclass A { void m(){g();} final g = () {}; }"),
        (
            "kt",
            "fun g() {}\nclass A {\n inner class B {\n fun m(){g()}\n }\n val g: ()->Unit = {}\n}",
        ),
    ],
)
def test_members_shadow_globals_throughout_class(
    tmp_path: Path, extension: str, source: str
) -> None:
    path = tmp_path / f"a.{extension}"
    path.write_text(source)
    graph = parse_file(str(path))
    edges = [e for e in graph.edges if e.kind == EdgeKind.CALLS and e.source_id.endswith(".m")]
    assert edges and all(dict(e.attributes).get("binding_blocked") for e in edges)
