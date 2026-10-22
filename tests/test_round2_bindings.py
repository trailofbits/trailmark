"""Regression controls for runtime bindings and preserved callable reachability."""

from pathlib import Path

import pytest

from trailmark.models import CodeEdge, CodeGraph, EdgeConfidence, EdgeKind
from trailmark.parse import parse_directory, parse_file
from trailmark.query.api import QueryEngine


def _parse(tmp_path: Path, extension: str, source: str) -> CodeGraph:
    path = tmp_path / f"case.{extension}"
    path.write_text(source)
    return parse_file(str(path))


def _calls(graph: CodeGraph, owner: str) -> list[CodeEdge]:
    return [
        edge
        for edge in graph.edges
        if edge.kind == EdgeKind.CALLS
        and edge.source_id == owner
        and not dict(edge.attributes).get("deferred")
    ]


@pytest.mark.parametrize("relative", [False, True])
def test_static_header_and_included_source_linkage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: bool
) -> None:
    (tmp_path / "include").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "include/util.h").write_text(
        "#ifndef UTIL_H\n#define UTIL_H\nstatic inline int helper(int x) { return x; }\n#endif\n"
    )
    (tmp_path / "include/wrapper.h").write_text('#include "util.h"\n')
    (tmp_path / "src/api.c").write_text(
        '#include "../include/wrapper.h"\nint api(int x) { return helper(x); }'
    )
    (tmp_path / "src/other.c").write_text("static int private_fn(void) { return 1; }")
    (tmp_path / "src/user.c").write_text("int user(void) { return private_fn(); }")
    (tmp_path / "src/included.c").write_text(
        '#include "other.c"\nint included(void) { return private_fn(); }'
    )
    monkeypatch.chdir(tmp_path.parent)
    root = tmp_path.name if relative else str(tmp_path)
    graph = parse_directory(root, "c")
    assert [(e.target_id, e.confidence) for e in _calls(graph, "src.api:api")] == [
        ("include.util:helper", EdgeConfidence.INFERRED)
    ]
    assert _calls(graph, "src.user:user")[0].target_id.startswith("proxy.")
    assert _calls(graph, "src.included:included")[0].target_id == "src.other:private_fn"


def test_python_runtime_redefinitions_and_overload_stubs(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        "py",
        """from typing import overload
@overload
def parse(x: int) -> int: ...
@overload
def parse(x: str) -> str: ...
def parse(x): return impl(x)
class User:
 @property
 def name(self): return getter()
 @name.setter
 def name(self, v): setter(v)
def main(): return parse(1)
def outer():
 def inner(): first()
 def inner(): second()
 inner()
""",
    )
    assert {u.id for u in graph.nodes.values() if u.name == "parse"} == {"case:parse"}
    assert "case:User.name" in graph.nodes
    assert "case:User.name@9_1" in graph.nodes
    assert _calls(graph, "case:main")[0].target_id == "case:parse"
    assert _calls(graph, "case:outer")[0].target_id == "case:outer.inner"
    assert _calls(graph, "case:outer.inner")[0].target_id.endswith(":case:second")


@pytest.mark.parametrize(
    ("extension", "source"),
    [
        (
            "kt",
            "fun g() {}\nclass A { val g: () -> Unit = {}; fun m() { g() } }\n"
            "fun outer() { class L { val g = 1 }; g() }",
        ),
        (
            "swift",
            "func g() {}\nclass A { let g: () -> Void = {}; func m() { g() } }\n"
            "func outer() { class L { let g = 1 }; g() }",
        ),
        (
            "dart",
            "void g() {}\nclass A { final void Function() g = () {}; void m() { g(); } }\n"
            "void outer() { class L { final g = 1; } g(); }",
        ),
    ],
)
def test_class_members_shadow_only_inside_class(
    tmp_path: Path, extension: str, source: str
) -> None:
    graph = _parse(tmp_path, extension, source)
    method = next(u.id for u in graph.nodes.values() if u.name == "m")
    outer = next(u.id for u in graph.nodes.values() if u.name == "outer")
    assert dict(_calls(graph, method)[0].attributes).get("binding_blocked")
    assert _calls(graph, outer)[0].target_id == "case:g"
    assert _calls(graph, outer)[0].confidence == EdgeConfidence.CERTAIN


@pytest.mark.parametrize("extension", ["js", "ts"])
def test_same_line_nested_declarations_keep_their_owner(tmp_path: Path, extension: str) -> None:
    graph = _parse(
        tmp_path,
        extension,
        "function a(){function g(){return one()}return g()}"
        "function b(){function g(){return two()}return g()}",
    )
    assert _calls(graph, "case:a")[0].target_id == "case:a.g"
    assert _calls(graph, "case:b")[0].target_id == "case:b.g"


def test_ruby_explicit_method_calls_and_lambdas(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        "rb",
        """def compute(x); x; end
def validate(x); x; end
def run(items)
 before = compute(1)
 compute = 3
 items.map { |i| compute(i) }
 checker = ->(x) { validate(x) }
end
class Cart
 def total(items); items; end
 def run(items); self.total = total(items); end
end
""",
    )
    assert sum(e.target_id == "case:compute" for e in _calls(graph, "case:run")) == 2
    assert any(e.target_id == "case:validate" for e in _calls(graph, "case:run"))
    assert len([e for e in _calls(graph, "case:Cart.run") if e.target_id == "case:Cart.total"]) == 1
    assert "validate" in {
        u["name"]
        for u in QueryEngine.from_directory(str(tmp_path), language="ruby").reachable_from(
            "case:run"
        )
    }


@pytest.mark.parametrize("extension", ["js", "ts"])
def test_arrows_inherit_this_but_function_expressions_do_not(
    tmp_path: Path, extension: str
) -> None:
    graph = _parse(
        tmp_path,
        extension,
        """class Handler {
 step(d) { return d; }
 handle(emitter, items) {
  emitter.on('data', d => this.step(d));
  items.map(x => this.step(x));
  items.map(function(x) { this.step(x); });
 }
}""",
    )
    edges = [
        e
        for e in graph.edges
        if e.kind == EdgeKind.CALLS and not dict(e.attributes).get("deferred")
    ]
    assert sum(e.target_id == "case:Handler.step" for e in edges) == 2
    assert sum(e.target_id.startswith("proxy.unresolved:this.step") for e in edges) == 1


@pytest.mark.parametrize("with_module", [False, True])
def test_require_shadows_same_file_function(tmp_path: Path, with_module: bool) -> None:
    (tmp_path / "app.js").write_text(
        "function sanitize() {} function handler(x) { "
        "const { sanitize } = require('./util'); return sanitize(x); }"
    )
    if with_module:
        (tmp_path / "util.js").write_text("export function sanitize(x) { return x; }")
    graph = parse_directory(str(tmp_path), "javascript")
    calls = _calls(graph, "app:handler")
    assert all(e.target_id != "app:sanitize" for e in calls)
    assert any(
        e.target_id == "util:sanitize" if with_module else e.target_id.endswith(":util:sanitize")
        for e in calls
    )


def test_cpp_declarator_shadows_inside_initializer(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        "cpp",
        "int fib(int n) { return n; } int run(){ std::function<int(int)> fib = "
        "[&](int n){ return fib(n-1); }; return 0; }",
    )
    calls = [
        e
        for e in graph.edges
        if e.kind == EdgeKind.CALLS and not dict(e.attributes).get("deferred")
    ]
    assert not any(e.target_id == "case:fib" for e in calls)
    assert any(dict(e.attributes).get("binding_blocked") for e in calls)


def test_python_lambda_default_parameter_is_local(tmp_path: Path) -> None:
    graph = _parse(tmp_path, "py", "def x(): pass\ndef run(): return lambda x=None: x()\n")
    calls = _calls(graph, "case:run")
    assert len(calls) == 1
    assert dict(calls[0].attributes).get("binding_blocked")


@pytest.mark.parametrize("extension", ["js", "ts"])
def test_object_method_arrow_does_not_capture_surrounding_class(
    tmp_path: Path, extension: str
) -> None:
    graph = _parse(
        tmp_path,
        extension,
        "class Handler { step() {} handle() { "
        "const obj = { run() { return () => this.step(); } }; return obj; } }",
    )
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS and edge.location]
    arrow = next(unit.id for unit in graph.nodes.values() if unit.name.startswith("$lambda"))
    (edge,) = [edge for edge in calls if edge.source_id == arrow]
    assert edge.target_id.startswith("proxy.")
    assert edge.target_id != "case:Handler.step"
