"""Behavioral regressions from the v0.7 pre-release audit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trailmark.analysis.augment import augment_from_sarif
from trailmark.models import (
    CodeEdge,
    CodeGraph,
    CodeUnit,
    EdgeConfidence,
    EdgeKind,
    NodeKind,
    Parameter,
    SourceLocation,
    TypeRef,
)
from trailmark.parse import parse_directory, parse_file
from trailmark.parsers.rust._scopes import _Resolution, _resolve_edge
from trailmark.parsers.rust.parser import RustParser
from trailmark.query.api import QueryEngine
from trailmark.storage.graph_store import GraphStore

FIXTURES = Path(__file__).parent / "fixtures" / "audit"


def test_proto_nested_fields_and_public_import() -> None:
    graph = parse_file(str(FIXTURES / "proto/cases/schema.proto"), "proto")
    for owner in ("Request", "Response"):
        assert f"audit.api:{owner}.Item" in graph.nodes
        assert any(
            e.source_id == f"audit.api:{owner}.item"
            and e.target_id == f"audit.api:{owner}.Item"
            and e.kind == EdgeKind.TYPE_USES
            for e in graph.edges
        )
    assert "audit.api:Request.Kind" in graph.nodes
    for field in ("password", "proof", "extras"):
        assert f"audit.api:Request.{field}" in graph.nodes
    assert dict(graph.nodes["audit.api:Request.password"].attributes)["oneof"]
    extras_type = graph.nodes["audit.api:Request.extras"].return_type
    assert extras_type is not None and extras_type.generic_args
    assert graph.dependencies == ["common.proto"]
    assert not any(e.kind == EdgeKind.CALLS for e in graph.edges)


def test_graphql_effective_roots() -> None:
    engine = QueryEngine.from_directory(
        str(FIXTURES / "graphql/explicit_roots"), language="graphql"
    )
    assert {row["node_id"] for row in engine.attack_surface()} == {
        "schema:RootQuery.user",
        "schema:RootQuery.legacy",
        "schema:RootMutation.deleteUser",
        "schema:RootSubscription.userChanged",
    }


def test_graphql_wrapped_type_dependencies() -> None:
    graph = parse_file(str(FIXTURES / "graphql/signatures/schema.graphql"), "graphql")
    wrapped = [
        unit
        for unit in graph.nodes.values()
        if unit.return_type and "User" in unit.return_type.name
    ]
    assert len(wrapped) > 2
    for unit in wrapped:
        assert any(
            edge.source_id == unit.id
            and edge.target_id == "schema:User"
            and edge.kind == EdgeKind.TYPE_USES
            for edge in graph.edges
        )


@pytest.mark.parametrize("mode", ["single", "split"])
def test_graphql_extensions(mode: str) -> None:
    graph = parse_directory(str(FIXTURES / "graphql/extensions" / mode), "graphql")
    for name in ("deleteUser", "name", "role", "alias"):
        assert any(unit.name == name for unit in graph.nodes.values())
    assert not any(edge.kind == EdgeKind.CALLS for edge in graph.edges)


@pytest.mark.parametrize("artifact", [{"index": 0}, {"uri": "app.py", "uriBaseId": "SRC"}])
def test_sarif_run_locations_bind_correct_source(tmp_path: Path, artifact: dict) -> None:
    graph = CodeGraph(root_path=str(tmp_path))
    for name, path in (("root", "app.py"), ("lib", "lib/app.py")):
        graph.nodes[name] = CodeUnit(
            id=name,
            name=name,
            kind=NodeKind.FUNCTION,
            location=SourceLocation(file_path=str(tmp_path / path), start_line=1, end_line=3),
        )
    report = {
        "runs": [
            {
                "tool": {"driver": {"name": "audit"}},
                "artifacts": [{"location": {"uri": "lib/app.py"}}],
                "originalUriBaseIds": {"SRC": {"uri": (tmp_path / "lib").as_uri() + "/"}},
                "results": [
                    {
                        "message": {"text": "finding"},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": artifact,
                                    "region": {"startLine": 2},
                                }
                            }
                        ],
                    }
                ],
            }
        ]
    }
    path = tmp_path / "results.sarif"
    path.write_text(json.dumps(report))
    assert augment_from_sarif(GraphStore(graph), str(path))["matched_findings"] == 1
    assert set(graph.annotations) == {"lib"}


def test_thrift_file_identity_and_signature_types() -> None:
    graph = parse_directory(str(FIXTURES / "thrift/identity"), "thrift")
    requests = [unit for unit in graph.nodes.values() if unit.name == "Request"]
    assert len(requests) == 2
    assert len({unit.location.file_path for unit in requests}) == 2
    for unit in requests:
        fields = [
            edge.target_id
            for edge in graph.edges
            if edge.kind == EdgeKind.CONTAINS and edge.source_id == unit.id
        ]
        assert len(fields) == 1
    graph = parse_directory(str(FIXTURES / "thrift/signatures"), "thrift")
    login = next(unit for unit in graph.nodes.values() if unit.name == "login")
    assert [ref.name for ref in login.exception_types] == ["Failure"]
    batch = next(unit for unit in graph.nodes.values() if unit.name == "batch")
    assert any(
        edge.source_id == batch.id
        and graph.nodes[edge.target_id].name == "Request"
        and edge.kind == EdgeKind.TYPE_USES
        for edge in graph.edges
    )
    graph = parse_directory(str(FIXTURES / "thrift/includes"), "thrift")
    login = next(unit for unit in graph.nodes.values() if unit.name == "login")
    assert any(
        edge.source_id == login.id
        and graph.nodes[edge.target_id].name == "Request"
        and edge.kind == EdgeKind.TYPE_USES
        for edge in graph.edges
    )


@pytest.mark.parametrize("case", ["contract", "contract_inline", "impl_self"])
def test_sway_implemented_abi_entrypoints(case: str) -> None:
    engine = QueryEngine.from_directory(str(FIXTURES / "sway" / case), language="sway")
    assert engine.attack_surface()
    assert engine.entrypoint_paths_to("sink")


def test_sway_generic_impl_owners() -> None:
    graph = parse_directory(str(FIXTURES / "sway/generic"), "sway")
    assert "main:get" not in graph.nodes
    for owner, sink in (("Wrapper", "first_sink"), ("Other", "second_sink")):
        method = f"main:{owner}.get"
        assert method in graph.nodes
        assert {
            edge.target_id
            for edge in graph.edges
            if edge.source_id == method and edge.kind == EdgeKind.CALLS
        } == {f"main:{sink}"}


def test_func_method_variants_and_lexical_callbacks() -> None:
    graph = parse_file(str(FIXTURES / "func/methods.fc"), "func")

    def callees(name: str) -> set[str]:
        return {
            edge.target_id
            for edge in graph.edges
            if edge.source_id == f"methods:{name}" and edge.kind == EdgeKind.CALLS
        }

    assert callees("bare") == {"methods:helper"}
    assert callees("dotted") == {"methods:.helper"}
    assert callees("modifying") == {"methods:~helper"}
    assert callees("dot_fallback") == {"methods:simple"}
    for caller in ("apply", "callback_local"):
        assert "methods:helper" not in callees(caller)
    scoped_edges = [
        edge
        for edge in graph.edges
        if edge.source_id == "methods:scoped" and edge.kind == EdgeKind.CALLS
    ]
    assert len(scoped_edges) == 2
    assert sum(edge.target_id == "methods:helper" for edge in scoped_edges) == 1


def test_func_semantic_getters_and_reserved_handlers() -> None:
    engine = QueryEngine.from_directory(str(FIXTURES / "func"), language="func")
    ids = {row["node_id"] for row in engine.attack_surface()}
    assert {"constructs:balance", "constructs:explicit_balance", "constructs:run_ticktock"} <= ids
    assert "constructs:get_private" not in ids


@pytest.mark.parametrize(
    "language,name,source",
    [("python", "bad.py", "def broken(:\n    pass\n"), ("c", "bad.c", "int f() { return 1 }\n")],
)
def test_structured_diagnostics_and_strict_mode(
    tmp_path: Path, language: str, name: str, source: str
) -> None:
    path = tmp_path / name
    path.write_text(source)
    graph = parse_file(str(path), language)
    assert graph.source_file_counts == {language: 1}
    assert graph.parse_diagnostics
    assert all(item.location.file_path == str(path) for item in graph.parse_diagnostics)
    diagnostics = GraphStore(graph).parse_diagnostics()
    diagnostics.clear()
    assert graph.parse_diagnostics
    engine = QueryEngine.from_directory(str(tmp_path), language=language)
    assert engine.summary()["parse_diagnostics"] == engine.parse_diagnostics()
    assert json.loads(engine.to_json())["summary"]["parse_diagnostics"]
    with pytest.raises(ValueError, match="Incomplete source analysis"):
        parse_file(str(path), language, strict=True)
    with pytest.raises(ValueError, match="Incomplete source analysis"):
        parse_directory(str(tmp_path), language, strict=True)
    with pytest.raises(ValueError, match="Incomplete source analysis"):
        QueryEngine.from_directory(str(tmp_path), language=language, strict=True)


def test_empty_valid_source_has_no_parse_diagnostics(tmp_path: Path) -> None:
    source = tmp_path / "empty.py"
    source.write_text("")
    assert parse_file(str(source), strict=True).parse_diagnostics == []


def test_cli_strict_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trailmark.cli import main

    (tmp_path / "bad.py").write_text("def broken(: pass\n")
    monkeypatch.setattr("sys.argv", ["trailmark", "analyze", str(tmp_path), "--strict", "--json"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "Incomplete source analysis" in output.err


@pytest.mark.parametrize("directory", [False, True])
def test_python_callable_ownership_and_shadowing(tmp_path: Path, directory: bool) -> None:
    path = tmp_path / "owned.py"
    path.write_text("""def sink(): pass
def factory():
    def hidden():
        if True: sink()
        raise RuntimeError()
    return hidden
def invoke():
    def hidden(): sink()
    hidden()
def parameter(sink): sink()
def later():
    sink()
    sink = None
def explicit_global():
    global sink
    sink()
def normal(): sink()
def variadic(self, cls, *args: int, **kwargs: str): pass
class C:
    def method(self, x): pass
    @staticmethod
    def static(self, cls): pass
""")
    graph = (
        parse_directory(str(tmp_path), "python") if directory else parse_file(str(path), "python")
    )
    calls = {
        (edge.source_id, edge.target_id) for edge in graph.edges if edge.kind == EdgeKind.CALLS
    }
    assert ("owned:factory", "owned:sink") not in calls
    assert ("owned:factory.hidden", "owned:sink") in calls
    assert ("owned:invoke", "owned:invoke.hidden") in calls
    assert ("owned:parameter", "owned:sink") not in calls
    assert ("owned:later", "owned:sink") not in calls
    assert ("owned:explicit_global", "owned:sink") in calls
    assert ("owned:normal", "owned:sink") in calls
    assert graph.nodes["owned:factory"].cyclomatic_complexity == 1
    assert not graph.nodes["owned:factory"].exception_types
    assert graph.nodes["owned:factory.hidden"].exception_types[0].name == "RuntimeError"
    assert [p.name for p in graph.nodes["owned:variadic"].parameters] == [
        "self",
        "cls",
        "args",
        "kwargs",
    ]
    assert [p.name for p in graph.nodes["owned:C.method"].parameters] == ["x"]
    assert [p.name for p in graph.nodes["owned:C.static"].parameters] == ["self", "cls"]


@pytest.mark.parametrize("language,extension", [("javascript", ".js"), ("typescript", ".ts")])
def test_ecmascript_nested_callable_ownership(
    tmp_path: Path, language: str, extension: str
) -> None:
    source = tmp_path / ("owned" + extension)
    source.write_text("""function sink() {}
function dormant() { function hidden() { if (true) sink(); } return 0; }
function invoke() { function hidden() { sink(); } hidden(); }
function parameter(sink) { sink(); }
const concise = () => sink();
const choose = (flag) => flag ? sink() : sink();
exports.handler = (event) => sink();
""")
    graph = parse_directory(str(tmp_path), language)
    calls = {
        (edge.source_id, edge.target_id) for edge in graph.edges if edge.kind == EdgeKind.CALLS
    }
    assert ("owned:dormant", "owned:sink") not in calls
    assert ("owned:dormant.hidden", "owned:sink") in calls
    assert ("owned:invoke", "owned:invoke.hidden") in calls
    assert ("owned:parameter", "owned:sink") not in calls
    assert ("owned:concise", "owned:sink") in calls
    assert graph.nodes["owned:choose"].cyclomatic_complexity == 2
    assert [p.name for p in graph.nodes["owned:handler"].parameters] == ["event"]
    assert not graph.parse_diagnostics


def test_typescript_tsx_and_interface_signatures() -> None:
    graph = parse_file(str(FIXTURES / "typescript/tsx/case.tsx"), "typescript", strict=True)
    calls = {(e.source_id, e.target_id) for e in graph.edges if e.kind == EdgeKind.CALLS}
    assert {("case:View", "case:sink"), ("case:Inline", "case:sink")} <= calls
    graph = parse_file(str(FIXTURES / "typescript/declarations/case.ts"), "typescript")
    member = graph.nodes["case:Repository.load"]
    assert [p.name for p in member.parameters] == ["key", "fallback"]
    assert member.return_type is not None and member.return_type.name == "Promise"
    assert member.type_parameters[0].name == "U"


def test_typescript_receiver_invalidations() -> None:
    graph = parse_directory(str(FIXTURES / "typescript/receivers"), "typescript")
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert not any(
        e.source_id in {"case:reassigned", "case:branch"}
        and e.target_id in {"case:Safe.run", "case:Risky.run"}
        for e in calls
    )
    block = [e.target_id for e in calls if e.source_id == "case:blockShadow"]
    assert block == ["case:Risky.run", "case:Safe.run"]


def _calls(graph: CodeGraph, source: str) -> list[str]:
    return [e.target_id for e in graph.edges if e.source_id == source and e.kind == EdgeKind.CALLS]


def test_sql_identity_body_ownership_and_identifier_matching() -> None:
    graph = parse_directory(str(FIXTURES / "sql/identities"), "sql")
    overloads = [n for n in graph.nodes.values() if n.name == "lookup"]
    assert len(overloads) == 2
    dependencies = {
        (e.source_id, e.target_id) for e in graph.edges if e.kind == EdgeKind.CORRESPONDS_TO
    }
    for unit in overloads:
        expected = (
            "integers"
            if unit.parameters[0].type_ref and unit.parameters[0].type_ref.name == "integer"
            else "strings"
        )
        assert {target for source, target in dependencies if source == unit.id} == {
            f"objects:app.{expected}"
        }
    assert graph.nodes["objects:app.shared"].kind == NodeKind.TABLE
    assert ("objects:app.shared_view", "objects:app.shared") in dependencies
    graph = parse_file(str(FIXTURES / "sql/ownership/procedures.sql"), "sql")
    assert not any(n.name == "ghost" for n in graph.nodes.values())
    assert {
        e.target_id
        for e in graph.edges
        if e.source_id == "procedures:app.refresh" and e.kind == EdgeKind.CORRESPONDS_TO
    } == {"procedures:app.actual"}
    assert (
        graph.nodes["procedures:app.refresh"].location.end_line
        > graph.nodes["procedures:app.refresh"].location.start_line
    )
    graph = parse_directory(str(FIXTURES / "sql/references"), "sql")
    dependencies = {
        (e.source_id, e.target_id) for e in graph.edges if e.kind == EdgeKind.CORRESPONDS_TO
    }
    assert ("z_views:app.case_variant", "a_tables:app.events") in dependencies
    assert ("z_views:app.unquoted_equivalent", 'a_tables:app."quoted"') in dependencies
    assert ("z_views:app.mixed_distinct", 'a_tables:app."MiXeD"') not in dependencies


def test_r_binding_order_mutation_and_quoted_syntax() -> None:
    graph = parse_file(str(FIXTURES / "r/scope/main.R"), "r")
    calls = [e for e in graph.edges if e.source_id == "main:main" and e.kind == EdgeKind.CALLS]
    assert len(calls) == 2
    assert graph.nodes[calls[0].target_id].kind == NodeKind.PROXY
    assert calls[1].target_id == "main:main.helper"
    graph = parse_directory(str(FIXTURES / "r/namespace"), "r")
    assert "main:helper" not in _calls(graph, "main:main")
    engine = QueryEngine.from_directory(str(FIXTURES / "r/quoted"), language="r")
    assert engine.paths_between("main:main", "main:helper") == []


def test_miden_invocation_kinds_qualified_names_and_entrypoints() -> None:
    graph = parse_directory(str(FIXTURES / "masm/qualified"), "masm")
    assert "client:check" not in _calls(graph, "client:caller")
    assert all(
        graph.nodes[target].kind == NodeKind.PROXY for target in _calls(graph, "client:caller")
    )
    graph = parse_file(str(FIXTURES / "masm/invocations/program.masm"), "masm")
    assert not _calls(graph, "program:referenced_only")
    executed = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert executed and all(
        dict(e.attributes)["opcode"] in {"exec", "call", "syscall"} for e in executed
    )
    engine = QueryEngine.from_directory(str(FIXTURES / "masm/entrypoints"), language="masm")
    assert {row["node_id"] for row in engine.attack_surface()} == {"library:api", "program:begin"}


def test_zig_identifiers_signatures_and_recovery_guard() -> None:
    graph = parse_file(str(FIXTURES / "zig/quoted.zig"), "zig")
    assert _calls(graph, "quoted:main") == [
        "quoted:helper",
        "quoted:Server.run",
        "quoted:helper",
        "quoted:Server.run",
    ]
    graph = parse_file(str(FIXTURES / "zig/ordinary.zig"), "zig")
    assert len(graph.nodes["ordinary:unnamed"].parameters) == 3
    assert [p.name for p in graph.nodes["ordinary:variadic"].parameters][-1] == "..."
    assert dict(graph.nodes["ordinary:variadic"].attributes)["variadic"] is True
    graph = parse_directory(str(FIXTURES / "zig/recovery"), "zig")
    assert graph.parse_diagnostics
    assert all(
        graph.nodes[target].kind == NodeKind.PROXY
        for target in _calls(graph, "destructure:destructure")
    )


def test_aiken_labels_and_deferred_captures() -> None:
    graph = parse_directory(str(FIXTURES / "aiken/labels"), "aiken")
    assert [p.name for p in graph.nodes["sample:another"].parameters] == ["helper"]
    assert "sample:helper" not in _calls(graph, "sample:another")
    graph = parse_file(str(FIXTURES / "aiken/captures/sample.ak"), "aiken")
    assert not _calls(graph, "sample:dormant")
    assert _calls(graph, "sample:piped") == ["sample:helper"]
    assert _calls(graph, "sample:passed") == ["sample:receives"]


def test_lua_local_assignments_and_environment() -> None:
    graph = parse_directory(str(FIXTURES / "lua/lexical"), "lua")
    assert all(
        graph.nodes[target].kind == NodeKind.PROXY
        for target in _calls(graph, "local_assignment:main")
    )
    assert "sandbox_only" not in graph.dependencies
    assert _calls(graph, "environment:main")[0] == "environment:stable"
    assert "environment:helper" not in _calls(graph, "environment:main")


@pytest.mark.parametrize("case", ["quoted_eval", "escaped_eval", "quoted_source", "quoted_unset"])
def test_bash_static_command_words_invalidate_bindings(case: str) -> None:
    graph = parse_directory(str(FIXTURES / "bash/cases" / case), "bash")
    assert "sample:helper" not in _calls(graph, "sample:main")
    if case == "quoted_source":
        assert "./library.bash" in graph.dependencies


def test_bash_coproc_and_time_ownership() -> None:
    graph = parse_file(str(FIXTURES / "bash/cases/coproc/sample.sh"), "bash", strict=True)
    assert graph.nodes["sample:main"].location.end_line == 8
    assert {"sample:worker", "sample:after"} <= set(_calls(graph, "sample:main"))
    graph = parse_file(str(FIXTURES / "bash/cases/time/sample.sh"), "bash", strict=True)
    assert _calls(graph, "sample:main").count("sample:helper") == 2


def test_go_generics_signatures_and_package_boundaries() -> None:
    graph = parse_directory(str(FIXTURES / "go"), "go")
    assert _calls(graph, "features:genericCaller") == ["features:identity"]
    assert not _calls(graph, "features:conversionCaller")
    assert [p.name for p in graph.nodes["features:grouped"].parameters] == [
        "a",
        "b",
        "c",
        "d",
        "cb",
        "ch",
        "rest",
    ]
    assert len(graph.nodes["features:unnamed"].parameters) == 2
    assert "other:localOnly" not in _calls(graph, "features:invokeOther")


@pytest.mark.parametrize("case", ["impl_collisions", "verus_impl_collisions"])
def test_rust_impl_bodies_remain_distinct(case: str) -> None:
    path = FIXTURES / "rust" / case
    graph = parse_directory(str(path), "rust")
    methods = [
        n for n in graph.nodes.values() if n.name in {"act", "run"} and n.kind == NodeKind.METHOD
    ]
    assert len(methods) == 4
    for method in methods:
        assert len(_calls(graph, method.id)) == 1
        assert graph.nodes[_calls(graph, method.id)[0]].name in {"first_sink", "second_sink"}


def test_rust_2024_exports() -> None:
    engine = QueryEngine.from_directory(str(FIXTURES / "rust/ffi2024"), language="rust")
    names = {row["node_id"].split(":")[-1] for row in engine.attack_surface()}
    assert {"hidden_export", "rust_abi_export", "signature_control"} <= names


def test_cairo_module_ownership_and_embedded_abi() -> None:
    graph = parse_directory(str(FIXTURES / "cairo/core/src"), "cairo")
    assert _calls(graph, "lib:local.caller") == ["lib:local.helper"]
    for owner in ("first", "second"):
        assert f"lib:{owner}.SameImpl.value" in graph.nodes
        assert f"lib:{owner}.Marker" in graph.nodes
        assert f"lib:{owner}.Calc" in graph.nodes
    assert all(
        e.source_id in graph.nodes and e.target_id in graph.nodes
        for e in graph.edges
        if e.kind == EdgeKind.CONTAINS
    )
    engine = QueryEngine.from_directory(str(FIXTURES / "cairo/contract/src"), language="cairo")
    assert {entry["node_id"] for entry in engine.attack_surface()} == {
        "lib:Counter.constructor",
        "lib:Counter.CounterImpl.read",
        "lib:Counter.CounterImpl.write",
        "lib:Counter.legacy_control",
        "lib:Counter.from_l1",
        "lib:Counter.PerItem.per_item_control",
    }


def test_php_namespace_and_callable_ownership() -> None:
    graph = parse_file(str(FIXTURES / "php/namespaces.php"), "php", strict=True)
    for namespace in ("Alpha", "Beta"):
        assert f"namespaces:{namespace}.Service.run" in graph.nodes
        entry = "alphaEntry" if namespace == "Alpha" else "betaEntry"
        assert _calls(graph, f"namespaces:{namespace}.{entry}") == [f"namespaces:{namespace}.sink"]
    graph = parse_file(str(FIXTURES / "php/semicolon_namespace.php"), "php")
    assert _calls(graph, "semicolon_namespace:Gamma.gammaEntry") == [
        "semicolon_namespace:Gamma.gammaTarget"
    ]
    graph = parse_file(str(FIXTURES / "php/callables.php"), "php")
    _assert_deferred_reachable(graph, "callables:buildCallbacks")
    assert graph.nodes["callables:buildCallbacks"].cyclomatic_complexity == 1
    assert not graph.nodes["callables:buildCallbacks"].exception_types
    assert len([n for n in graph.nodes.values() if "$lambda" in n.id]) == 2
    graph = parse_file(str(FIXTURES / "php/call_forms.php"), "php")
    calls = _calls(graph, "call_forms:Actions.invokeForms")
    assert len(calls) == 7
    assert calls.count("call_forms:Actions.instanceAction") == 2
    assert calls.count("call_forms:Actions.staticAction") == 2
    assert "call_forms:Base.parentAction" in calls


def test_perl_package_context_defaults_and_bare_calls() -> None:
    graph = parse_directory(str(FIXTURES / "perl/fixtures/qualified_body"), "perl")
    assert _calls(graph, "qualified_body:Beta.main") == ["qualified_body:Alpha.helper"]
    graph = parse_directory(str(FIXTURES / "perl/fixtures/signature_defaults"), "perl")
    assert "signature_defaults:side_effect" in _calls(graph, "signature_defaults:handler")
    lambdas = [n for n in graph.nodes.values() if "<lambda>" in n.id]
    assert len(lambdas) == 1
    assert _calls(graph, lambdas[0].id) == ["signature_defaults:side_effect"]
    engine = QueryEngine.from_directory(str(FIXTURES / "perl/fixtures/bare_call"), language="perl")
    assert engine.paths_between("bare_call:main", "bare_call:helper")


@pytest.mark.parametrize("case", ["mutation_alias", "mutation_qualified", "mutation_long"])
def test_powershell_mutators_keep_stale_functions_unresolved(case: str) -> None:
    graph = parse_directory(str(FIXTURES / "powershell" / case), "powershell")
    assert _calls(graph, "sample:main")
    assert "sample:helper" not in _calls(graph, "sample:main")
    assert all(
        graph.nodes[target].kind == NodeKind.PROXY for target in _calls(graph, "sample:main")
    )


def test_powershell_callback_signatures_and_header_defaults() -> None:
    graph = parse_directory(str(FIXTURES / "powershell/parameters"), "powershell")
    for name, annotation in (("Invoke-Work", "string"), ("Invoke-Header", "int")):
        parent = graph.nodes[f"sample:{name}"]
        assert len(parent.parameters) == 1
        assert parent.parameters[0].name == "$Callback"
        assert parent.parameters[0].type_ref is None
        assert parent.parameters[0].default
        callback = next(n for n in graph.nodes.values() if n.id.startswith(parent.id + ".<lambda>"))
        assert [p.name for p in callback.parameters] == ["$Inner"]
        assert callback.parameters[0].type_ref == TypeRef(annotation)
    graph = parse_directory(str(FIXTURES / "powershell/header_defaults"), "powershell")
    for name in ("Invoke-Header", "Invoke-Block"):
        assert _calls(graph, f"sample:{name}").count("sample:Get-Default") == 1
    parent = graph.nodes["sample:Invoke-HeaderCallback"]
    assert parent.cyclomatic_complexity == 1
    assert "sample:Get-Default" not in _calls(graph, parent.id)
    callback = next(n for n in graph.nodes.values() if n.id.startswith(parent.id + ".<lambda>"))
    assert callback.cyclomatic_complexity == 2
    assert _calls(graph, callback.id) == ["sample:Get-Default"]


def test_vb_field_and_property_shadows() -> None:
    graph = parse_directory(str(FIXTURES / "vb/field_shadow"), "vb")
    assert "sample:Sink.Execute" not in _calls(graph, "sample:App.Run")
    graph = parse_directory(str(FIXTURES / "vb/property_shadow"), "vb")
    getter = graph.nodes["sample:App.Item.get"]
    assert getter.parameters == (Parameter("HELPER", TypeRef("Converter")),)
    assert getter.return_type == TypeRef("String")
    assert "sample:App.Helper" not in _calls(graph, getter.id)
    graph = parse_directory(str(FIXTURES / "vb/default_property"), "vb")
    assert [p.name for p in graph.nodes["sample:App.Item.set"].parameters] == ["index", "value"]
    for accessor in ("get", "set"):
        assert dict(graph.nodes[f"sample:App.Value.{accessor}"].attributes)["vb_shared"] is True


def test_vb_array_declarators_retain_main_entrypoint() -> None:
    engine = QueryEngine.from_directory(str(FIXTURES / "vb/array_main"), language="vb")
    assert engine.entrypoint_paths_to("sample:App.Helper") == [
        ["sample:App.Main", "sample:App.Helper"]
    ]
    graph = parse_directory(str(FIXTURES / "vb/array_main"), "vb")
    assert graph.nodes["sample:App.Main"].parameters[0].type_ref == TypeRef("String()")


def test_julia_destructuring_quotes_and_callable_methods(tmp_path: Path) -> None:
    engine = QueryEngine.from_directory(
        str(FIXTURES / "julia/cases/destructuring"), language="julia"
    )
    assert engine.paths_between("case:main", "case:helper") == []
    graph = parse_directory(str(FIXTURES / "julia/cases/destructuring"), "julia")
    for name in ("main", "properties", "closure"):
        assert "case:helper" not in _calls(graph, f"case:{name}")
        assert len(graph.nodes[f"case:{name}"].parameters) == 1
    graph = parse_directory(str(FIXTURES / "julia/cases/quotation"), "julia")
    for name in ("main", "quoted_block"):
        assert not _calls(graph, f"case:{name}")
        assert graph.nodes[f"case:{name}"].cyclomatic_complexity == 1
    source = tmp_path / "quotes.jl"
    source.write_text(
        "leaf() = 1\nf() = :(leaf($(leaf())))\ng() = quote; quote; $(leaf()); end; end\n"
    )
    graph = parse_file(str(source), "julia", strict=True)
    assert _calls(graph, "quotes:f") == ["quotes:leaf"]
    assert not _calls(graph, "quotes:g")
    graph = parse_directory(str(FIXTURES / "julia/cases/callable_structs"), "julia")
    assert graph.nodes["case:<script>"].cyclomatic_complexity == 1
    assert not _calls(graph, "case:<script>")
    assert len(_calls(graph, "case:Handler.__call__")) == 2
    assert graph.nodes["case:Handler.__call__"].cyclomatic_complexity == 2
    assert _calls(graph, "case:ShortHandler.__call__") == ["case:leaf"]
    assert graph.nodes["case:Handler.__call__"].parameters[0] == Parameter("h", TypeRef("Handler"))


def test_c_indirect_calls_and_tag_namespaces() -> None:
    graph = parse_directory(str(FIXTURES / "c/shadowing"), "c")
    for name in ("dispatch", "explicit_indirect", "local_shadow"):
        assert len(_calls(graph, f"shadow:{name}")) == 1
        assert all(graph.nodes[t].kind == NodeKind.PROXY for t in _calls(graph, f"shadow:{name}"))
    graph = parse_directory(str(FIXTURES / "c/namespaces"), "c")
    for module, name, caller in (
        ("function_first", "object", "call_object"),
        ("tag_first", "item", "use"),
    ):
        target = _calls(graph, f"{module}:{caller}")[0]
        assert graph.nodes[target].kind == NodeKind.FUNCTION
        assert graph.nodes[f"{module}:{name}"].kind == NodeKind.STRUCT
        assert target != f"{module}:{name}"


def test_solidity_call_options_and_declaration_arguments() -> None:
    graph = parse_directory(str(FIXTURES / "solidity/call_options"), "solidity")
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 4
    assert [e.location.start_line for e in calls if e.location] == [4, 5, 6, 7]
    graph = parse_directory(str(FIXTURES / "solidity/function_pointer"), "solidity")
    assert "function_pointer:C.work" not in _calls(graph, "function_pointer:C.dispatch")
    graph = parse_directory(str(FIXTURES / "solidity/modifier_arguments"), "solidity")
    assert _calls(graph, "modifier_arguments:C.f") == ["modifier_arguments:C.authorize"]
    graph = parse_directory(str(FIXTURES / "solidity/constructor_arguments"), "solidity")
    assert _calls(graph, "constructor_arguments:Child.constructor") == [
        "constructor_arguments:compute"
    ]


def test_cpp_namespace_identity_and_deferred_ownership(tmp_path: Path) -> None:
    graph = parse_directory(str(FIXTURES / "cpp/namespace_scope"), "cpp")
    for namespace in ("alpha", "beta"):
        assert graph.nodes[f"case:{namespace}.Worker"].kind == NodeKind.CLASS
        assert _calls(graph, f"case:{namespace}.entry") == [f"case:{namespace}.sink"]
    assert _calls(graph, "case:$anonymous.private_entry") == ["case:$anonymous.private_sink"]
    graph = parse_directory(str(FIXTURES / "cpp/body_ownership"), "cpp")
    _assert_deferred_reachable(graph, "case:outer")
    assert graph.nodes["case:outer"].cyclomatic_complexity == 1
    assert len([n for n in graph.nodes.values() if _calls(graph, n.id) == ["case:sink"]]) == 3
    source = tmp_path / "captures.cpp"
    source.write_text(
        "void sink() {}\nint eager() { return 1; }\nvoid f() { auto cb=[x=eager()] { sink(); }; }\n"
    )
    graph = parse_file(str(source), "cpp", strict=True)
    edges = [e for e in graph.edges if e.kind == EdgeKind.CALLS and e.source_id == "captures:f"]
    assert [e.target_id for e in edges if not dict(e.attributes).get("deferred")] == [
        "captures:eager"
    ]
    assert any(
        e.target_id == "captures:f.$lambda@3_19" and e.confidence == EdgeConfidence.UNCERTAIN
        for e in edges
    )
    graph = parse_directory(str(FIXTURES / "cpp/callable_shadow"), "cpp")
    for name in ("parameter", "pointer", "closure", "object", "Holder.method"):
        assert "case:callback" not in _calls(graph, f"case:{name}")
        assert "case:Holder.callback" not in _calls(graph, f"case:{name}")


def test_csharp_namespaces_shadowing_and_call_forms() -> None:
    graph = parse_directory(str(FIXTURES / "c_sharp/fixtures"), "c_sharp")
    for namespace, method in (("First", "FirstOnly"), ("Second", "SecondOnly")):
        assert _calls(graph, f"Namespaces:{namespace}.Same.Run") == [
            f"Namespaces:{namespace}.Same.{method}"
        ]
    prefix = "Scopes:Audit.Scopes.Dispatch"
    for name in ("ByParameter", "ByLocal", "LocalShadow"):
        assert f"{prefix}.Send" not in _calls(graph, f"{prefix}.{name}")
    _assert_deferred_reachable(graph, f"{prefix}.Deferred")
    assert graph.nodes[f"{prefix}.Deferred"].cyclomatic_complexity == 1
    prefix = "Signatures:Audit.Signatures.Syntax"
    assert _calls(graph, f"{prefix}.Generic") == [f"{prefix}.Identity", f"{prefix}.Sink"]
    assert len(_calls(graph, f"{prefix}.Nullable")) == 1
    assert graph.nodes[_calls(graph, f"{prefix}.Nullable")[0]].kind == NodeKind.PROXY


def test_java_deferred_owners_explicit_construction_and_servlets(tmp_path: Path) -> None:
    graph = parse_directory(str(FIXTURES / "java/ownership"), "java")
    for name in ("deferred", "local", "anonymous"):
        assert "Ownership:Ownership.sink" not in _calls(graph, f"Ownership:Ownership.{name}")
        assert graph.nodes[f"Ownership:Ownership.{name}"].cyclomatic_complexity == 1
    graph = parse_directory(str(FIXTURES / "java/construction"), "java")
    constructions = [e for e in graph.edges if dict(e.attributes).get("constructor_type")]
    assert len(constructions) == 3
    assert any(e.target_id == "Construction:Base.Base" for e in constructions)
    assert len({e.source_id for e in constructions}) == 3
    engine = QueryEngine.from_directory(str(FIXTURES / "java/entrypoints"), language="java")
    assert {e["node_id"] for e in engine.attack_surface()} == {"Entrypoints:Routes.route"}
    source = tmp_path / "Servlets.java"
    source.write_text(
        "import jakarta.servlet.http.HttpServlet;\n"
        "class Base extends HttpServlet {}\n"
        "class Web extends Base {\n"
        "protected void doGet(HttpServletRequest req, HttpServletResponse res) {}\n"
        "static void doPost(HttpServletRequest req, HttpServletResponse res) {}\n"
        "void doDelete(String key) {}\n}\n"
    )
    engine = QueryEngine.from_directory(str(tmp_path), language="java")
    assert {e["node_id"] for e in engine.attack_surface()} == {"Servlets:Web.doGet"}


def test_swift_extensions_scopes_and_deferred_bodies(tmp_path: Path) -> None:
    graph = parse_directory(str(FIXTURES / "swift/extensions"), "swift")
    assert graph.nodes["probe:Service"].kind == NodeKind.STRUCT
    assert _calls(graph, "probe:Service.checked") == ["probe:sanitize"]
    engine = QueryEngine.from_directory(str(FIXTURES / "swift/binding"), language="swift")
    assert engine.paths_between("probe:Service.audit", "probe:secretSink")
    assert engine.paths_between("probe:Service.audit", "probe:decoySink") == []
    graph = parse_directory(str(FIXTURES / "swift/ownership"), "swift")
    _assert_deferred_reachable(graph, "probe:install")
    assert graph.nodes["probe:install"].cyclomatic_complexity == 1
    (tmp_path / "a.swift").write_text("struct Service {}\nfunc sink() {}\n")
    (tmp_path / "b.swift").write_text("extension Service { func run() { sink() } }\n")
    graph = parse_directory(str(tmp_path), "swift")
    assert graph.nodes["a:Service"].kind == NodeKind.STRUCT
    assert _calls(graph, "a:Service.run") == ["a:sink"]
    assert any(
        e.source_id == "a:Service"
        and e.target_id == "a:Service.run"
        and e.kind == EdgeKind.CONTAINS
        for e in graph.edges
    )


def test_kotlin_nested_companions_and_local_owners() -> None:
    graph = parse_directory(str(FIXTURES / "kotlin/fixtures"), "kotlin")
    _assert_deferred_reachable(graph, "Ownership:caller")
    assert graph.nodes["Ownership:caller"].cyclomatic_complexity == 1
    assert _calls(graph, "Ownership:caller.neverCalled") == ["Ownership:hiddenSink"]
    assert _calls(graph, "Ownership:Outer.Nested.nestedMethod") == ["Ownership:hiddenSink"]
    assert _calls(graph, "Ownership:Outer.Companion.main") == ["Ownership:hiddenSink"]
    for name in ("invokeParameter", "invokeLocal"):
        assert "Calls:target" not in _calls(graph, f"Calls:{name}")
    engine = QueryEngine.from_directory(str(FIXTURES / "kotlin/fixtures"), language="kotlin")
    assert "Ownership:Outer.Companion.main" in {e["node_id"] for e in engine.attack_surface()}


def test_objc_selectors_dispatch_identity_and_blocks() -> None:
    graph = parse_directory(str(FIXTURES / "objc/fixtures/selectors"), "objc")
    assert _calls(graph, "Selectors:Worker.+touch") == ["Selectors:class_sink"]
    assert _calls(graph, "Selectors:Worker.touch") == ["Selectors:instance_sink"]
    messages = [e for e in graph.edges if dict(e.attributes).get("objc_selector")]
    assert len(messages) == 3
    assert {dict(e.attributes)["objc_selector"] for e in messages} == {"touch", "perform:other:"}
    assert any(
        e.target_id == "Selectors:Worker.touch" and e.confidence == EdgeConfidence.INFERRED
        for e in messages
    )
    assert all(
        e.confidence == EdgeConfidence.UNCERTAIN
        for e in messages
        if graph.nodes[e.target_id].kind == NodeKind.PROXY
    )
    assert len([e for e in graph.edges if e.kind == EdgeKind.CALLS]) == 6
    graph = parse_directory(str(FIXTURES / "objc/fixtures/blocks"), "objc")
    _assert_deferred_reachable(graph, "Blocks:build_callback")
    assert graph.nodes["Blocks:build_callback"].cyclomatic_complexity == 1
    block = next(
        n for n in graph.nodes.values() if "$block" in n.id and n.kind == NodeKind.FUNCTION
    )
    assert block.cyclomatic_complexity == 2
    assert _calls(graph, block.id) == ["Blocks:callback_sink"]


def test_dart_constructors_arrows_and_lexical_binding() -> None:
    graph = parse_file(str(FIXTURES / "dart/constructors.dart"), "dart", strict=True)
    constructors = [n for n in graph.nodes.values() if dict(n.attributes).get("dart_constructor")]
    assert {n.id for n in constructors} == {
        "constructors:Permit.Permit",
        "constructors:Permit.Permit.named",
        "constructors:Permit.Permit.create",
    }
    assert constructors[0].parameters[0].name == "token"
    assert _calls(graph, "constructors:Permit.Permit.named") == ["constructors:audit"]
    assert len(_calls(graph, "constructors:main")) == 3
    graph = parse_file(str(FIXTURES / "dart/bodies.dart"), "dart")
    for name in ("arrow", "asyncArrow", "C.arrow"):
        assert _calls(graph, f"bodies:{name}") == ["bodies:bodySink"]
    graph = parse_file(str(FIXTURES / "dart/binding.dart"), "dart")
    for name in ("parameterShadow", "localShadow", "Handler.implicit"):
        assert "binding:sink" not in _calls(graph, f"binding:{name}")
    assert _calls(graph, "binding:Handler.implicit") == ["binding:Handler.sink"]
    _assert_deferred_reachable(graph, "binding:deferred")


def test_circom_include_visibility_discard_and_main_arguments(tmp_path: Path) -> None:
    graph = parse_directory(str(FIXTURES / "circom/includes"), "circom")
    assert set(_calls(graph, "circuit:Root")) == {"lib.child:Child", "lib.child:width"}
    assert set(_calls(graph, "circuit:main")) == {"lib.child:Child", "lib.child:width"}
    graph = parse_file(str(FIXTURES / "circom/underscore/circuit.circom"), "circom", strict=True)
    assert _calls(graph, "circuit:Root") == ["circuit:Check"]
    graph = parse_file(str(FIXTURES / "circom/main_args/circuit.circom"), "circom")
    assert set(_calls(graph, "circuit:main")) == {"circuit:Child", "circuit:outer", "circuit:inner"}
    (tmp_path / "a.circom").write_text("pragma circom 2.1.8; template Child() {}")
    (tmp_path / "b.circom").write_text("pragma circom 2.1.8; template Child() {}")
    (tmp_path / "root.circom").write_text(
        'pragma circom 2.1.8; include "a.circom"; include "b.circom"; component main = Child();'
    )
    graph = parse_directory(str(tmp_path), "circom")
    assert all(target not in {"a:Child", "b:Child"} for target in _calls(graph, "root:main"))


def test_move_qualified_generics_callbacks_and_modules() -> None:
    graph = parse_directory(str(FIXTURES / "move/bindings"), "move")
    assert _calls(graph, "caller:0x42::caller:run").count("caller:0x42::caller:transfer") == 1
    assert _calls(graph, "caller:0x42::caller:run").count("library:0x43::library:transfer") == 3
    graph = parse_file(str(FIXTURES / "move/generic/generic.move"), "move", strict=True)
    assert _calls(graph, "generic:0x42::generic:run") == ["generic:0x42::generic:id"] * 2
    sites = [e.location.start_line for e in graph.edges if e.kind == EdgeKind.CALLS and e.location]
    assert sites == [4, 5]
    graph = parse_file(str(FIXTURES / "move/callback/callback.move"), "move", strict=True)
    callback_calls = [
        e
        for e in graph.edges
        if e.kind == EdgeKind.CALLS and e.source_id == "callback:0x42::callback:run"
    ]
    assert len(callback_calls) == 1
    assert callback_calls[0].confidence == EdgeConfidence.UNCERTAIN
    assert dict(callback_calls[0].attributes)["binding_blocked"] is True
    assert "callback:0x42::callback:handler" not in _calls(graph, "callback:0x42::callback:run")
    graph = parse_file(str(FIXTURES / "move/multi_module/multi.move"), "move", strict=True)
    assert {"multi:0x42::a:ping", "multi:0x42::b:ping"} <= graph.nodes.keys()
    graph = parse_file(str(FIXTURES / "move/grouped_import/grouped.move"), "move", strict=True)
    assert len(_calls(graph, "grouped:0x42::grouped:run")) == 2
    assert all(
        "0x43::library::transfer" in target for target in _calls(graph, "grouped:0x42::grouped:run")
    )


def test_move_entrypoint_modifiers_and_generic_comparison_control(tmp_path: Path) -> None:
    engine = QueryEngine.from_directory(str(FIXTURES / "move/entrypoints"), language="move")
    assert {row["node_id"].rsplit(":", 1)[-1] for row in engine.attack_surface()} == {
        "start",
        "indented_start",
        "public_api",
    }
    path = tmp_path / "nested.move"
    path.write_text(
        "module 0x1::test { fun id<T>(x: T): T { x } "
        "fun run(x: u64) { id<vector<vector<u64>>>(vector[]); if (x < 3) {}; } }"
    )
    graph = parse_file(str(path), "move", strict=True)
    assert _calls(graph, "nested:0x1::test:run") == ["nested:0x1::test:id"]


def test_tact_static_member_binding_and_receiver_signatures() -> None:
    graph = parse_file(str(FIXTURES / "tact/binding/wallet.tact"), "tact")
    targets = _calls(graph, "wallet:Wallet.receive")
    assert targets[:3] == ["wallet:check", "wallet:Wallet.check", "wallet:onlyGlobal"]
    assert targets[-1].startswith("proxy.")
    graph = parse_file(str(FIXTURES / "tact/coverage/wallet.tact"), "tact")
    receive = graph.nodes["wallet:Wallet.receive"]
    bounced = graph.nodes["wallet:Wallet.bounced"]
    assert receive.parameters[0].name == "msg"
    assert receive.parameters[0].type_ref == TypeRef("Ping")
    assert bounced.parameters[0].type_ref == TypeRef("bounced<Ping>")
    assert graph.nodes["wallet:Wallet.current"].return_type == TypeRef("Int")


@pytest.mark.parametrize(
    "case,package", [("repeated", "repeated"), ("cross_clause", "crossclause")]
)
def test_rego_preserves_all_clause_owners(case: str, package: str) -> None:
    graph = parse_directory(str(FIXTURES / "rego" / case), "rego")
    clauses = _calls(graph, f"{package}:allow")
    assert len(clauses) == (3 if case == "repeated" else 2)
    assert len(set(clauses)) == len(clauses)
    assert all(graph.nodes[clause].location.start_line > 0 for clause in clauses)
    targets = {target for clause in clauses for target in _calls(graph, clause)}
    assert targets == {f"{package}:first", f"{package}:second"}
    engine = QueryEngine.from_graph(graph)
    assert engine.paths_between(f"{package}:allow", f"{package}:second")


def test_rego_else_and_head_calls_stay_with_clause() -> None:
    graph = parse_directory(str(FIXTURES / "rego/multi_body"), "rego")
    clause = _calls(graph, "multibody:allow")[0]
    assert set(_calls(graph, clause)) == {"multibody:primary", "multibody:fallback"}
    clause = _calls(graph, "multibody:result")[0]
    assert _calls(graph, clause) == ["multibody:helper"]


@pytest.mark.parametrize(
    "language,path",
    [
        ("rego", "rego/inline_if/policy.rego"),
        ("move", "move/address_block/address.move"),
        ("circom", "circom/buses/circuit.circom"),
    ],
)
def test_unsupported_syntax_reports_diagnostics_and_strict_rejects(
    language: str, path: str
) -> None:
    graph = parse_file(str(FIXTURES / path), language)
    assert graph.parse_diagnostics
    assert all(d.location.start_line > 0 and d.message for d in graph.parse_diagnostics)
    with pytest.raises(ValueError, match="diagnostic"):
        parse_file(str(FIXTURES / path), language, strict=True)


def test_ruby_owners_vcalls_and_entrypoint_visibility() -> None:
    graph = parse_file(str(FIXTURES / "ruby/fixtures/namespaces.rb"), "ruby")
    assert {
        "namespaces:Admin.Worker.perform",
        "namespaces:Public.Worker.perform",
        "namespaces:Outer.Inner.execute",
    } <= graph.nodes.keys()
    graph = parse_file(str(FIXTURES / "ruby/fixtures/singleton.rb"), "ruby")
    assert _calls(graph, "singleton:Processor.process") == ["singleton:Processor.instance_sink"]
    assert _calls(graph, "singleton:Processor.self.process") == [
        "singleton:Processor.self.class_sink"
    ]
    graph = parse_file(str(FIXTURES / "ruby/fixtures/calls.rb"), "ruby")
    assert _calls(graph, "calls:Handler.run").count("calls:Handler.sink") == 7
    assert "calls:sink" not in _calls(graph, "calls:Handler.run")
    engine = QueryEngine.from_directory(str(FIXTURES / "ruby/fixtures"), language="ruby")
    ids = {row["node_id"] for row in engine.attack_surface()}
    assert "entrypoints:UsersController.show" in ids
    assert "entrypoints:EmailWorker.perform" in ids
    assert not ids & {
        "entrypoints:UsersController.internal_token",
        "entrypoints:UsersController.self.rebuild_index",
    }
    graph = parse_file(str(FIXTURES / "ruby/fixtures/modern.rb"), "ruby")
    assert len(graph.nodes["modern:configured"].parameters) == 6
    assert not _calls(graph, "modern:configured")
    assert not any(target.endswith(":item") for target in _calls(graph, "modern:iterate"))


def test_erlang_arity_exports_specs_and_qualified_calls() -> None:
    graph = parse_file(str(FIXTURES / "erlang/arity/arity_demo.erl"), "erlang")
    assert _calls(graph, "arity_demo:choose/0") == ["arity_demo:zero_sink/0"]
    assert "arity_demo:one_sink/1" in _calls(graph, "arity_demo:choose/1")
    assert graph.nodes["arity_demo:choose/0"].return_type == TypeRef("atom")
    assert graph.nodes["arity_demo:choose/1"].return_type == TypeRef("integer")
    engine = QueryEngine.from_directory(str(FIXTURES / "erlang/arity"), language="erlang")
    assert {row["node_id"] for row in engine.attack_surface()} == {
        "arity_demo:choose/1",
        "arity_demo:run0/0",
        "arity_demo:run1/0",
    }
    engine = QueryEngine.from_directory(str(FIXTURES / "erlang/qualified"), language="erlang")
    graph = parse_directory(str(FIXTURES / "erlang/qualified"), "erlang")
    assert _calls(graph, "caller:static_known/0") == ["local_target:hit/0"]
    assert all(target.startswith("proxy.") for target in _calls(graph, "caller:static_missing/0"))
    assert all("lists:reverse" in target for target in _calls(graph, "caller:imported/1"))
    assert "local_target:reverse/1" not in _calls(graph, "caller:imported/1")
    assert not engine.paths_between("caller:dynamic/2", "local_target:hit/0")


def test_erlang_closure_creation_capture_and_target_evaluation() -> None:
    graph = parse_file(str(FIXTURES / "erlang/closures/closure_demo.erl"), "erlang")
    for source in ("capture/0", "remote_capture/0"):
        assert not _calls(graph, f"closure_demo:{source}")
    _assert_deferred_reachable(graph, "closure_demo:deferred/0")
    assert graph.nodes["closure_demo:deferred/0"].cyclomatic_complexity == 1
    closures = [unit for unit in graph.nodes.values() if ".$fun@" in unit.id]
    assert len(closures) == 2
    assert all(_calls(graph, unit.id) == ["closure_demo:sink/0"] for unit in closures)
    assert len(_calls(graph, "closure_demo:immediate/0")) == 1
    assert "closure_demo:maker/0" in _calls(graph, "closure_demo:factory_call/0")


def test_haskell_lexical_owners_binding_and_call_syntax() -> None:
    engine = QueryEngine.from_directory(str(FIXTURES / "haskell/fixtures"), language="haskell")
    graph = parse_directory(str(FIXTURES / "haskell/fixtures"), "haskell")
    assert _calls(graph, "Binding:direct") == ["Binding:danger"]
    for source in ("applyParam", "lambdaShadow", "caseShadow", "passedOnly"):
        assert not engine.paths_between(f"Binding:{source}", "Binding:danger")
    for owner, local in (("whereOwner", "local"), ("letOwner", "helper")):
        ident = f"Binding:{owner}.{local}"
        assert _calls(graph, f"Binding:{owner}") == [ident]
        assert graph.nodes[f"Binding:{owner}"].cyclomatic_complexity == 1
        assert "Binding:danger" in _calls(graph, ident)
    assert graph.nodes["Binding:whereOwner.local"].cyclomatic_complexity == 3
    for source in ("viaPrefix", "viaParens", "unbox"):
        assert _calls(graph, f"Syntax:{source}") == ["Syntax:target"]
    assert _calls(graph, "Syntax:viaBacktick") == ["Syntax:add"]
    assert _calls(graph, "Syntax:viaOperator") == ["Syntax:<+>"]
    assert "Syntax:target" in _calls(graph, "Syntax:<+>")
    assert all(target.startswith("proxy.") for target in _calls(graph, "Syntax:viaQualified"))
    assert all(target.startswith("proxy.") for target in _calls(graph, "Syntax:viaDollar"))


def test_go_directory_linking_respects_package_even_in_polyglot(tmp_path: Path) -> None:
    (tmp_path / "app.go").write_text("package app\nfunc Run() { missing() }")
    (tmp_path / "other_test.go").write_text("package app_test\nfunc missing() {}")
    (tmp_path / "control.py").write_text("def missing(): pass\n")
    graph = parse_directory(str(tmp_path), "go,python")
    assert all(target.startswith("proxy.") for target in _calls(graph, "app:Run"))


def test_skipped_verus_reports_structured_diagnostic(tmp_path: Path) -> None:
    path = tmp_path / "hidden.rs"
    path.write_text("fn creator() { verus! { proof fn hidden() {} } }")
    graph = parse_file(str(path), "rust")
    assert any(
        d.grammar == "verus" and d.kind == "unsupported_syntax" for d in graph.parse_diagnostics
    )
    with pytest.raises(ValueError, match="diagnostic"):
        parse_file(str(path), "rust", strict=True)


def test_solidity_inherited_callable_storage_blocks_free_binding(tmp_path: Path) -> None:
    (tmp_path / "base.sol").write_text(
        "contract Base { function() internal work; function helper() internal {} }"
    )
    (tmp_path / "child.sol").write_text(
        'import "./base.sol"; function work() {} '
        "contract Child is Base { function run() public { work(); helper(); } }"
    )
    graph = parse_directory(str(tmp_path), "solidity")
    assert "child:work" not in _calls(graph, "child:Child.run")
    assert "base:Base.helper" in _calls(graph, "child:Child.run")
    assert any("$binding.work" in target for target in _calls(graph, "child:Child.run"))


def test_sql_replacement_signature_excludes_default_and_normalizes_schema(tmp_path: Path) -> None:
    path = tmp_path / "replace.sql"
    path.write_text(
        "CREATE FUNCTION APP.f(x integer DEFAULT 1) RETURNS integer "
        "AS $$ SELECT x $$ LANGUAGE SQL;\n"
        'CREATE OR REPLACE FUNCTION "app".f(x integer DEFAULT 2) RETURNS integer '
        "AS $$ SELECT x $$ LANGUAGE SQL;\n"
        "CREATE PROCEDURE APP.p(x integer DEFAULT 1) LANGUAGE SQL AS $$ SELECT x $$;\n"
        'CREATE OR REPLACE PROCEDURE "app".p(x integer DEFAULT 2) LANGUAGE SQL AS $$ SELECT x $$;\n'
    )
    graph = parse_file(str(path), "sql")
    routines = [
        unit
        for unit in graph.nodes.values()
        if unit.kind in {NodeKind.FUNCTION, NodeKind.PROCEDURE}
    ]
    assert len(routines) == 2
    for routine in routines:
        assert routine.parameters[0].default == "2"
        assert routine.parameters[0].type_ref == TypeRef("integer")


@pytest.mark.parametrize(
    "artifact",
    [
        {"index": -1},
        {"index": 7},
        {"index": True},
        {"uri": "app.py", "uriBaseId": "cycle"},
    ],
)
def test_sarif_invalid_bases_and_indices_remain_unmatched(tmp_path: Path, artifact: dict) -> None:
    graph = CodeGraph(root_path=str(tmp_path))
    graph.nodes["app"] = CodeUnit(
        "app", "app", NodeKind.FUNCTION, SourceLocation(str(tmp_path / "app.py"), 1, 3)
    )
    report = {
        "runs": [
            {
                "originalUriBaseIds": {"cycle": {"uri": "", "uriBaseId": "cycle"}},
                "results": [{"locations": [{"physicalLocation": {"artifactLocation": artifact}}]}],
            }
        ]
    }
    path = tmp_path / "report.sarif"
    path.write_text(json.dumps(report))
    assert augment_from_sarif(GraphStore(graph), str(path))["matched_findings"] == 0
    assert not graph.annotations


@pytest.mark.parametrize("definition", ["struct same;", "struct same { int value; };"])
def test_c_tag_collision_retains_both_containment_edges(tmp_path: Path, definition: str) -> None:
    source = tmp_path / "tag.c"
    source.write_text(f"int same(void) {{ return 0; }}\n{definition}\n")
    graph = parse_file(str(source), "c")
    assert graph.nodes["tag:same"].kind == NodeKind.STRUCT
    function = next(unit for unit in graph.nodes.values() if unit.kind == NodeKind.FUNCTION)
    children = {
        edge.target_id
        for edge in graph.edges
        if edge.kind == EdgeKind.CONTAINS and edge.source_id == "tag"
    }
    assert {"tag:same", function.id} <= children


def test_ruby_named_visibility_is_local_to_its_class(tmp_path: Path) -> None:
    (tmp_path / "nested.rb").write_text(
        "class Outer < ApplicationController\n def show; end\n"
        " class Inner < ApplicationController\n def show; end\n end\n private :show\nend\n"
    )
    engine = QueryEngine.from_directory(str(tmp_path), language="ruby")
    assert {row["node_id"] for row in engine.attack_surface()} == {"nested:Outer.Inner.show"}


def test_static_namespace_linking_keeps_file_local_visibility(tmp_path: Path) -> None:
    (tmp_path / "owner.cpp").write_text(
        "namespace Util { static void local() {} static void remote() {} }\n"
        "using namespace Util; void run() { local(); }"
    )
    (tmp_path / "other.cpp").write_text("void other() { remote(); }")
    graph = parse_directory(str(tmp_path), "cpp")
    assert _calls(graph, "owner:run") == ["owner:Util.local"]
    assert "owner:Util.remote" not in _calls(graph, "other:other")


def test_cross_file_linking_go_same_package_and_python_import(tmp_path: Path) -> None:
    (tmp_path / "app.go").write_text("package app\nfunc Run() { helper() }")
    (tmp_path / "helper.go").write_text("package app\nfunc helper() {}")
    graph = parse_directory(str(tmp_path), "go")
    assert _calls(graph, "app:Run") == ["helper:helper"]
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "helper.py").write_text("def helper(): pass\n")
    (tmp_path / "app.py").write_text("from pkg.helper import helper\ndef run(): helper()\n")
    graph = parse_directory(str(tmp_path), "python")
    assert _calls(graph, "app:run") == ["pkg.helper:helper"]


@pytest.mark.parametrize("kind,blocked", [(EdgeKind.CALLS, True), (EdgeKind.READS, False)])
def test_rust_alias_resolution_preserves_blocked_and_noncall_edges(
    tmp_path: Path, kind: EdgeKind, blocked: bool
) -> None:
    source = b"fn real() {} fn caller() {} use self::real as alias;"
    path = tmp_path / "scope.rs"
    path.write_bytes(source)
    parser = RustParser()
    graph = parser.parse_file(str(path))
    declaration = parser._parser.parse(source).root_node.named_children[-1]
    context = _Resolution(graph, {"scope": [("alias", "scope:real", declaration)]}, {}, {}, "scope")
    control = CodeEdge("scope:caller", "scope:alias", EdgeKind.CALLS)
    assert _resolve_edge(control, context).target_id == "scope:real"
    edge = CodeEdge("scope:caller", "scope:alias", kind, attributes=(("binding_blocked", blocked),))
    assert _resolve_edge(edge, context) == edge


def _assert_deferred_reachable(graph: CodeGraph, source: str) -> None:
    edges = [
        edge for edge in graph.edges if edge.kind == EdgeKind.CALLS and edge.source_id == source
    ]
    assert edges
    assert all(
        edge.confidence == EdgeConfidence.UNCERTAIN and dict(edge.attributes).get("deferred")
        for edge in edges
    )
    reachable = {unit["id"] for unit in QueryEngine.from_graph(graph).reachable_from(source)}
    assert {edge.target_id for edge in edges} <= reachable
    assert all(
        graph.nodes[edge.target_id].kind in {NodeKind.FUNCTION, NodeKind.METHOD} for edge in edges
    )
