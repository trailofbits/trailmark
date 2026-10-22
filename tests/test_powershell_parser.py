"""PowerShell binding, script block ownership and public integration."""

from pathlib import Path

import pytest
from tree_sitter import Parser
from tree_sitter_language_pack import get_language

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.nodes import NodeKind
from trailmark.parse import detect_languages, parse_directory, parse_file
from trailmark.query.api import QueryEngine


def _file(tmp_path: Path, source: str) -> Path:
    assert not Parser(get_language("powershell")).parse(source.encode()).root_node.has_error
    path = tmp_path / "sample.ps1"
    path.write_text(source)
    return path


def test_parameters_filters_classes_and_case_insensitive_calls(tmp_path: Path) -> None:
    path = _file(
        tmp_path,
        """param([string]$Name)
function Get-Value {
 param([ValidateRange(1,5)][int]$Count = 1)
 if ($Count -gt 0) { Write-Output $Count }
}
filter Select-Thing { $_ }
function main { get-value -Count 2 }
class Thing { [int] Run([int]$x) { return $x } }
main
""",
    )
    graph = parse_file(str(path))
    assert graph.nodes["sample:<script>"].parameters[0].name == "$Name"
    helper = graph.nodes["sample:Get-Value"]
    assert helper.parameters[0].type_ref is not None
    assert helper.parameters[0].type_ref.name == "int"
    assert helper.parameters[0].default == "1" and helper.cyclomatic_complexity == 2
    assert graph.nodes["sample:Thing.Run"].kind == NodeKind.METHOD
    assert graph.nodes["sample:Select-Thing"].kind == NodeKind.FUNCTION
    assert any(
        e.source_id == "sample:main"
        and e.target_id == helper.id
        and e.confidence == EdgeConfidence.INFERRED
        for e in graph.edges
    )
    engine = QueryEngine.from_directory(str(tmp_path), language="auto")
    assert engine.entrypoint_paths_to("Get-Value")


def test_empty_functions_and_nested_script_blocks_have_own_bodies(tmp_path: Path) -> None:
    path = _file(
        tmp_path,
        """function helper {}
function main {
 $callback = { if ($true) { helper } }
 helper
}
""",
    )
    graph = parse_file(str(path))
    assert "sample:helper" in graph.nodes
    anonymous = [u for u in graph.nodes.values() if u.name.startswith("<lambda>")]
    assert len(anonymous) == 1 and anonymous[0].cyclomatic_complexity == 2
    assert graph.nodes["sample:main"].cyclomatic_complexity == 1
    calls = [
        e
        for e in graph.edges
        if e.kind == EdgeKind.CALLS and not dict(e.attributes).get("deferred")
    ]
    assert len(calls) == 2 and {e.source_id for e in calls} == {"sample:main", anonymous[0].id}


@pytest.mark.parametrize(
    "setup",
    [
        "Set-Alias helper other",
        "Set-Item Function:helper $other",
        "Invoke-Expression $code",
        ". ./other.ps1",
        "Import-Module Other",
    ],
)
def test_runtime_mutation_prevents_inference(tmp_path: Path, setup: str) -> None:
    path = _file(tmp_path, f"function helper {{}}\n{setup}\nfunction main {{ helper }}\n")
    graph = parse_file(str(path))
    assert all(
        e.confidence == EdgeConfidence.UNCERTAIN for e in graph.edges if e.kind == EdgeKind.CALLS
    )


def test_ambiguous_receivers_callbacks_and_cross_file_namesakes(tmp_path: Path) -> None:
    _file(
        tmp_path,
        """function helper {}
function HELPER {}
function main { helper; & $helper; $obj.helper(); [Other]::helper() }
""",
    )
    (tmp_path / "other.psm1").write_text("function helper {}\n")
    graph = parse_directory(str(tmp_path), language="auto")
    assert detect_languages(str(tmp_path)) == ["powershell"]
    assert graph.source_file_counts == {"powershell": 2}
    calls = [
        e
        for e in graph.edges
        if e.kind == EdgeKind.CALLS and not dict(e.attributes).get("deferred")
    ]
    assert len(calls) == 4
    assert all(graph.nodes[e.target_id].kind == NodeKind.PROXY for e in calls)
    assert (
        len(
            [
                u
                for u in graph.nodes.values()
                if u.kind == NodeKind.FUNCTION and u.name.casefold() == "helper"
            ]
        )
        == 3
    )


def test_literal_dependencies(tmp_path: Path) -> None:
    path = _file(
        tmp_path, "using module ./One.psm1\nImport-Module -Name 'Two'\nImport-Module $dynamic\n"
    )
    assert parse_file(str(path)).dependencies == ["./One.psm1", "Two"]


def test_parse_errors_warn(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "broken.ps1"
    path.write_text("function broken { if (")
    parse_file(str(path))
    assert "PowerShell syntax errors" in caplog.text
