"""VB.NET case binding, overloads, entrypoints and dynamic boundaries."""

from pathlib import Path

import pytest
from tree_sitter import Parser
from tree_sitter_language_pack import get_language

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.nodes import NodeKind
from trailmark.parse import detect_languages, parse_directory, parse_file
from trailmark.query.api import QueryEngine


def _file(tmp_path: Path, source: str) -> Path:
    assert not Parser(get_language("vb")).parse(source.encode()).root_node.has_error
    path = tmp_path / "sample.vb"
    path.write_text(source)
    return path


def test_containers_signatures_case_binding_and_entrypoint(tmp_path: Path) -> None:
    path = _file(
        tmp_path,
        """Imports System
Namespace Demo
Module App
 Function Helper(ByVal x As Integer, Optional y As Integer = 1) As Integer
  If x > 0 Then
   Return y
  End If
  Return 0
 End Function
 Sub MAIN(args As String())
  helper(1)
  App.HELPER(2)
 End Sub
End Module
End Namespace
""",
    )
    graph = parse_file(str(path))
    helper = graph.nodes["sample:Demo.App.Helper"]
    assert (
        helper.parameters[0].type_ref is not None
        and helper.parameters[0].type_ref.name == "Integer"
    )
    assert helper.parameters[1].default == "1" and helper.cyclomatic_complexity == 2
    assert graph.dependencies == ["System"]
    calls = [
        e
        for e in graph.edges
        if e.kind == EdgeKind.CALLS and not dict(e.attributes).get("deferred")
    ]
    assert len(calls) == 2 and all(e.target_id == helper.id for e in calls)
    engine = QueryEngine.from_directory(str(tmp_path), language="auto")
    assert {e["node_id"] for e in engine.attack_surface()} == {"sample:Demo.App.MAIN"}
    assert engine.entrypoint_paths_to("Helper")
    assert detect_languages(str(tmp_path)) == ["vb"]
    assert parse_directory(str(tmp_path), language="vb").source_file_counts == {"vb": 1}


def test_overloads_receivers_and_delegate_shadows_stay_uncertain(tmp_path: Path) -> None:
    path = _file(
        tmp_path,
        """Module App
Sub Helper(x As Integer)
End Sub
Sub Helper(x As String)
End Sub
Sub Main(helper As Object, receiver As Object)
 Helper(1)
 receiver.Helper()
 App.Helper(1)
End Sub
End Module
""",
    )
    graph = parse_file(str(path))
    helpers = [u for u in graph.nodes.values() if u.name == "Helper"]
    assert len(helpers) == 2 and all("@" in u.id for u in helpers)
    calls = [
        e
        for e in graph.edges
        if e.kind == EdgeKind.CALLS and not dict(e.attributes).get("deferred")
    ]
    assert len(calls) == 3 and all(graph.nodes[e.target_id].kind == NodeKind.PROXY for e in calls)
    assert all(e.confidence == EdgeConfidence.UNCERTAIN for e in calls)


@pytest.mark.parametrize(
    ("header", "method", "expected"),
    [
        ("Module App", "Sub Main()", True),
        ("Class App", "Shared Function Main(args As String()) As Integer", True),
        ("Class App", "Sub main()", False),
        ("Module App", "Sub Main(x As Boolean)", False),
    ],
)
def test_cli_signature_eligibility(
    tmp_path: Path, header: str, method: str, expected: bool
) -> None:
    kind = "Function" if "Function" in method else "Sub"
    end = "Module" if header.startswith("Module") else "Class"
    _file(tmp_path, f"{header}\n{method}\nEnd {kind}\nEnd {end}\n")
    engine = QueryEngine.from_directory(str(tmp_path), language="vb")
    assert bool(engine.attack_surface()) == expected


def test_lambdas_accessors_constructors_and_bare_calls_have_owners(tmp_path: Path) -> None:
    path = _file(
        tmp_path,
        """Class App
Sub New()
 Helper()
End Sub
Sub Helper()
End Sub
Sub Run()
 Dim cb = Function(x As Integer) Helper()
 Call Helper()
 Helper
End Sub
Property Value As Integer
 Get
  Return Helper()
 End Get
 Set(value As Integer)
  Helper()
 End Set
End Property
End Class
""",
    )
    graph = parse_file(str(path))
    assert {"sample:App.New", "sample:App.Value.get", "sample:App.Value.set"} <= graph.nodes.keys()
    anonymous = [u for u in graph.nodes.values() if u.name.startswith("<lambda>")]
    assert len(anonymous) == 1 and len(anonymous[0].parameters) == 1
    calls = [
        e
        for e in graph.edges
        if e.kind == EdgeKind.CALLS and not dict(e.attributes).get("deferred")
    ]
    assert len(calls) == 6
    assert len([e for e in calls if e.source_id == "sample:App.Run"]) == 2


def test_namespace_receiver_and_captured_local_shadows_stay_uncertain(tmp_path: Path) -> None:
    path = _file(
        tmp_path,
        """Namespace Demo
Module App
Sub Helper()
End Sub
Sub Run(Demo As Object)
 Dim cb = Function() Helper()
 Dim Helper As Object
 Demo.App.Helper()
End Sub
End Module
End Namespace
""",
    )
    graph = parse_file(str(path))
    calls = [
        e
        for e in graph.edges
        if e.kind == EdgeKind.CALLS and not dict(e.attributes).get("deferred")
    ]
    assert len(calls) == 2
    assert all(graph.nodes[e.target_id].kind == NodeKind.PROXY for e in calls)


def test_parse_errors_warn(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "broken.vb"
    path.write_text("Module App\nSub broken(")
    parse_file(str(path))
    assert "VB.NET syntax errors" in caplog.text
