"""Declaration identities and executable parameter scopes preserve call targets."""

from pathlib import Path

import pytest

from trailmark.models import EdgeConfidence, EdgeKind
from trailmark.parse import parse_file


@pytest.mark.parametrize("extension", ["js", "ts"])
def test_duplicate_property_bodies_keep_free_function_calls(tmp_path: Path, extension: str) -> None:
    path = tmp_path / f"app.{extension}"
    path.write_text(
        "const add = (a) => a;\nexport function getD() {\n"
        " const internal = {sign: (m) => add(m)};\n"
        " return {sign: (m) => add(m)};\n}\n"
    )
    graph = parse_file(str(path))
    calls = [
        e
        for e in graph.edges
        if e.kind == EdgeKind.CALLS and not dict(e.attributes).get("deferred")
    ]
    assert len(calls) == 2
    assert len({e.source_id for e in calls}) == 2
    assert all(e.target_id == "app:add" and e.confidence == EdgeConfidence.CERTAIN for e in calls)


def test_duplicate_python_bodies_keep_free_function_calls(tmp_path: Path) -> None:
    path = tmp_path / "app.py"
    path.write_text(
        "def add(x): return x\ndef outer():\n"
        " def sign(m): return add(m)\n def sign(m): return add(m)\n return sign\n"
    )
    graph = parse_file(str(path))
    calls = [
        e
        for e in graph.edges
        if e.kind == EdgeKind.CALLS and not dict(e.attributes).get("deferred")
    ]
    assert len(calls) == 2 and len({e.source_id for e in calls}) == 2
    assert all(e.target_id == "app:add" and e.confidence == EdgeConfidence.CERTAIN for e in calls)


@pytest.mark.parametrize("extension", ["ts", "tsx"])
@pytest.mark.parametrize(
    "signature",
    [
        "type Printer = (format: string) => void;",
        "interface Printer {print(format: string): void;}",
        "declare function print(format: string): void;",
        "abstract class Printer {abstract print(format: string): void;}",
        "type Printer = new (format: string) => object;",
        "interface Props {onClick(format: string): void;}",
        "function print(format: string): void; function print(x: any) {}",
    ],
)
def test_typescript_signature_parameters_do_not_shadow_functions(
    tmp_path: Path, extension: str, signature: str
) -> None:
    path = tmp_path / f"app.{extension}"
    path.write_text(
        f"export function format(v: number){{return String(v);}}\n{signature}\n"
        "export function show(v: number){return format(v);}\n"
    )
    graph = parse_file(str(path))
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS and e.source_id == "app:show")
    assert call.target_id == "app:format" and call.confidence == EdgeConfidence.CERTAIN


@pytest.mark.parametrize(
    ("extension", "source"),
    [
        ("py", "def outer():\n def fmt(): pass\n def inner(fmt): return fmt()\n"),
        ("cs", "class C {void Outer(){void F(){} void Inner(System.Action F){F();}}}"),
        ("kt", "fun outer(){fun f(){}; fun inner(f: ()->Unit){f()}}"),
        ("swift", "func outer(){func f(){}; func inner(f: ()->Void){f()}}"),
    ],
)
def test_inner_parameter_shadows_outer_local_function(
    tmp_path: Path, extension: str, source: str
) -> None:
    path = tmp_path / f"app.{extension}"
    path.write_text(source)
    graph = parse_file(str(path))
    calls = [
        e
        for e in graph.edges
        if e.kind == EdgeKind.CALLS and not dict(e.attributes).get("deferred")
    ]
    assert len(calls) == 1
    assert dict(calls[0].attributes).get("binding_blocked")


@pytest.mark.parametrize("parameters", ["|**opts|", "|x, *rest, **opts, &blk|"])
@pytest.mark.parametrize("lambda_form", [False, True])
def test_ruby_double_splat_parameters_are_bindings(
    tmp_path: Path, parameters: str, lambda_form: bool
) -> None:
    path = tmp_path / "app.rb"
    body = (
        f"->({parameters.strip('|')}) {{ opts }}"
        if lambda_form
        else f"xs.each {{ {parameters} opts }}"
    )
    path.write_text(f"class C\n def opts;end\n def run(xs)\n {body}\n end\nend\n")
    graph = parse_file(str(path))
    assert not any(e.kind == EdgeKind.CALLS and e.target_id == "app:C.opts" for e in graph.edges)


@pytest.mark.parametrize("declaration", ["std::function<int(int)> cb(fnp)", "Functor cb(fnp)"])
def test_cpp_object_initialization_shadows_free_function(tmp_path: Path, declaration: str) -> None:
    path = tmp_path / "app.cpp"
    path.write_text(
        "int cb(int x){return x;} int run(int (*fnp)(int)){" + declaration + ";return cb(1);}"
    )
    graph = parse_file(str(path))
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)
    assert dict(call.attributes).get("binding_blocked")


def test_dart_local_closure_shadows_function(tmp_path: Path) -> None:
    path = tmp_path / "app.dart"
    path.write_text("int helper(int x)=>x; int run(){var helper=(int x)=>x+1;return helper(1);}")
    graph = parse_file(str(path))
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)
    assert dict(call.attributes).get("binding_blocked")
