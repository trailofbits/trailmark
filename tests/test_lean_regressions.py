"""Lean robustness and name-resolution regressions."""

from pathlib import Path

import pytest

from trailmark.analysis.proxies import ensure_proxy_nodes
from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind
from trailmark.parse import parse_directory, parse_file
from trailmark.query.api import QueryEngine


def _parse(tmp_path: Path, source: str, *, filename: str = "Sample.lean") -> CodeGraph:
    path = tmp_path / filename
    path.write_text(source)
    return parse_file(str(path), strict=True)


def _targets(graph: CodeGraph, owner: str, kind: EdgeKind = EdgeKind.REFERENCES) -> list[str]:
    return [e.target_id for e in graph.edges if e.source_id == owner and e.kind == kind]


@pytest.mark.parametrize("body", [" + ".join(["x"] * 3000), "[" + ", ".join(["x"] * 8000) + "]"])
def test_deep_and_wide_terms_keep_every_occurrence(tmp_path: Path, body: str) -> None:
    graph = _parse(tmp_path, f"def x := 1\ndef big := {body}\n")
    assert not graph.parse_diagnostics
    assert _targets(graph, "Sample:big") == ["Sample:x"] * body.count("x")


def test_closer_field_does_not_resolve_calls_to_outer_function(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        """\
def width (n : Nat) := n
structure Rect where
  width : Nat
namespace Rect
def double (r : Rect) := 2 * width r
end Rect
""",
    )
    assert _targets(graph, "Sample:Rect.double") == ["Sample:Rect", "Sample:Rect.width"]
    targets = _targets(graph, "Sample:Rect.double", EdgeKind.CALLS)
    assert len(targets) == 1 and graph.nodes[targets[0]].kind == NodeKind.PROXY
    assert "Sample:width" not in targets


def test_hiding_open_is_skipped_without_bogus_namespaces(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        """\
namespace N
def hidden (n : Nat) := n
def shown (n : Nat) := n
end N
open N hiding hidden
def f (n : Nat) := hidden n
def g (n : Nat) := shown n
""",
    )
    assert not _targets(graph, "Sample:f")
    assert not _targets(graph, "Sample:g")
    assert all(
        graph.nodes[t].kind == NodeKind.PROXY for t in _targets(graph, "Sample:f", EdgeKind.CALLS)
    )


def test_renaming_open_never_opens_original_names(tmp_path: Path) -> None:
    path = tmp_path / "Sample.lean"
    path.write_text("""\
namespace N
def before (n : Nat) := n
end N
open N renaming before → after
def f (n : Nat) := before n
def g (n : Nat) := after n
""")
    # This grammar version reports an error for the arrow in the renaming clause.
    graph = parse_file(str(path))
    assert not _targets(graph, "Sample:f")
    assert not _targets(graph, "Sample:g")


def test_simple_do_loop_binds_its_variable(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        """\
def y := 3
def numbers := [1, 2]
def loop : IO Unit := do
  for y in numbers do
    IO.println y
""",
    )
    assert _targets(graph, "Sample:loop") == ["Sample:numbers"]
    calls = [graph.nodes[t].name for t in _targets(graph, "Sample:loop", EdgeKind.CALLS)]
    assert calls == ["IO.println"]


def test_unpreserved_loop_shape_is_reported_instead_of_guessed(tmp_path: Path) -> None:
    path = tmp_path / "Sample.lean"
    path.write_text("""\
def y := 3
def loop : IO Unit := do
  for y in [1,2] do
    IO.println y
  IO.println y
""")
    graph = parse_file(str(path))
    assert any(
        d.kind == "unsupported_syntax" and "Loop shape" in d.message
        for d in graph.parse_diagnostics
    )
    assert not _targets(graph, "Sample:loop")
    with pytest.raises(ValueError, match="Incomplete source analysis"):
        parse_file(str(path), strict=True)


def test_blocked_call_name_cannot_become_a_module_target(tmp_path: Path) -> None:
    graph = _parse(tmp_path, "def apply (f : Nat → Nat) (x : Nat) := f x\n", filename="f.lean")
    (target,) = _targets(graph, "f:apply", EdgeKind.CALLS)
    assert graph.nodes[target].kind == NodeKind.PROXY
    assert graph.nodes[target].name == "f"
    assert graph.nodes["f"].kind == NodeKind.MODULE
    before = graph.edges.copy()
    ensure_proxy_nodes(graph)
    assert graph.edges == before


def test_unknown_qualified_call_cannot_become_a_directory_module_target(tmp_path: Path) -> None:
    (tmp_path / "Foo").mkdir()
    (tmp_path / "Foo/Bar.lean").write_text("def value := 1\n")
    (tmp_path / "Main.lean").write_text("def run := Foo.Bar 1\n")
    graph = parse_directory(str(tmp_path), "lean", strict=True)
    (target,) = _targets(graph, "Main:run", EdgeKind.CALLS)
    assert graph.nodes[target].kind == NodeKind.PROXY
    assert graph.nodes[target].name == "Foo.Bar"
    assert graph.nodes["Foo.Bar"].kind == NodeKind.MODULE


def test_type_uses_obey_namespace_order_and_shadowing(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        """\
namespace Geometry
structure Dimensions where
  w : Nat
end Geometry
def absent (d : Dimensions) := d.w
def shadow (Dimensions : Type) (d : Dimensions) := d
def qualified (d : Geometry.Dimensions) := d.w
open Geometry
def opened (d : Dimensions) := d.w
""",
    )
    assert not _targets(graph, "Sample:absent", EdgeKind.TYPE_USES)
    assert not _targets(graph, "Sample:shadow", EdgeKind.TYPE_USES)
    for name in ("qualified", "opened"):
        assert _targets(graph, f"Sample:{name}", EdgeKind.TYPE_USES) == [
            "Sample:Geometry.Dimensions"
        ]
    assert all(
        e.confidence == EdgeConfidence.INFERRED for e in graph.edges if e.kind == EdgeKind.TYPE_USES
    )


def test_type_uses_cannot_rebind_unimported_private_or_ambiguous_types(tmp_path: Path) -> None:
    (tmp_path / "A.lean").write_text(
        "structure Dimensions where\n  w : Nat\nprivate structure Secret where\n  w : Nat\n"
    )
    (tmp_path / "B.lean").write_text("def unimported (d : Dimensions) := d\n")
    (tmp_path / "C.lean").write_text("import A\ndef inaccessible (d : Secret) := d\n")
    (tmp_path / "D.lean").write_text("structure Dimensions where\n  w : Nat\n")
    (tmp_path / "E.lean").write_text("import A\nimport D\ndef ambiguous (d : Dimensions) := d\n")
    graph = parse_directory(str(tmp_path), "lean", strict=True)
    for name in ("B:unimported", "C:inaccessible", "E:ambiguous"):
        assert not _targets(graph, name, EdgeKind.TYPE_USES)


def test_later_declarations_do_not_capture_earlier_uses(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        """\
def sqrt (n : Nat) := n
namespace Vec
def norm (n : Nat) := sqrt n
def sqrt (n : Nat) := n
def after (n : Nat) := sqrt n
end Vec
def recursive (n : Nat) := recursive n
mutual
def left (n : Nat) := right n
def right (n : Nat) := left n
end
""",
    )
    for kind in (EdgeKind.CALLS, EdgeKind.REFERENCES):
        assert _targets(graph, "Sample:Vec.norm", kind) == ["Sample:sqrt"]
        assert _targets(graph, "Sample:Vec.after", kind) == ["Sample:Vec.sqrt"]
        assert _targets(graph, "Sample:recursive", kind) == ["Sample:recursive"]
        assert _targets(graph, "Sample:left", kind) == ["Sample:right"]
        assert _targets(graph, "Sample:right", kind) == ["Sample:left"]
    assert not any(k.startswith("_lean_") for u in graph.nodes.values() for k, _ in u.attributes)


def test_call_exports_contain_no_temporary_lookup_or_import_flags(tmp_path: Path) -> None:
    graph = _parse(tmp_path, "def f (n : Nat) := n\ndef g := f 1\ndef h := absent 1\n")
    for edge in graph.edges:
        assert (
            not {"_lookup", "lean_candidates", "lean_receiver_candidates", "import_binding"}
            & dict(edge.attributes).keys()
        )
    assert _targets(graph, "Sample:g", EdgeKind.CALLS) == ["Sample:f"]


def test_protected_names_require_qualification(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        """\
namespace A
protected def baz := 1
end A
open A
def unqualified := baz
def qualified := A.baz
""",
    )
    assert not _targets(graph, "Sample:unqualified")
    assert _targets(graph, "Sample:qualified") == ["Sample:A.baz"]


def test_root_and_open_names_compete_at_the_same_tier(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        """\
namespace O
def x := 2
end O
def x := 1
open O
def use := x
namespace Inner
def x := 3
def local := x
end Inner
def exact := _root_.x
""",
    )
    assert not _targets(graph, "Sample:use")
    assert _targets(graph, "Sample:Inner.local") == ["Sample:Inner.x"]
    assert _targets(graph, "Sample:exact") == ["Sample:x"]


def test_field_defaults_have_references_without_state_variable_callers(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        """\
def helper (n : Nat) := n
structure Cfg where
  size : Nat := helper 3
""",
    )
    assert _targets(graph, "Sample:Cfg.size") == ["Sample:helper"]
    assert not _targets(graph, "Sample:Cfg.size", EdgeKind.CALLS)
    assert QueryEngine.from_graph(graph).callers_of("helper") == []


def test_body_annotations_keep_body_context_without_creating_calls(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        """\
structure Item where
  value : Nat
def helper (i : Item) := i
def run (i : Item) := (helper : Item → Item) i
def annotated (i : Item) := let x : Item := i; x
""",
    )
    for name, contexts in (
        ("run", ["signature", "body", "body", "body"]),
        ("annotated", ["signature", "body"]),
    ):
        assert [
            dict(e.attributes)["lean_reference_context"]
            for e in graph.edges
            if e.source_id == f"Sample:{name}" and e.kind == EdgeKind.REFERENCES
        ] == contexts
    assert _targets(graph, "Sample:run", EdgeKind.CALLS) == ["Sample:helper"]
    assert not _targets(graph, "Sample:annotated", EdgeKind.CALLS)


def test_receiver_type_keeps_its_declaration_time_lookup(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        """\
namespace Outer
structure Record where
  width : Nat
namespace Inner
variable (r : Record)
structure Record where
  width : String
def use := r.width
end Inner
end Outer
""",
    )
    assert _targets(graph, "Sample:Outer.Inner.use") == ["Sample:Outer.Record.width"]


def test_recursive_local_value_does_not_capture_a_global(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        """\
def recurse (n : Nat) := n
def use := let rec recurse := fun n => recurse n; recurse 0
""",
    )
    assert "Sample:recurse" not in [
        e.target_id
        for e in graph.edges
        if e.source_id.startswith("Sample:use") and e.kind in {EdgeKind.CALLS, EdgeKind.REFERENCES}
    ]
