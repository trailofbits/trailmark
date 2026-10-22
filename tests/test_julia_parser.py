"""Julia syntax, callable ownership, and conservative dispatch boundaries."""

from pathlib import Path

import pytest
from tree_sitter import Parser
from tree_sitter_language_pack import get_language

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind
from trailmark.parse import detect_languages, parse_directory, parse_file
from trailmark.query.api import QueryEngine


def _source(tmp_path: Path, text: str) -> Path:
    assert not Parser(get_language("julia")).parse(text.encode()).root_node.has_error
    path = tmp_path / "sample.jl"
    path.write_text(text)
    return path


def _calls(graph: CodeGraph, caller: str) -> list[tuple[str, EdgeConfidence]]:
    return [
        (e.target_id, e.confidence)
        for e in graph.edges
        if e.kind == EdgeKind.CALLS and e.source_id == caller
    ]


def test_modules_types_signatures_and_entrypoint(tmp_path: Path) -> None:
    _source(
        tmp_path,
        """module Demo
abstract type Shape end
primitive type Bits 8 end
struct Box{T}
    value::T
end
leaf(x::Int=1)::Int = x
function helper(xs::Int...; fallback=leaf())::Int
    if isempty(xs)
        return fallback
    end
    leaf(1)
end
short(x::T) where {T} = leaf(x)
function main()
    helper(1)
end
end
""",
    )
    graph = parse_directory(str(tmp_path), language="auto")
    assert detect_languages(str(tmp_path)) == ["julia"]
    assert graph.source_file_counts == {"julia": 1}
    assert graph.nodes["sample:Demo"].kind == NodeKind.NAMESPACE
    assert graph.nodes["sample:Demo.Shape"].kind == NodeKind.INTERFACE
    assert graph.nodes["sample:Demo.Bits"].kind == NodeKind.STRUCT
    assert graph.nodes["sample:Demo.Box"].kind == NodeKind.STRUCT
    helper = graph.nodes["sample:Demo.helper"]
    assert [p.name for p in helper.parameters] == ["xs...", "fallback"]
    assert helper.parameters[0].type_ref is not None
    assert helper.parameters[0].type_ref.name == "Int"
    assert helper.parameters[1].default == "leaf()"
    assert helper.return_type is not None
    assert helper.return_type.name == "Int"
    assert helper.cyclomatic_complexity == 2
    assert _calls(graph, "sample:Demo.short") == [("sample:Demo.leaf", EdgeConfidence.INFERRED)]
    engine = QueryEngine.from_directory(str(tmp_path), language="julia")
    assert engine.entrypoint_paths_to("helper")


def test_all_dispatch_candidates_survive_without_guessing(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        """f(x::Int) = x
f(x::String) = x
main() = f(1)
""",
    )
    graph = parse_file(str(path))
    ids = [n.id for n in graph.nodes.values() if n.name == "f" and n.kind != NodeKind.PROXY]
    assert len(ids) == 2
    assert all("@" in ident for ident in ids)
    target, confidence = _calls(graph, "sample:main")[0]
    assert confidence == EdgeConfidence.UNCERTAIN
    assert graph.nodes[target].kind == NodeKind.PROXY


def test_closures_and_do_blocks_own_calls_and_branches(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        """leaf(x) = x
function outer()
    first = x -> leaf(x)
    second = function(x)
        if x > 0
            leaf(x)
        end
    end
    map([1]) do item
        leaf(item)
    end
    first(1)
    second(2)
end
""",
    )
    graph = parse_file(str(path))
    assert _calls(graph, "sample:outer.first") == [("sample:leaf", EdgeConfidence.INFERRED)]
    assert graph.nodes["sample:outer.second"].cyclomatic_complexity == 2
    anonymous = next(n for n in graph.nodes.values() if n.name.startswith("<lambda>"))
    assert _calls(graph, anonymous.id) == [("sample:leaf", EdgeConfidence.INFERRED)]
    outer_calls = _calls(graph, "sample:outer")
    assert len(outer_calls) == 4
    assert (anonymous.id, EdgeConfidence.UNCERTAIN) in outer_calls
    assert [call for call in outer_calls if call[1] != EdgeConfidence.UNCERTAIN] == [
        ("sample:outer.first", EdgeConfidence.INFERRED),
        ("sample:outer.second", EdgeConfidence.INFERRED),
    ]
    assert graph.nodes["sample:outer"].cyclomatic_complexity == 1


def test_shadowing_aliases_qualified_and_cross_file_calls_stay_unresolved(tmp_path: Path) -> None:
    _source(
        tmp_path,
        """helper() = 1
function caller(helper, receiver)
    helper()
    receiver.helper()
end
alias = helper
alias()
missing()
helper.([1])
""",
    )
    (tmp_path / "other.jl").write_text("alias() = 1\nmissing() = 2\n")
    graph = parse_directory(str(tmp_path), language="julia")
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 5
    assert all(e.confidence == EdgeConfidence.UNCERTAIN for e in calls)
    assert all(graph.nodes[e.target_id].kind == NodeKind.PROXY for e in calls)


@pytest.mark.parametrize(
    "binding",
    [
        "helper = callback",
        "helper += callback",
        "helper, x = values",
        "local helper",
        "for helper in xs\nhelper()\nend",
    ],
)
def test_local_writes_and_loop_variables_shadow_functions(tmp_path: Path, binding: str) -> None:
    path = _source(tmp_path, f"helper() = 1\nfunction caller()\n{binding}\nhelper()\nend\n")
    graph = parse_file(str(path))
    assert all(c == EdgeConfidence.UNCERTAIN for _, c in _calls(graph, "sample:caller"))


def test_conditional_definitions_script_order_and_module_boundaries(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        """late()
late() = 1
late()
if enabled
    maybe() = 1
end
maybe()
module Nested
    main() = late()
end
""",
    )
    graph = parse_file(str(path))
    calls = _calls(graph, "sample:<script>")
    assert [c for _, c in calls] == [
        EdgeConfidence.UNCERTAIN,
        EdgeConfidence.INFERRED,
        EdgeConfidence.UNCERTAIN,
    ]
    assert _calls(graph, "sample:Nested.main")[0][1] == EdgeConfidence.UNCERTAIN


def test_keyword_arguments_do_not_shadow_function_names(tmp_path: Path) -> None:
    path = _source(tmp_path, "helper(; x=1) = x\nmain() = helper(; helper=1)\n")
    assert _calls(parse_file(str(path)), "sample:main") == [
        ("sample:helper", EdgeConfidence.INFERRED)
    ]


def test_imports_and_literal_includes(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        """using LinearAlgebra
import Statistics: mean
include("helpers.jl")
include("$dynamic.jl")
include(path)
helper() = 1
main() = helper()
""",
    )
    graph = parse_file(str(path))
    assert graph.dependencies == ["LinearAlgebra", "Statistics", "helpers.jl"]
    assert _calls(graph, "sample:main")[0][1] == EdgeConfidence.UNCERTAIN


def test_shadowed_include_and_import_aliases(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        """import Other as Alias
import Third: helper as callme
include(path) = path
callme() = 1
main() = callme()
include("not-a-file.jl")
""",
    )
    graph = parse_file(str(path))
    assert graph.dependencies == ["Other", "Third"]
    assert _calls(graph, "sample:main")[0][1] == EdgeConfidence.UNCERTAIN


def test_transparent_macros_and_opaque_expansion_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = _source(tmp_path, "@inline helper(x) = x\nmain() = helper(1)\n")
    graph = parse_file(str(path))
    assert _calls(graph, "sample:main") == [("sample:helper", EdgeConfidence.INFERRED)]
    path.write_text(
        "helper() = 1\nmain() = helper()\n@generated function generated(x)\n hidden(x)\nend\n"
    )
    graph = parse_file(str(path))
    assert "macro expansion skipped" in caplog.text
    assert not any(n.name == "generated" for n in graph.nodes.values())
    assert _calls(graph, "sample:main")[0][1] == EdgeConfidence.UNCERTAIN
    assert len([e for e in graph.edges if e.kind == EdgeKind.CALLS]) == 1


def test_constructor_and_imported_names_are_not_guessed(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        """struct Box
    value::Int
    Box(x) = new(x)
end
import Other: helper
helper() = 1
main() = helper()
Box(1)
""",
    )
    graph = parse_file(str(path))
    assert graph.nodes["sample:Box"].kind == NodeKind.STRUCT
    assert all(
        e.confidence == EdgeConfidence.UNCERTAIN for e in graph.edges if e.kind == EdgeKind.CALLS
    )


def test_syntax_error_warns_and_prevents_inference(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "broken.jl"
    path.write_text("helper() = 1\nmain() = helper()\nfunction broken(\n")
    graph = parse_file(str(path))
    assert "syntax errors" in caplog.text
    assert all(
        e.confidence == EdgeConfidence.UNCERTAIN for e in graph.edges if e.kind == EdgeKind.CALLS
    )


def test_closure_writes_and_catch_bindings_prevent_false_calls(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        """function outer()
    helper() = 1
    inner() = (helper = callback)
    helper()
end
helper() = 1
function catcher()
    try
        risky()
    catch helper
        helper()
    end
end
""",
    )
    graph = parse_file(str(path))
    assert _calls(graph, "sample:outer")[0][1] == EdgeConfidence.UNCERTAIN
    assert all(c == EdgeConfidence.UNCERTAIN for _, c in _calls(graph, "sample:catcher"))
