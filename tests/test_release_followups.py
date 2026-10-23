"""Regression coverage for the remaining v0.7 binding and ownership gaps."""

import os
from pathlib import Path

import pytest

from trailmark.models import CodeEdge, CodeGraph, EdgeConfidence, EdgeKind, NodeKind
from trailmark.parse import parse_directory, parse_file


def _graph(root: Path, source: str, extension: str) -> CodeGraph:
    path = root / f"app.{extension}"
    path.write_text(source)
    return parse_file(str(path))


def _calls(graph: CodeGraph) -> list[CodeEdge]:
    return [
        edge
        for edge in graph.edges
        if edge.kind == EdgeKind.CALLS and not dict(edge.attributes).get("deferred")
    ]


@pytest.mark.parametrize("extension", ["c", "cpp"])
def test_block_external_prototype_is_scoped(tmp_path: Path, extension: str) -> None:
    (tmp_path / f"app.{extension}").write_text(
        "int run(){int helper(int);return helper(1);}\nint other(){return helper(2);}"
    )
    (tmp_path / ("util.h" if extension == "c" else "util.hpp")).write_text(
        "static int helper(int x){return x;}"
    )
    graph = parse_directory(str(tmp_path), "c" if extension == "c" else "cpp")
    calls = {edge.source_id: edge for edge in _calls(graph)}
    assert calls["app:run"].target_id != "util:helper"
    assert calls["app:other"].target_id == "util:helper"


@pytest.mark.parametrize("language", ["python", "auto"])
@pytest.mark.parametrize("last", ["class Thing: pass", "def Thing(): pass"])
def test_python_import_uses_final_definition(tmp_path: Path, last: str, language: str) -> None:
    (tmp_path / "a.py").write_text(f"def Thing(): pass\n{last}\n")
    (tmp_path / "b.py").write_text("from a import Thing\ndef run(): return Thing()\n")
    graph = parse_directory(str(tmp_path), language)
    edge = next(edge for edge in _calls(graph) if edge.source_id == "b:run")
    assert edge.target_id == "a:Thing"
    assert graph.nodes[edge.target_id].kind == (
        NodeKind.CLASS if last.startswith("class") else NodeKind.FUNCTION
    )


@pytest.mark.parametrize("extension", ["js", "ts"])
@pytest.mark.parametrize(
    "value", ["function*(){yield helper();}", "async function*(){helper();}", "((()=>helper()))"]
)
def test_callable_initializer_wrappers(tmp_path: Path, extension: str, value: str) -> None:
    graph = _graph(
        tmp_path, f"function helper(){{}} const f={value}; function run(){{f();}}", extension
    )
    calls = _calls(graph)
    assert any(edge.source_id == "app:run" and edge.target_id == "app:f" for edge in calls)
    assert sum(edge.target_id == "app:helper" for edge in calls) == 1


@pytest.mark.parametrize(
    "wrapper", ["((()=>helper()) as (()=>void))", "(()=>helper()) satisfies (()=>void)"]
)
def test_typescript_callable_assertions(tmp_path: Path, wrapper: str) -> None:
    graph = _graph(
        tmp_path, f"function helper(){{}} const f={wrapper}; function run(){{f();}}", "ts"
    )
    assert any(edge.source_id == "app:run" and edge.target_id == "app:f" for edge in _calls(graph))


@pytest.mark.parametrize("extension", ["js", "ts"])
@pytest.mark.parametrize(
    "definition,owner",
    [
        ("function keygen(seed=randomBytes(32)){}", "app:keygen"),
        ("const keygen=(seed=randomBytes(32))=>seed;", "app:keygen"),
        ("function keygen({seed=randomBytes(32)}={}){}", "app:keygen"),
        ("class C {keygen(seed=randomBytes(32)){}}", "app:C.keygen"),
    ],
)
def test_default_parameter_calls(
    tmp_path: Path, extension: str, definition: str, owner: str
) -> None:
    graph = _graph(tmp_path, "function randomBytes(n){return n;}\n" + definition, extension)
    calls = [edge for edge in _calls(graph) if edge.target_id == "app:randomBytes"]
    assert len(calls) == 1
    assert calls[0].source_id == owner
    assert calls[0].location is not None
    assert calls[0].location.start_line == 2


@pytest.mark.parametrize("extension", ["js", "ts"])
def test_default_callback_keeps_its_body(tmp_path: Path, extension: str) -> None:
    graph = _graph(tmp_path, "function work(){} function run(cb=()=>work()){}", extension)
    calls = [edge for edge in _calls(graph) if edge.target_id == "app:work"]
    assert len(calls) == 1 and calls[0].source_id.startswith("app:run.")


@pytest.mark.parametrize("extension", ["js", "ts"])
def test_accessor_bodies_are_distinct(tmp_path: Path, extension: str) -> None:
    graph = _graph(
        tmp_path,
        "function read(){} function write(x){} class C {"
        "get v(){return read();} set v(x){write(x);}}",
        extension,
    )
    calls = _calls(graph)
    assert len(calls) == 2
    assert len({edge.source_id for edge in calls}) == 2
    for edge in calls:
        owner = graph.nodes[edge.source_id]
        assert edge.location is not None
        assert owner.location.start_col is not None and owner.location.end_col is not None
        assert edge.location.start_col is not None
        assert owner.location.start_col <= edge.location.start_col <= owner.location.end_col


@pytest.mark.parametrize(
    "body", ["xs.each { _1 }", "xs.each { _9 }", "xs.each { -> { _1 } }", "-> { _1 }"]
)
def test_ruby_numbered_parameters_are_values(tmp_path: Path, body: str) -> None:
    graph = _graph(tmp_path, f"def run(xs)\n {body}\nend\n", "rb")
    assert not any(
        dict(edge.attributes).get("ruby_bare_name") in {"_1", "_9"} for edge in _calls(graph)
    )


def test_ruby_numbered_explicit_call(tmp_path: Path) -> None:
    graph = _graph(tmp_path, "def _1;end\ndef run(xs)\n xs.each { _1() }\nend", "rb")
    assert any(edge.target_id == "app:_1" for edge in _calls(graph))


def test_move_self_is_current_module(tmp_path: Path) -> None:
    graph = _graph(
        tmp_path,
        "module 0x1::m {fun local(){} fun run(){Self::local();Self::missing();}} "
        "module 0x1::n {fun local(){}}",
        "move",
    )
    calls = _calls(graph)
    assert calls[0].target_id == "app:0x1::m:local"
    assert calls[0].confidence == EdgeConfidence.CERTAIN
    assert calls[1].confidence == EdgeConfidence.UNCERTAIN


def test_csharp_overload_descendants_use_final_owner(tmp_path: Path) -> None:
    graph = _graph(
        tmp_path,
        "class K {void M(int x){void L(){void N(){A();}N();}L();}\n"
        "void M(string x){void L(){B();}L();}\nvoid A(){} void B(){}}",
        "cs",
    )
    for edge in graph.edges:
        if edge.kind == EdgeKind.CONTAINS and graph.nodes[edge.source_id].kind in {
            NodeKind.FUNCTION,
            NodeKind.METHOD,
        }:
            assert edge.target_id.startswith(edge.source_id + ".")
    calls = _calls(graph)
    assert next(edge for edge in calls if edge.target_id == "app:K.A").source_id.endswith(".L.N")
    assert next(edge for edge in calls if edge.target_id == "app:K.B").source_id.endswith(".L")


@pytest.mark.parametrize("extension", ["py", "js", "ts"])
def test_returned_call_is_indirect(tmp_path: Path, extension: str) -> None:
    source = (
        "def k(): pass\ndef run(): k()()()\n"
        if extension == "py"
        else "function k(){} function run(){k()()();}"
    )
    graph = _graph(tmp_path, source, extension)
    calls = _calls(graph)
    assert len(calls) == 3
    assert sum(edge.target_id == "app:k" for edge in calls) == 1
    indirect = [edge for edge in calls if edge.target_id != "app:k"]
    assert all(edge.confidence == EdgeConfidence.UNCERTAIN for edge in indirect)
    assert len({edge.target_id for edge in indirect}) == 2


@pytest.mark.parametrize("conditional", ["if flag:", "for _ in [1]:"])
def test_python_conditional_parameter_rebinding(tmp_path: Path, conditional: str) -> None:
    graph = _graph(
        tmp_path, f"def run(f, flag):\n {conditional}\n  def f(): pass\n return f()\n", "py"
    )
    edge = _calls(graph)[0]
    assert edge.confidence == EdgeConfidence.UNCERTAIN
    assert dict(edge.attributes).get("binding_blocked")


def test_python_defining_branch_and_unconditional_binding(tmp_path: Path) -> None:
    graph = _graph(
        tmp_path,
        "def run(f, flag):\n if flag:\n  def f(): pass\n  f()\n def f(): pass\n f()\n",
        "py",
    )
    calls = _calls(graph)
    assert len(calls) == 2 and all(edge.confidence == EdgeConfidence.CERTAIN for edge in calls)
    assert len({edge.target_id for edge in calls}) == 2


@pytest.mark.parametrize("extension", ["js", "ts"])
@pytest.mark.parametrize(
    "value", ["(function named(){helper();})", "(function* named(){yield helper();})"]
)
def test_nested_callable_initializer_keeps_body_owner(
    tmp_path: Path, extension: str, value: str
) -> None:
    graph = _graph(
        tmp_path, f"function helper(){{}} function run(){{const f={value}; f();}}", extension
    )
    calls = _calls(graph)
    assert len(calls) == 2
    assert any(edge.source_id == "app:run" and edge.target_id == "app:run.f" for edge in calls)
    assert any(edge.source_id == "app:run.f" and edge.target_id == "app:helper" for edge in calls)


@pytest.mark.parametrize("extension", ["js", "ts"])
def test_wrapped_noncallable_still_shadows(tmp_path: Path, extension: str) -> None:
    graph = _graph(tmp_path, "function f(){} function run(){const f=(42);f();}", extension)
    assert all(dict(edge.attributes).get("binding_blocked") for edge in _calls(graph))


@pytest.mark.parametrize("extension", ["py", "js", "ts"])
def test_parenthesized_returned_call_keeps_both_sites(tmp_path: Path, extension: str) -> None:
    source = (
        "def k(): pass\ndef run(): ((k()))()\n"
        if extension == "py"
        else "function k(){} function run(){((k()))();}"
    )
    calls = _calls(_graph(tmp_path, source, extension))
    assert len(calls) == 2
    assert sum(edge.target_id == "app:k" for edge in calls) == 1
    assert sum(dict(edge.attributes).get("indirect_call") is True for edge in calls) == 1


@pytest.mark.parametrize("suffix", ["h", "hpp"])
@pytest.mark.parametrize("include", [False, True])
def test_c_prototype_fallback_preserves_verified_includes(
    tmp_path: Path, suffix: str, include: bool
) -> None:
    language, extension = ("c", "c") if suffix == "h" else ("cpp", "cpp")
    header = f"util.{suffix}"
    (tmp_path / header).write_text("static int helper(int x){return x;}")
    prefix = f'#include "{header}"\n' if include else ""
    (tmp_path / f"app.{extension}").write_text(
        prefix + "int run(){extern int helper(int);return helper(1);}"
    )
    graph = parse_directory(str(tmp_path), language)
    edge = next(edge for edge in _calls(graph) if edge.source_id == "app:run")
    assert (edge.target_id == "util:helper") == include


def test_invalid_c_static_block_prototype_stays_blocked(tmp_path: Path) -> None:
    graph = _graph(
        tmp_path,
        "int run(){static int helper(int);return helper(1);}",
        "c",
    )
    assert dict(_calls(graph)[0].attributes).get("binding_blocked")


@pytest.mark.parametrize(
    "body",
    [
        "if flag:\n  def f(): pass\n else:\n  def f(): pass\n",
        "if flag:\n  if flag:\n   def f(): pass\n",
    ],
)
def test_python_branch_definitions_remain_ambiguous_after_merge(tmp_path: Path, body: str) -> None:
    graph = _graph(tmp_path, "def run(f, flag):\n " + body + " return f()\n", "py")
    assert _calls(graph)[0].confidence == EdgeConfidence.UNCERTAIN


def test_ruby_numbered_names_outside_blocks_remain_calls(tmp_path: Path) -> None:
    graph = _graph(tmp_path, "def _1;end\ndef run; _1; end", "rb")
    assert any(edge.target_id == "app:_1" for edge in _calls(graph))


def test_nested_csharp_overloads_keep_ambiguous_target_under_final_owner(tmp_path: Path) -> None:
    graph = _graph(
        tmp_path,
        "class K {void M(int x){void L(int y){} void L(string y){} L(1);}\n"
        "void M(string x){void L(){} L();}}",
        "cs",
    )
    assert not any("K.M.L" in edge.target_id for edge in _calls(graph))
    ambiguous = next(edge for edge in _calls(graph) if edge.confidence == EdgeConfidence.UNCERTAIN)
    assert ambiguous.source_id + ".L" in ambiguous.target_id


@pytest.mark.parametrize("extension", ["js", "ts"])
def test_wrapped_class_arrow_keeps_method_kind(tmp_path: Path, extension: str) -> None:
    graph = _graph(tmp_path, "function work(){} class C{f=(()=>work());}", extension)
    assert graph.nodes["app:C.f"].kind == NodeKind.METHOD
    assert _calls(graph)[0].source_id == "app:C.f"


def test_deep_callable_wrappers_are_iterative(tmp_path: Path) -> None:
    graph = _graph(
        tmp_path,
        "function work(){} const f="
        + "(" * 3000
        + "()=>work()"
        + ")" * 3000
        + ";function run(){f();}",
        "ts",
    )
    assert any(edge.source_id == "app:run" and edge.target_id == "app:f" for edge in _calls(graph))


@pytest.mark.parametrize(
    "extension,value",
    [
        ("js", "(/* before */()=>work()/* after */)"),
        ("ts", "(/* before */()=>work()) satisfies (()=>void)"),
        ("ts", "<(()=>void)>/* asserted */(()=>work())"),
    ],
)
def test_comments_in_callable_wrappers_preserve_binding(
    tmp_path: Path, extension: str, value: str
) -> None:
    graph = _graph(
        tmp_path, f"function work(){{}} const f={value};function run(){{f();}}", extension
    )
    assert any(edge.source_id == "app:run" and edge.target_id == "app:f" for edge in _calls(graph))


def test_returned_call_proxy_positions_are_one_based(tmp_path: Path) -> None:
    graph = _graph(tmp_path, "def k(): pass\ndef run(): k()()\n", "py")
    edge = next(edge for edge in _calls(graph) if dict(edge.attributes).get("indirect_call"))
    assert dict(edge.attributes)["raw_target"] == "app:$returned@2_11_30"


@pytest.mark.skipif(os.name == "nt", reason="Windows forbids colons in filenames")
def test_python_final_bindings_keep_distinct_modules_with_colons(tmp_path: Path) -> None:
    (tmp_path / "a:b.py").write_text("def f(): pass\n")
    (tmp_path / "a:c.py").write_text("def f(): pass\n")
    (tmp_path / "run.py").write_text("def run(): f()\n")
    graph = parse_directory(str(tmp_path), "python")
    edge = next(edge for edge in _calls(graph) if edge.source_id == "run:run")
    assert edge.confidence == EdgeConfidence.UNCERTAIN
    assert edge.target_id.startswith("proxy.unresolved:")
