"""Regression coverage for callable identities and lexical execution contexts."""

from pathlib import Path

import pytest

from trailmark.models import CodeEdge, CodeGraph, EdgeConfidence, EdgeKind
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


@pytest.mark.parametrize(
    "definition,name",
    [
        ("PHPAPI zend_result php_hash_environment(void)", "php_hash_environment"),
        ("static RET CCONV NAME_HANDLER(HANDLER_ARGS)", "NAME_HANDLER"),
        ("ZEND_API ZEND_COLD void ZEND_FASTCALL f(void)", "f"),
        ("static RET CCONV UNKNOWN_HANDLER(CONDITIONAL_ARGS)", "UNKNOWN_HANDLER"),
    ],
)
def test_ordinary_c_macro_specifiers(tmp_path: Path, definition: str, name: str) -> None:
    graph = _graph(
        tmp_path,
        "#define PHPAPI\n#define HANDLER_ARGS int a\ntypedef int zend_result;\n"
        "#if CONFIG\n#define CONDITIONAL_ARGS int a\n"
        "#else\n#define CONDITIONAL_ARGS int b\n#endif\n"
        f"{definition} {{ sink(); }}\nvoid run(){{ {name}(0); }}",
        "c",
    )
    unit = graph.nodes[f"app:{name}"]
    assert not dict(unit.attributes).get("macro_signature_unresolved")
    assert unit.return_type is None or unit.return_type.name != name
    assert any(e.source_id == "app:run" and e.target_id == unit.id for e in _calls(graph))
    if name == "NAME_HANDLER":
        assert [p.name for p in unit.parameters] == ["a"]


@pytest.mark.parametrize("extension", ["js", "ts", "cpp", "py", "rs"])
def test_compound_callee_does_not_bind_operand(tmp_path: Path, extension: str) -> None:
    source = {
        "js": "function a(){} function pick(a,b){ (a || b)(); }",
        "ts": "function a(){} function pick(a:any,b:any){ (true ? a : b)(); }",
        "cpp": "void a(){} bool flag(){} void pick(){ (flag() ? a : b)(); }",
        "py": "def a(): pass\ndef pick(a,b):\n    (a or b)()\n",
        "rs": "fn a(){} fn pick(c:bool){ (if c {a} else {b})(); }",
    }[extension]
    graph = _graph(tmp_path, source, extension)
    calls = _calls(graph)
    indirect = [e for e in calls if "$indirect@" in e.target_id]
    assert len(indirect) == 1
    assert indirect[0].confidence == EdgeConfidence.UNCERTAIN
    assert dict(indirect[0].attributes).get("indirect_call")
    assert all(e in indirect or e.target_id == "app:flag" for e in calls)


@pytest.mark.parametrize("extension", ["js", "ts", "py", "c", "cpp"])
def test_parenthesized_parameter_stays_shadowed(tmp_path: Path, extension: str) -> None:
    source = (
        "def a(): pass\ndef pick(a): (a)()"
        if extension == "py"
        else "void a(){} void pick(void (*a)()){ (a)(); }"
        if extension in {"c", "cpp"}
        else "function a(){} function pick(a){ (a)(); }"
    )
    graph = _graph(tmp_path, source, extension)
    assert not any(e.target_id == "app:a" for e in _calls(graph))


@pytest.mark.parametrize("extension", ["js", "ts"])
def test_named_iife_calls_expression(tmp_path: Path, extension: str) -> None:
    graph = _graph(
        tmp_path, "function setup(){} function run(){ (function setup(){})() }", extension
    )
    assert any(e.source_id == "app:run" and e.target_id == "app:run.setup" for e in _calls(graph))
    assert not any(e.target_id == "app:setup" for e in _calls(graph))


@pytest.mark.parametrize("extension", ["js", "ts"])
def test_unextracted_iife_does_not_bind_module_function(tmp_path: Path, extension: str) -> None:
    source = (
        "function setup(){} function outer(){"
        + "function nested(){" * 47
        + "(function setup(){})()"
        + "}" * 48
    )
    graph = _graph(tmp_path, source, extension)
    assert graph.parse_diagnostics
    calls = _calls(graph)
    assert len(calls) == 1
    assert calls[0].target_id != "app:setup"
    assert calls[0].confidence == EdgeConfidence.UNCERTAIN


def test_go_parenthesized_pointer_conversion_is_not_call(tmp_path: Path) -> None:
    graph = _graph(tmp_path, "package p; func run(){ (*fr.Vector)(&t); (*byte)(p) }", "go")
    assert _calls(graph) == []


def test_parameter_decorators_do_not_run_in_method(tmp_path: Path) -> None:
    graph = _graph(
        tmp_path,
        'function Inject(t:string){} class Svc { constructor(@Inject("T") x:number){} '
        'handler(@Inject("Q") q:number){} }',
        "ts",
    )
    assert not any(e.source_id.startswith("app:Svc.") for e in _calls(graph))


@pytest.mark.parametrize("extension", ["js", "ts"])
def test_named_function_expression_recurses_to_itself(tmp_path: Path, extension: str) -> None:
    (tmp_path / f"app.{extension}").write_text(
        "Foo.prototype.walk = function walk(n){return walk(n-1)}"
    )
    (tmp_path / f"other.{extension}").write_text("function walk(){}")
    graph = parse_directory(str(tmp_path), "javascript" if extension == "js" else "typescript")
    call = _calls(graph)[0]
    assert call.source_id == call.target_id == "app:Foo.prototype.walk"
    assert call.confidence == EdgeConfidence.CERTAIN


def test_do_while_receiver_assignment_is_unconditional(tmp_path: Path) -> None:
    graph = _graph(
        tmp_path, "class Bar {m(){}} function run(){let x; do{x=new Bar()}while(false);x.m()}", "ts"
    )
    assert any(e.target_id == "app:Bar.m" for e in _calls(graph))


def test_python_try_body_binding_is_not_conditional(tmp_path: Path) -> None:
    graph = _graph(
        tmp_path, "def run():\n try:\n  def k(): pass\n finally: pass\n return k()", "py"
    )
    assert any(
        e.target_id == "app:run.k" and e.confidence == EdgeConfidence.CERTAIN for e in _calls(graph)
    )


def test_namespace_does_not_rebind_module_call_as_self(tmp_path: Path) -> None:
    graph = _graph(
        tmp_path,
        "function sign(m:number){return m} export function make(){"
        "class K{sign(m:number){return sign(m)}} return new K()} namespace NS{export const v=1}",
        "ts",
    )
    assert any(
        e.source_id == "app:make.K.sign" and e.target_id == "app:sign" for e in _calls(graph)
    )


@pytest.mark.parametrize("parameter", ["unsigned", "const T", "struct S", "enum E", "union U"])
def test_cpp_typed_block_prototypes(tmp_path: Path, parameter: str) -> None:
    graph = _graph(tmp_path, f"int run(){{int other({parameter});return other(5);}}", "cpp")
    assert not any(dict(e.attributes).get("binding_blocked") for e in _calls(graph))


@pytest.mark.parametrize("extension", ["c", "cpp"])
def test_swallowed_static_prototype_keeps_same_file_definition(
    tmp_path: Path, extension: str
) -> None:
    graph = _graph(
        tmp_path,
        "int run(){ static int helper(int); return helper(1); }\n"
        "static int helper(int x){return x;}",
        extension,
    )
    assert any(e.source_id == "app:run" and e.target_id == "app:helper" for e in _calls(graph))


@pytest.mark.parametrize("extension", ["js", "ts"])
def test_local_class_initializers_do_not_inherit_outer_this(tmp_path: Path, extension: str) -> None:
    graph = _graph(
        tmp_path,
        "class C {helper(){} m(){class K {a=this.helper(); static b=this.helper();"
        "static {this.helper()}}} n(){return class {x=this.helper()}}}",
        extension,
    )
    assert not any(e.target_id == "app:C.helper" for e in _calls(graph))


def test_merged_namespace_is_not_a_function_body(tmp_path: Path) -> None:
    graph = _graph(
        tmp_path, "export function fn(){} export namespace fn {export function extra(){}}", "ts"
    )
    assert not any(e.kind == EdgeKind.CALLS and e.source_id == "app:fn" for e in graph.edges)


def test_merged_namespace_keeps_local_function_distinct(tmp_path: Path) -> None:
    graph = _graph(
        tmp_path,
        "function outer(){function fn(){} return fn()} namespace outer {export function fn(){}}",
        "ts",
    )
    edge = next(e for e in _calls(graph) if e.source_id == "app:outer")
    assert edge.target_id == "app:outer.fn" and edge.confidence == EdgeConfidence.CERTAIN


def test_ambient_module_quotes_do_not_change_ids(tmp_path: Path) -> None:
    graphs = [
        _graph(
            tmp_path,
            f"declare module {quote}legacy{quote} {{export function helper():void;}}",
            "ts",
        )
        for quote in ['"', "'"]
    ]
    assert set(graphs[0].nodes) == set(graphs[1].nodes)


@pytest.mark.parametrize(
    "extension,source",
    [
        ("py", "def fmt(): pass\ndef d(fmt):\n def inner(): return fmt()\n return inner()"),
        ("kt", "fun fmt(){}\nfun d(fmt:()->Unit){fun inner(){fmt()};inner()}"),
        ("swift", "func fmt(){}\nfunc d(fmt:()->Void){func inner(){fmt()};inner()}"),
        ("cs", "class C {void fmt(){} void d(System.Action fmt){void inner(){fmt();} inner();}}"),
    ],
)
def test_captured_parameters_shadow_named_functions(
    tmp_path: Path, extension: str, source: str
) -> None:
    graph = _graph(tmp_path, source, extension)
    calls = [e for e in _calls(graph) if graph.nodes[e.source_id].name == "inner"]
    assert calls and all(dict(e.attributes).get("binding_blocked") for e in calls)


def test_optional_ts_parameter_is_a_binding(tmp_path: Path) -> None:
    graph = _graph(
        tmp_path, "function fmt(n:number){} function d(fmt?: (n:number)=>void){fmt!(12)}", "ts"
    )
    assert all(dict(e.attributes).get("binding_blocked") for e in _calls(graph))


@pytest.mark.parametrize("extension", ["js", "ts"])
def test_object_literal_callable_has_object_owner(tmp_path: Path, extension: str) -> None:
    graph = _graph(
        tmp_path,
        "function sign(){} export const api={sign:()=>sign()};function run(){sign()}",
        extension,
    )
    calls = _calls(graph)
    assert all(e.target_id == "app:sign" and e.confidence == EdgeConfidence.CERTAIN for e in calls)
    assert "app:api.sign" in graph.nodes


@pytest.mark.parametrize(
    "extension,source",
    [
        ("cpp", "void run(int x){int f;f();} void run(float x){int f;f();}"),
        ("kt", "fun run(x:Int,f:()->Unit){f()}\nfun run(x:String,f:()->Unit){f()}"),
        ("swift", "func run(x:Int,f:()->Void){f()}\nfunc run(x:String,f:()->Void){f()}"),
        (
            "cs",
            "class C {void run(int x,System.Action f){f();}"
            "void run(string x,System.Action f){f();}}",
        ),
    ],
)
def test_overload_blocked_targets_use_final_owner(
    tmp_path: Path, extension: str, source: str
) -> None:
    graph = _graph(tmp_path, source, extension)
    calls = _calls(graph)
    assert len(calls) == 2
    assert all(e.source_id + ".$binding." in e.target_id for e in calls)
    assert len({e.target_id for e in calls}) == 2


def test_java_constructor_proxy_uses_final_owner(tmp_path: Path) -> None:
    graph = _graph(
        tmp_path, "class C {void run(){new Missing();} void run(int x){new Missing();}}", "java"
    )
    calls = _calls(graph)
    assert calls and all(e.source_id + ".$constructor" in e.target_id for e in calls)


@pytest.mark.parametrize("body", ["xs.each { it }", "-> { it }"])
def test_ruby_implicit_it_is_a_value(tmp_path: Path, body: str) -> None:
    graph = _graph(tmp_path, f"def it;end\ndef run(xs);{body};end", "rb")
    assert not any(e.target_id == "app:it" for e in _calls(graph))


@pytest.mark.parametrize(
    "body", ["it", "xs.each { |x| it }", "xs.each { it() }", "xs.each { it(1) }"]
)
def test_ruby_explicit_it_calls_stay_methods(tmp_path: Path, body: str) -> None:
    graph = _graph(tmp_path, f"def it;end\ndef run(xs);{body};end", "rb")
    assert any(e.target_id == "app:it" for e in _calls(graph))


@pytest.mark.parametrize("namespace_first", [True, False])
def test_qualified_merged_namespace_call(tmp_path: Path, namespace_first: bool) -> None:
    namespace = "namespace outer {export function member(){}}"
    function = "function outer(){function member(){} member()}"
    graph = _graph(
        tmp_path,
        (namespace + function if namespace_first else function + namespace)
        + "function run(){outer.member()}",
        "ts",
    )
    calls = {e.source_id: e for e in _calls(graph)}
    assert calls["app:outer"].target_id == "app:outer.member"
    assert calls["app:run"].target_id == "app:outer.$namespace.member"


def test_known_c_wrapper_with_type_named_argument(tmp_path: Path) -> None:
    graph = _graph(tmp_path, "#define DECL(n) void zif_ ## n(void)\nDECL(void){}", "c")
    assert "app:zif_void" in graph.nodes


@pytest.mark.parametrize("shadow", ["parameter", "local", "captured"])
def test_merged_namespace_receiver_can_be_shadowed(tmp_path: Path, shadow: str) -> None:
    caller = {
        "parameter": "function run(outer:any){outer.member()}",
        "local": "function run(){const outer={member(){}};outer.member()}",
        "captured": "function run(outer:any){return ()=>outer.member()}",
    }[shadow]
    graph = _graph(
        tmp_path, "function outer(){} namespace outer{export function member(){}}" + caller, "ts"
    )
    assert not any(e.target_id == "app:outer.$namespace.member" for e in _calls(graph))


def test_python_subscript_callee_keeps_indirect_site(tmp_path: Path) -> None:
    graph = _graph(tmp_path, "def run(callbacks):\n return callbacks[0]()", "py")
    call = _calls(graph)[0]
    assert call.target_id.endswith("$indirect@2_8_42")
    assert call.confidence == EdgeConfidence.UNCERTAIN
    assert dict(call.attributes).get("indirect_call")


def test_cpp_pointer_callee_keeps_indirect_site(tmp_path: Path) -> None:
    graph = _graph(tmp_path, "void run(){(*fp)();}", "cpp")
    call = _calls(graph)[0]
    assert "$indirect@" in call.target_id
    assert call.confidence == EdgeConfidence.UNCERTAIN
    assert dict(call.attributes).get("indirect_call")


def test_compound_callee_retains_operand_and_argument_calls(tmp_path: Path) -> None:
    graph = _graph(tmp_path, "function run(){(flag() ? fast : slow)(arg())}", "js")
    targets = [e.target_id for e in _calls(graph)]
    assert len(targets) == 3
    assert sum("$indirect@" in target for target in targets) == 1
    assert any(target.endswith(":flag") for target in targets)
    assert any(target.endswith(":arg") for target in targets)


def test_merged_namespace_shadow_in_dotted_filename(tmp_path: Path) -> None:
    path = tmp_path / "app.test.ts"
    path.write_text(
        "function outer(){} namespace outer{export function member(){}}"
        "function run(outer:any){outer.member()}"
    )
    graph = parse_file(str(path))
    assert not any(e.target_id == "app.test:outer.$namespace.member" for e in _calls(graph))
