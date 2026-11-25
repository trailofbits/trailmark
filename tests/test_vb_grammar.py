"""VB grammar repairs retain real source spans without masking declarations."""

from pathlib import Path

import pytest

from trailmark.models.edges import EdgeKind
from trailmark.models.nodes import NodeOrigin
from trailmark.parse import parse_directory, parse_file


@pytest.mark.mutation_fast
@pytest.mark.parametrize("separator", ["\n", " : ", "\n\n ' note\n"])
def test_class_inheritance_follows_the_header_terminator(tmp_path: Path, separator: str) -> None:
    path = tmp_path / "sample.vb"
    path.write_text(
        f"Public Class A{separator}Inherits B\n"
        " Public Sub Run()\n  Helper()\n End Sub\n"
        " Private Sub Helper()\n End Sub\nEnd Class\n"
    )
    graph = parse_file(str(path), "vb")
    assert not graph.parse_diagnostics
    assert "sample:A.Run" in graph.nodes and "sample:A.Helper" in graph.nodes
    relation = next(e for e in graph.edges if e.kind == EdgeKind.INHERITS)
    assert relation.source_id == "sample:A" and relation.location is not None
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)
    assert call.target_id == "sample:A.Helper"
    assert call.location is not None and call.location.start_line == 3 + separator.count("\n")


def test_rem_prefix_does_not_consume_next_class_or_property(tmp_path: Path) -> None:
    path = tmp_path / "sample.vb"
    path.write_text(
        "Class First\nEnd Class\nClass Second\n"
        " ReadOnly Property Remaining As Integer\n Get\n Return 1\n End Get\n End Property\n"
        " Sub Remove()\n REM Fake() : Class Ghost\n End Sub\nEnd Class\n"
    )
    graph = parse_file(str(path), "vb")
    assert not graph.parse_diagnostics
    assert {
        "sample:First",
        "sample:Second",
        "sample:Second.Remaining.get",
        "sample:Second.Remove",
    } <= graph.nodes.keys()
    assert not any(e.kind == EdgeKind.CALLS for e in graph.edges)
    assert not any(u.name == "Ghost" for u in graph.nodes.values())


@pytest.mark.mutation_fast
def test_generic_parameters_and_new_arguments_keep_exact_sites(tmp_path: Path) -> None:
    path = tmp_path / "sample.vb"
    path.write_text(
        "Class Box\n Sub New(values As Dictionary(Of String, Integer))\n End Sub\n"
        " Function Make(values As Dictionary(Of String, Integer)) As Box\n"
        "  Return New Box(values)\n End Function\nEnd Class\n"
    )
    graph = parse_file(str(path), "vb")
    assert not graph.parse_diagnostics
    assert graph.nodes["sample:Box.New"].parameters[0].type_ref is not None
    assert (
        graph.nodes["sample:Box.New"].parameters[0].type_ref.name
        == "Dictionary(Of String, Integer)"
    )
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)
    assert call.target_id == "sample:Box.New"
    assert call.location is not None and call.location.start_line == 5


def test_local_arrays_static_primitive_calls_and_colon_calls_keep_main_body(tmp_path: Path) -> None:
    path = tmp_path / "sample.vb"
    path.write_text(
        "Module App\n Sub Main()\n Dim args() As String = GetArgs()\n"
        " If args.Length <> 3 OrElse String.IsNullOrEmpty(args(1)) Then\n"
        "  ShowSyntax() : ShowSyntax()\n End If\n Dim buffer(99) As Char\n"
        " End Sub\n Sub ShowSyntax()\n End Sub\nEnd Module\n"
    )
    graph = parse_file(str(path), "vb")
    assert not graph.parse_diagnostics
    assert graph.nodes["sample:App.Main"].location.end_line == 9
    calls = [
        e
        for e in graph.edges
        if e.kind == EdgeKind.CALLS and e.target_id == "sample:App.ShowSyntax"
    ]
    assert len(calls) == 2
    assert all(
        e.source_id == "sample:App.Main" and e.location and e.location.start_line == 5
        for e in calls
    )
    assert calls[0].location != calls[1].location


def test_interpolated_apostrophes_and_rem_text_are_literal(tmp_path: Path) -> None:
    path = tmp_path / "sample.vb"
    path.write_text(
        "Module App\n Sub Main()\n"
        "  Print($\"source {path} '{path}' REM Fake() : Class Ghost\") : Helper()\n"
        " End Sub\n Sub Helper()\n End Sub\nEnd Module\n"
    )
    graph = parse_file(str(path), "vb")
    assert not graph.parse_diagnostics
    assert any(e.target_id == "sample:App.Helper" for e in graph.edges)
    assert not any("Fake" in e.target_id for e in graph.edges)
    assert not any(u.name == "Ghost" for u in graph.nodes.values())


def test_using_as_new_retains_constructor_and_argument_calls(tmp_path: Path) -> None:
    path = tmp_path / "sample.vb"
    path.write_text(
        "Module App\n Sub Main()\n Using resource As New Box(Value())\n End Using\n End Sub\n"
        " Function Value() As Integer\n Return 1\n End Function\nEnd Module\n"
        "Class Box\n Sub New(value As Integer)\n End Sub\nEnd Class\n"
    )
    graph = parse_file(str(path), "vb")
    assert not graph.parse_diagnostics
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert {e.target_id for e in calls} == {"sample:Box.New", "sample:App.Value"}


@pytest.mark.parametrize("ambiguous", [False, True])
def test_cross_file_constructor_binding_requires_a_unique_inspected_type(
    tmp_path: Path, ambiguous: bool
) -> None:
    (tmp_path / "box.vb").write_text("Class Box\n Sub New()\n End Sub\nEnd Class\n")
    if ambiguous:
        (tmp_path / "other.vb").write_text("Class Box\nEnd Class\n")
    (tmp_path / "entry.vb").write_text(
        "Module App\n Sub Main()\n Dim item = New Box()\n End Sub\nEnd Module\n"
    )
    graph = parse_directory(str(tmp_path), "vb")
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)
    if ambiguous:
        assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY
    else:
        assert call.target_id == "box:Box.New"


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    "definition,invocation",
    [
        ("Class Box\nEnd Class\n", "New External.Box()"),
        (
            "Class Box\n Sub New()\n End Sub\n Sub New(x As Integer)\n End Sub\nEnd Class\n",
            "New Box()",
        ),
    ],
)
def test_qualified_unknown_and_overloaded_constructors_remain_proxies(
    tmp_path: Path, definition: str, invocation: str
) -> None:
    path = tmp_path / "sample.vb"
    path.write_text(
        definition + f"Module App\n Sub Main()\n Dim item = {invocation}\n End Sub\nEnd Module\n"
    )
    graph = parse_file(str(path), "vb")
    assert not graph.parse_diagnostics
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)
    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY


@pytest.mark.mutation_fast
def test_constructor_in_recovery_file_remains_uncertain(tmp_path: Path) -> None:
    path = tmp_path / "sample.vb"
    path.write_text(
        "Class Box\nEnd Class\nModule App\n Sub Main()\n Dim item = New Box()\n"
        " ???\n End Sub\nEnd Module\n"
    )
    graph = parse_file(str(path), "vb")
    assert graph.parse_diagnostics
    call = next(e for e in graph.edges if dict(e.attributes).get("constructor_type"))
    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    "context",
    [
        "Class Holder(Of Packet As New)\n Sub Run()\n Dim item = New Packet()\n"
        " End Sub\nEnd Class\n",
        "Imports Packet = Other\nNamespace A\nClass Holder\n Sub Run()\n"
        " Dim item = New Packet()\n End Sub\nEnd Class\nEnd Namespace\n",
    ],
)
def test_constructor_type_parameters_and_import_aliases_block_short_name_guessing(
    tmp_path: Path, context: str
) -> None:
    (tmp_path / "packet.vb").write_text("Class Packet\n Sub New()\n End Sub\nEnd Class\n")
    path = tmp_path / "entry.vb"
    path.write_text(context)
    graph = parse_directory(str(tmp_path), "vb")
    assert not graph.parse_diagnostics
    call = next(e for e in graph.edges if dict(e.attributes).get("constructor_type"))
    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY


def test_unrelated_namespace_type_does_not_satisfy_bare_constructor(tmp_path: Path) -> None:
    (tmp_path / "packet.vb").write_text("Namespace Other\nClass Packet\nEnd Class\nEnd Namespace\n")
    (tmp_path / "entry.vb").write_text(
        "Namespace A\nClass Holder\n Sub Run()\n Dim item = New Packet()\n"
        " End Sub\nEnd Class\nEnd Namespace\n"
    )
    graph = parse_directory(str(tmp_path), "vb")
    assert not graph.parse_diagnostics
    call = next(e for e in graph.edges if dict(e.attributes).get("constructor_type"))
    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY
