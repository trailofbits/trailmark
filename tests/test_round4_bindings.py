"""Callable declarations and call-site spans preserve lexical targets."""

from pathlib import Path

import pytest

from trailmark.models import EdgeConfidence, EdgeKind
from trailmark.parse import parse_directory, parse_file


@pytest.mark.parametrize("extension", ["js", "ts"])
@pytest.mark.parametrize("export", ["", "export "])
def test_module_callable_variables_link(tmp_path: Path, extension: str, export: str) -> None:
    path = tmp_path / f"app.{extension}"
    path.write_text(
        f"{export}const fmt = (x) => x.toString();\n"
        f"{export}const twice = function (x) {{ return fmt(x * 2); }};\n"
        "function main(i) { return fmt(twice(i)); }\n"
    )
    graph = parse_file(str(path))
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS and e.source_id == "app:main"]
    assert {e.target_id for e in calls} == {"app:fmt", "app:twice"}
    assert all(e.confidence == EdgeConfidence.CERTAIN for e in calls)
    assert any(e.source_id == "app:twice" and e.target_id == "app:fmt" for e in graph.edges)


@pytest.mark.parametrize("extension", ["js", "ts"])
def test_noncallable_module_variable_stays_blocked(tmp_path: Path, extension: str) -> None:
    path = tmp_path / f"app.{extension}"
    path.write_text("const fmt = 5; function main() { fmt(); }")
    graph = parse_file(str(path))
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)
    assert dict(call.attributes)["binding_blocked"]


@pytest.mark.parametrize("extension", ["py", "js", "ts"])
@pytest.mark.parametrize("chain", ["sf().commit()", "sf().query(1).all()", "sf().start().finish()"])
def test_chained_calls_retain_each_target(tmp_path: Path, extension: str, chain: str) -> None:
    path = tmp_path / f"app.{extension}"
    source = (
        f"def run(sf):\n return {chain}\n"
        if extension == "py"
        else (f"function run(sf) {{ return {chain}; }}")
    )
    path.write_text(source)
    graph = parse_file(str(path))
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS and e.source_id == "app:run"]
    names = chain.replace("(1)", "()").replace("()", "").split(".")
    assert len(calls) == len(names)
    assert len([e for e in calls if dict(e.attributes).get("binding_blocked")]) == 1
    for name in names[1:]:
        assert any(
            e.target_id.endswith("." + name) or e.target_id.endswith(":" + name) for e in calls
        )


@pytest.mark.parametrize("extension", ["js", "ts"])
def test_named_function_promise_chain(tmp_path: Path, extension: str) -> None:
    path = tmp_path / f"app.{extension}"
    path.write_text("function f(){} function run(){ return f().then().catch(); }")
    graph = parse_file(str(path))
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS and e.source_id == "app:run"]
    assert len(calls) == 3
    assert len([e for e in calls if e.target_id == "app:f"]) == 1


@pytest.mark.parametrize(
    ("parameters", "body", "names"),
    [
        ("*rest", "rest", ["rest"]),
        ("x, &blk", "blk", ["blk"]),
        ("(a, b)", "log(a, b)", ["a", "b"]),
        ("opt = 1", "opt", ["opt"]),
        ("kw:", "kw", ["kw"]),
    ],
)
def test_ruby_block_parameter_patterns(
    tmp_path: Path, parameters: str, body: str, names: list[str]
) -> None:
    path = tmp_path / "app.rb"
    methods = "\n".join(f" def {name}; end" for name in names)
    path.write_text(
        f"class C\n{methods}\n def run(i)\n i.each {{ |{parameters}| {body} }}\n end\nend\n"
    )
    graph = parse_file(str(path))
    targets = {e.target_id for e in graph.edges if e.kind == EdgeKind.CALLS}
    assert all(f"app:C.{name}" not in targets for name in names)


@pytest.mark.parametrize("extension", ["js", "ts"])
@pytest.mark.parametrize("nested", [False, True])
def test_require_shadowing_reaches_nested_callables(
    tmp_path: Path, extension: str, nested: bool
) -> None:
    source = (
        'function o(){ const {cb:h}=require("./x"); return function inner(h){ return ()=>h(); }; }'
        if nested
        else 'const {cb}=require("./x");function outer(cb){ return [1].map(()=>cb()); }'
    )
    (tmp_path / f"app.{extension}").write_text(source)
    (tmp_path / f"x.{extension}").write_text("export function cb(){}")
    graph = parse_directory(str(tmp_path), "auto")
    assert not any(e.kind == EdgeKind.CALLS and e.target_id == "x:cb" for e in graph.edges)


@pytest.mark.parametrize("extension", ["js", "ts"])
def test_single_arrow_parameter_shadows_require_in_nested_body(
    tmp_path: Path, extension: str
) -> None:
    (tmp_path / f"app.{extension}").write_text(
        'const {cb}=require("./x"); const outer = cb => () => cb();'
    )
    (tmp_path / f"x.{extension}").write_text("export function cb(){}")
    graph = parse_directory(str(tmp_path), "auto")
    assert not any(e.kind == EdgeKind.CALLS and e.target_id == "x:cb" for e in graph.edges)


def test_require_prefers_file_before_directory(tmp_path: Path) -> None:
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib/index.js").write_text("export function cb(){}")
    (tmp_path / "lib.js").write_text("export function cb(){}")
    (tmp_path / "app.js").write_text('const {cb}=require("./lib");function run(){cb();}')
    graph = parse_directory(str(tmp_path), "javascript")
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS and e.source_id == "app:run")
    assert call.target_id == "lib:cb"


def test_ruby_block_defaults_keep_calls_but_not_parameter_names(tmp_path: Path) -> None:
    path = tmp_path / "app.rb"
    path.write_text(
        "class C\n def fallback; end\n def opt; end\n"
        " def run(xs)\n xs.each { |opt = fallback| consume(opt) }\n end\nend\n"
    )
    graph = parse_file(str(path))
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert any(e.target_id == "app:C.fallback" for e in calls)
    assert not any(e.target_id == "app:C.opt" for e in calls)
