"""Standard bounds-check wrappers retain explicit source calls."""

from pathlib import Path

import pytest

from trailmark.models.edges import EdgeKind
from trailmark.models.nodes import NodeOrigin
from trailmark.parse import parse_file
from trailmark.query.api import QueryEngine


@pytest.mark.mutation_fast
def test_inbounds_assignment_retains_callback_site_and_parameter_shadow(tmp_path: Path) -> None:
    path = tmp_path / "sample.jl"
    path.write_text(
        "f(x) = x\nfunction apply_values!(f, vals)\n"
        " for i in 1:length(vals)\n  @inbounds vals[i] = f(vals[i])\n end\nend\n"
    )
    graph = parse_file(str(path), "julia", strict=True)
    calls = [
        e
        for e in graph.edges
        if e.kind == EdgeKind.CALLS and e.location and e.location.start_line == 4
    ]
    assert len(calls) == 1
    assert calls[0].source_id == "sample:apply_values!"
    assert calls[0].location and calls[0].location.start_col == 22
    assert graph.nodes[calls[0].target_id].origin == NodeOrigin.PROXY
    assert calls[0].target_id != "sample:f"
    assert QueryEngine.from_graph(graph).paths_between(calls[0].source_id, calls[0].target_id)


@pytest.mark.parametrize("body", ["@inbounds helper(x)", "@inbounds begin\n helper(x)\nend"])
def test_inbounds_body_calls_keep_their_lexical_function(tmp_path: Path, body: str) -> None:
    path = tmp_path / "sample.jl"
    path.write_text(f"helper(x) = x\nrun(x) = {body}\n")
    graph = parse_file(str(path), "julia", strict=True)
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 1
    assert (calls[0].source_id, calls[0].target_id) == ("sample:run", "sample:helper")


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    "prefix,suffix",
    [
        ("macro inbounds(ex)\n :(nothing)\nend\n", ""),
        ("using Other: @inbounds\n", ""),
        ('const var"@inbounds" = other\n', ""),
        ("import Other.@inbounds\n", ""),
        ("using Other\n", ""),
        ('include("macros.jl")\n', ""),
        ("baremodule M\n", "end\n"),
    ],
)
def test_macro_binding_barriers_keep_inbounds_opaque(
    tmp_path: Path, prefix: str, suffix: str
) -> None:
    path = tmp_path / "sample.jl"
    path.write_text(prefix + "helper(x) = x\nrun(x) = @inbounds helper(x)\n" + suffix)
    graph = parse_file(str(path), "julia")
    assert graph.parse_diagnostics
    assert not any(e.kind == EdgeKind.CALLS and e.source_id.endswith("run") for e in graph.edges)


@pytest.mark.mutation_fast
def test_macro_redefinition_in_another_module_does_not_hide_standard_wrapper(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sample.jl"
    path.write_text(
        "module A\nmacro inbounds(ex)\n :(nothing)\nend\nend\n"
        "module B\nhelper(x) = x\nrun(x) = @inbounds helper(x)\nend\n"
    )
    graph = parse_file(str(path), "julia", strict=True)
    assert any(
        e.kind == EdgeKind.CALLS
        and e.source_id == "sample:B.run"
        and e.target_id == "sample:B.helper"
        for e in graph.edges
    )


def test_quoted_wrapper_and_unknown_macro_do_not_supply_runtime_calls(tmp_path: Path) -> None:
    path = tmp_path / "sample.jl"
    path.write_text("helper(x) = x\ndata() = :(@inbounds helper(1))\nrun() = @unknown helper(1)\n")
    graph = parse_file(str(path), "julia")
    assert graph.parse_diagnostics
    assert not any(e.kind == EdgeKind.CALLS for e in graph.edges)


@pytest.mark.mutation_fast
def test_quoted_macro_definition_does_not_change_active_wrapper_binding(tmp_path: Path) -> None:
    path = tmp_path / "sample.jl"
    path.write_text(
        "data = quote\nmacro inbounds(ex)\n :(nothing)\nend\nend\n"
        "helper(x) = x\nrun(x) = @inbounds helper(x)\n"
    )
    graph = parse_file(str(path), "julia", strict=True)
    assert any(
        e.kind == EdgeKind.CALLS and e.source_id == "sample:run" and e.target_id == "sample:helper"
        for e in graph.edges
    )
