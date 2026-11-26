"""Lean declarations, lexical ownership, and bounded import resolution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tree_sitter import Parser
from tree_sitter_language_pack import get_language

from trailmark.analysis.entrypoints import detect_entrypoints
from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind, TypeRef
from trailmark.parse import detect_languages, parse_directory, parse_file, supported_languages
from trailmark.parsers.lean import LeanParser
from trailmark.parsers.lean._syntax import application, field
from trailmark.query.api import QueryEngine
from trailmark.source_policy import SourcePolicy, SourceRoot


def _parse(tmp_path: Path, source: str) -> CodeGraph:
    path = tmp_path / "Sample.lean"
    path.write_text(source)
    return parse_file(str(path), strict=True)


def _calls(graph: CodeGraph, source: str) -> list[CodeEdge]:
    return [e for e in graph.edges if e.kind == EdgeKind.CALLS and e.source_id == source]


def _references(graph: CodeGraph, source: str) -> list[CodeEdge]:
    return [e for e in graph.edges if e.kind == EdgeKind.REFERENCES and e.source_id == source]


@pytest.mark.mutation_fast
def test_application_returns_the_head_and_arguments_in_source_order() -> None:
    root = Parser(get_language("lean")).parse(b"def use := combine first second\n").root_node
    declaration = root.named_children[0].named_children[0]
    body = field(declaration, "body")
    assert body is not None and body.type == "app"
    head, arguments = application(body)
    assert head.type == "identifier" and head.text == b"combine"
    assert [argument.text for argument in arguments] == [b"first", b"second"]


@pytest.mark.mutation_fast
def test_reference_fixture_and_opt_in_queries(tmp_path: Path) -> None:
    source = (Path(__file__).parent / "fixtures/lean/references.lean").read_text()
    graph = _parse(tmp_path, source)
    expected = {
        "Geometry.perimeter": [
            ("Geometry.Dimensions", "signature"),
            ("Geometry.Dimensions.width", "projection"),
            ("Geometry.Dimensions.height", "projection"),
            ("Geometry.Dimensions.width", "projection"),
            ("Geometry.Dimensions.height", "projection"),
        ],
        "Geometry.widen": [
            ("Geometry.Dimensions", "signature"),
            ("Geometry.Dimensions", "signature"),
            ("Geometry.Dimensions.width", "record_update"),
            ("Geometry.Dimensions.width", "projection"),
        ],
        "two_is_even": [("divisibleByTwo", "signature")],
        "two_still_even": [("divisibleByTwo", "signature"), ("two_is_even", "body")],
    }
    for owner, targets in expected.items():
        edges = _references(graph, f"Sample:{owner}")
        assert [(e.target_id, dict(e.attributes)["lean_reference_context"]) for e in edges] == [
            (f"Sample:{name}", context) for name, context in targets
        ]
        assert all(e.confidence == EdgeConfidence.INFERRED for e in edges)
        assert all(e.location is not None for e in edges)
        assert not _calls(graph, f"Sample:{owner}")
    assert not any(e.kind in {EdgeKind.READS, EdgeKind.WRITES} for e in graph.edges)
    repeated = _references(graph, "Sample:Geometry.perimeter")[1:]
    assert len({e.location for e in repeated}) == 4
    assert all(e.target_id in graph.nodes for e in graph.edges if e.kind == EdgeKind.REFERENCES)
    assert not any(
        name in dict(e.attributes)
        for e in graph.edges
        if e.kind == EdgeKind.REFERENCES
        for name in ("lean_candidates", "lean_receiver_candidates", "lean_field")
    )

    engine = QueryEngine.from_graph(graph)
    assert engine.reachable_from("two_still_even") == []
    assert engine.ancestors_of("divisibleByTwo") == []
    assert engine.paths_between("two_still_even", "divisibleByTwo") == []
    assert [
        n["id"] for n in engine.reachable_from("two_still_even", edge_kinds=("references",))
    ] == ["Sample:divisibleByTwo", "Sample:two_is_even"]
    assert sorted(
        engine.paths_between("two_still_even", "divisibleByTwo", edge_kinds=("references",))
    ) == [
        ["Sample:two_still_even", "Sample:divisibleByTwo"],
        ["Sample:two_still_even", "Sample:two_is_even", "Sample:divisibleByTwo"],
    ]
    assert [
        n["id"] for n in engine.ancestors_of("Dimensions.width", edge_kinds=("references",))
    ] == ["Sample:Geometry.perimeter", "Sample:Geometry.widen"]
    exported = json.loads(engine.to_json())
    locations = [e["location"] for e in exported["edges"] if e["kind"] == "references"]
    assert len(locations) == sum(map(len, expected.values()))
    assert {location["start_line"] for location in locations} >= {8, 11, 17, 19}


@pytest.mark.mutation_fast
def test_registration_and_public_apis(tmp_path: Path) -> None:
    assert "lean" in supported_languages()
    assert LeanParser().language == "lean"
    graph = _parse(tmp_path, 'def main : IO Unit := IO.println "hello"\n')
    assert graph.language == "lean"
    assert graph.source_file_counts == {"lean": 1}
    assert detect_languages(str(tmp_path)) == ["lean"]
    assert "Sample:main" in detect_entrypoints(graph, str(tmp_path))
    engine = QueryEngine.from_graph(graph)
    assert engine.callees_of("main")[0]["name"] == "IO.println"
    assert json.loads(engine.to_json())["language"] == "lean"
    (tmp_path / "other.py").write_text("def other(): pass\n")
    merged = parse_directory(str(tmp_path), "auto", strict=True)
    assert merged.source_file_counts == {"lean": 1, "python": 1}
    assert {"Sample:main", "other:other"} <= merged.nodes.keys()


@pytest.mark.mutation_fast
def test_declarations_parameters_and_docs(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        """\
namespace Demo
/-- Apply twice. -/
@[inline] private def twice {α : Type} (f : α → α) (x y : α) [Inhabited α] : α := f (f x)
abbrev Count := Nat
opaque hidden (n : Nat) : Nat := n
axiom trusted (n : Nat) : n = n
constant limit : Nat
theorem identity (n : Nat) : n = n := by rfl
lemma truth : True := by trivial
instance named : Inhabited Nat := ⟨0⟩
instance : Inhabited Bool := ⟨false⟩
example : True := by trivial
end Demo
def defaulted (n : Nat := 1) : Nat := n
""",
    )
    unit = graph.nodes["Sample:Demo.twice"]
    assert unit.docstring == "Apply twice."
    assert dict(unit.attributes)["lean_private"] is True
    assert [p.name for p in unit.parameters] == ["α", "f", "x", "y", "$inst4"]
    assert [p.type_ref for p in unit.parameters] == list(
        map(TypeRef, ["Type", "α → α", "α", "α", "Inhabited α"])
    )
    assert unit.return_type == TypeRef("α")
    assert unit.location.start_line == 3
    for name, declaration in [
        ("Count", "abbrev"),
        ("hidden", "opaque"),
        ("trusted", "axiom"),
        ("limit", "constant"),
        ("identity", "theorem"),
        ("truth", "theorem"),
        ("named", "instance"),
    ]:
        assert (
            dict(graph.nodes[f"Sample:Demo.{name}"].attributes)["lean_declaration"] == declaration
        )
    assert len([n for n in graph.nodes if "<instance>@" in n]) == 1
    assert len([n for n in graph.nodes if "<example>@" in n]) == 1
    assert graph.nodes["Sample:defaulted"].parameters[0].default == "1"
    assert not _calls(graph, "Sample:Demo.identity")


@pytest.mark.mutation_fast
def test_namespace_sections_and_reopening(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        """\
namespace Outer.Inner
section Local
def a (n : Nat) := n
end Local
def b (n : Nat) := a n
end Outer.Inner
namespace Outer.Inner
def c (n : Nat) := b n
end Outer.Inner
def _root_.root (n : Nat) := Outer.Inner.c n
""",
    )
    assert graph.nodes["Sample:Outer"].kind == NodeKind.NAMESPACE
    assert graph.nodes["Sample:Outer.Inner"].kind == NodeKind.NAMESPACE
    assert "Sample:Outer.Inner.Local" not in graph.nodes
    assert _calls(graph, "Sample:Outer.Inner.b")[0].target_id == "Sample:Outer.Inner.a"
    assert _calls(graph, "Sample:Outer.Inner.c")[0].target_id == "Sample:Outer.Inner.b"
    assert _calls(graph, "Sample:root")[0].target_id == "Sample:Outer.Inner.c"
    assert (
        sum(
            e.target_id == "Sample:Outer.Inner" and e.kind == EdgeKind.CONTAINS for e in graph.edges
        )
        == 1
    )


@pytest.mark.mutation_fast
def test_types_fields_and_constructors(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        """\
/-- Coordinates. -/
structure Point where
  /-- Horizontal coordinate. -/
  x : Nat
  y : Nat := 0
class Sized (α : Type) where
  size : α → Nat
inductive Listy (α : Type) where
  | nil : Listy α
  | cons (head : α) (tail : Listy α) : Listy α
def singleton (n : Nat) := Listy.cons n Listy.nil
""",
    )
    assert graph.nodes["Sample:Point"].kind == NodeKind.STRUCT
    assert graph.nodes["Sample:Point"].docstring == "Coordinates."
    assert graph.nodes["Sample:Point.x"].kind == NodeKind.STATE_VARIABLE
    assert graph.nodes["Sample:Point.x"].return_type == TypeRef("Nat")
    assert graph.nodes["Sample:Point.x"].docstring == "Horizontal coordinate."
    assert graph.nodes["Sample:Sized"].kind == NodeKind.TRAIT
    assert graph.nodes["Sample:Sized.size"].kind == NodeKind.METHOD
    assert graph.nodes["Sample:Listy"].kind == NodeKind.ENUM
    assert [p.name for p in graph.nodes["Sample:Listy.cons"].parameters] == ["head", "tail"]
    calls = _calls(graph, "Sample:singleton")
    assert len(calls) == 1
    assert calls[0].target_id == "Sample:Listy.cons"


@pytest.mark.mutation_fast
def test_applications_and_branches_exclude_signatures_and_tactics(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        """\
def inc (n : Nat) : Nat := n + 1
def add (a b : Nat) : Nat := a + b
def nested (n : Nat) := add (inc n) (inc n)
def choose (n : Nat) := if n > 0 then inc n else 0
def cases (n : Nat) := match n with
  | 0 => inc 0
  | k + 1 => inc k
theorem proof (n : Nat) : inc n = inc n := by
  exact Eq.refl (inc n)
def quoted := `(inc 1)
""",
    )
    calls = _calls(graph, "Sample:nested")
    assert [e.target_id for e in calls] == ["Sample:add", "Sample:inc", "Sample:inc"]
    assert all(e.confidence == EdgeConfidence.INFERRED and e.location is not None for e in calls)
    assert graph.nodes["Sample:choose"].cyclomatic_complexity == 2
    assert graph.nodes["Sample:choose"].branches[0].condition == "n > 0"
    assert graph.nodes["Sample:cases"].cyclomatic_complexity == 3
    assert [e.target_id for e in _calls(graph, "Sample:cases")] == ["Sample:inc", "Sample:inc"]
    assert not _calls(graph, "Sample:proof")
    assert not _calls(graph, "Sample:quoted")
    assert [e.target_id for e in _references(graph, "Sample:proof")] == ["Sample:inc"] * 2
    assert not _references(graph, "Sample:quoted")


@pytest.mark.mutation_fast
def test_reference_shadowing_in_signatures_and_nested_scopes(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        """\
def predicate (n : Nat) : Prop := n = n
theorem binder (predicate : Nat → Prop) : predicate 0 := by sorry
theorem quantified : ∀ predicate : Nat → Prop, predicate 0 := by sorry
theorem existential : ∃ predicate : Nat → Prop, predicate 0 := by sorry
theorem nested : (∀ predicate : Nat → Prop, predicate 0) ∧ predicate 1 := by sorry
def local := let predicate := 0; predicate
def pattern (n : Nat) := match n with
  | predicate => predicate
def hypothesis := if predicate : True then predicate else predicate
def nestedLet := (let predicate := 0; predicate, predicate)
def lambda := fun predicate => predicate
def setLike := { predicate : Nat | predicate = predicate }
section
variable (predicate : Nat → Prop)
theorem inSection : predicate 0 := by sorry
end
theorem afterSection : predicate 0 := by sorry
""",
    )
    for name in (
        "binder",
        "quantified",
        "existential",
        "local",
        "pattern",
        "hypothesis",
        "setLike",
        "inSection",
    ):
        assert not _references(graph, f"Sample:{name}")
    for name in ("nested", "nestedLet", "afterSection"):
        assert [e.target_id for e in _references(graph, f"Sample:{name}")] == ["Sample:predicate"]
    lambda_id = next(n for n in graph.nodes if n.startswith("Sample:lambda.<lambda>@"))
    assert not _references(graph, lambda_id)


@pytest.mark.mutation_fast
def test_typed_projections_are_lexical_and_type_specific(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        """\
structure Dimensions where
  width : Nat
structure Label where
  width : String
structure Box (α : Type) where
  value : α
abbrev Alias := Dimensions
def numeric (d : Dimensions) := d.width
def textual (d : Label) := d.width
def boxed (b : Box Nat) := b.value
def unknown (d) := d.width
def aliased (d : Alias) := d.width
def shadowType (Dimensions : Type) (d : Dimensions) := d.width
def inferred (d : Dimensions) := let other := d; other.width
def explicit (d : Dimensions) := let other : Dimensions := d; other.width
def scoped (d : Dimensions) := (let d : Label := { width := "x" }; d.width, d.width)
def captured (d : Dimensions) := fun n => d.width + n
def typedLambda := fun d : Dimensions => d.width
def shadowLambda (d : Dimensions) := fun d => d.width
section
variable (d : Dimensions)
def fromSection := d.width
end
def afterSection := d.width
""",
    )

    def projections(owner: str) -> list[str]:
        return [
            e.target_id
            for e in _references(graph, owner)
            if dict(e.attributes)["lean_reference_context"] == "projection"
        ]

    for name, target in (
        ("numeric", "Dimensions.width"),
        ("textual", "Label.width"),
        ("boxed", "Box.value"),
        ("explicit", "Dimensions.width"),
        ("fromSection", "Dimensions.width"),
    ):
        assert projections(f"Sample:{name}") == [f"Sample:{target}"]
    assert projections("Sample:scoped") == ["Sample:Label.width", "Sample:Dimensions.width"]
    for name in ("unknown", "aliased", "shadowType", "inferred", "afterSection"):
        assert not projections(f"Sample:{name}")
    for name in ("captured", "typedLambda", "shadowLambda"):
        child = next(n for n in graph.nodes if n.startswith(f"Sample:{name}.<lambda>@"))
        assert projections(child) == ([] if name == "shadowLambda" else ["Sample:Dimensions.width"])
        deferred = _calls(graph, f"Sample:{name}")[0]
        assert deferred.confidence == EdgeConfidence.UNCERTAIN


@pytest.mark.mutation_fast
def test_signatures_of_types_fields_and_helpers_retain_references(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        """\
structure Dimensions where
  width : Nat
structure Envelope where
  dimensions : Dimensions
class HasDimensions where
  dimensions : Dimensions
inductive Wrapped where
  | make (d : Dimensions) : Wrapped
def predicate (d : Dimensions) : Prop := d.width = 0
def use (d : Dimensions) (h : predicate d) := helper d
where
  helper (x : Dimensions) : Dimensions := x
def local (d : Dimensions) := let helper (x : Dimensions) : Dimensions := x; helper d
""",
    )
    for owner in ("Envelope.dimensions", "HasDimensions.dimensions", "Wrapped.make"):
        refs = _references(graph, f"Sample:{owner}")
        assert refs[0].target_id == "Sample:Dimensions"
        assert dict(refs[0].attributes)["lean_reference_context"] == "signature"
    assert [e.target_id for e in _references(graph, "Sample:use")] == [
        "Sample:Dimensions",
        "Sample:predicate",
        "Sample:use.helper",
    ]
    assert [e.target_id for e in _references(graph, "Sample:use.helper")] == [
        "Sample:Dimensions"
    ] * 2
    local_id = next(n for n in graph.nodes if n.startswith("Sample:local.helper@"))
    assert [e.target_id for e in _references(graph, local_id)] == ["Sample:Dimensions"] * 2
    assert _references(graph, "Sample:local")[-1].target_id == local_id


def test_quotations_and_tactics_do_not_supply_reference_evidence(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        """\
def inc (n : Nat) := n + 1
def quote := `(inc 1)
def antiquote (x) := `(inc $(x))
theorem proof : True := by
  have x := inc 1
  trivial
""",
    )
    for name in ("quote", "antiquote", "proof"):
        assert not _references(graph, f"Sample:{name}")
        assert not _calls(graph, f"Sample:{name}")


def test_annotations_defaults_and_labels_are_distinct(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        """\
structure Dimensions where
  width : Nat
def sample : Dimensions := { width := 1 }
def identity (d : Dimensions) : Dimensions := d
def defaulted (d : Dimensions := sample) : Dimensions := d
def ascribed (d : Dimensions) := (identity : Dimensions → Dimensions) d
def width := 0
def label := identity (d := sample)
def anonymous : Dimensions := .mk 1
def nameOnly := `sample
""",
    )
    assert [e.target_id for e in _references(graph, "Sample:defaulted")] == [
        "Sample:Dimensions",
        "Sample:sample",
        "Sample:Dimensions",
    ]
    assert not _calls(graph, "Sample:defaulted")
    refs = _references(graph, "Sample:ascribed")
    assert [(e.target_id, dict(e.attributes)["lean_reference_context"]) for e in refs] == [
        ("Sample:Dimensions", "signature"),
        ("Sample:Dimensions", "body"),
        ("Sample:Dimensions", "body"),
        ("Sample:identity", "body"),
    ]
    assert [e.target_id for e in _references(graph, "Sample:label")] == [
        "Sample:identity",
        "Sample:sample",
    ]
    assert not _references(graph, "Sample:nameOnly")
    assert [e.target_id for e in _references(graph, "Sample:anonymous")] == ["Sample:Dimensions"]


@pytest.mark.mutation_fast
def test_nested_callable_ownership_and_lexical_blocking(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        """\
def helper (n : Nat) := n + 1
def callback (helper : Nat → Nat) (n : Nat) := helper n
def outer (n : Nat) := let local (x : Nat) := helper x; local n
def closure := fun helper => helper 1
def scoped (n : Nat) : IO Nat := do
  let helper := n
  helper n
def auxiliary (n : Nat) := go n
where
  go (x : Nat) := helper x
""",
    )
    blocked = _calls(graph, "Sample:callback")[0]
    assert dict(blocked.attributes)["binding_blocked"] is True
    assert graph.nodes[blocked.target_id].kind == NodeKind.PROXY
    local = next(n for n in graph.nodes if n.startswith("Sample:outer.local@"))
    assert _calls(graph, local)[0].target_id == "Sample:helper"
    assert all(e.target_id == local for e in _calls(graph, "Sample:outer"))
    assert len(_calls(graph, local)) == 1
    closure = next(n for n in graph.nodes if n.startswith("Sample:closure.<lambda>@"))
    assert dict(_calls(graph, closure)[0].attributes)["binding_blocked"] is True
    assert dict(_calls(graph, "Sample:closure")[0].attributes)["deferred"] is True
    assert dict(_calls(graph, "Sample:scoped")[0].attributes)["binding_blocked"] is True
    assert _calls(graph, "Sample:auxiliary.go")[0].target_id == "Sample:helper"
    assert all(e.target_id == "Sample:auxiliary.go" for e in _calls(graph, "Sample:auxiliary"))


@pytest.mark.mutation_fast
def test_import_resolution_is_scoped_and_transitive(tmp_path: Path) -> None:
    (tmp_path / "Lib").mkdir()
    (tmp_path / "Lib" / "Base.lean").write_text("""\
namespace Util
def inc (n : Nat) := n + 1
private def secret (n : Nat) := n
end Util
""")
    (tmp_path / "Bridge.lean").write_text("import Lib.Base\n")
    (tmp_path / "Unrelated.lean").write_text("def missing (n : Nat) := n\n")
    (tmp_path / "Main.lean").write_text("""\
import Bridge
open Util
def run (n : Nat) := inc n
def qualified (n : Nat) := Util.inc n
def hidden (n : Nat) := Util.secret n
def unknown (n : Nat) := missing n
def callback (inc : Nat → Nat) (n : Nat) := inc n
""")
    graph = parse_directory(str(tmp_path), "lean", strict=True)
    assert graph.source_file_counts == {"lean": 4}
    assert set(graph.dependencies) == {"Bridge", "Lib.Base"}
    for name in ("run", "qualified"):
        assert _calls(graph, f"Main:{name}")[0].target_id == "Lib.Base:Util.inc"
        assert [e.target_id for e in _references(graph, f"Main:{name}")] == ["Lib.Base:Util.inc"]
    for name in ("hidden", "unknown", "callback"):
        call = _calls(graph, f"Main:{name}")[0]
        assert graph.nodes[call.target_id].kind == NodeKind.PROXY
        assert call.confidence == EdgeConfidence.UNCERTAIN
        assert not _references(graph, f"Main:{name}")


def test_open_ambiguity_and_scope(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        """\
namespace A
def f (n : Nat) := n
end A
namespace B
def f (n : Nat) := n
end B
section Local
open A
def one (n : Nat) := f n
end Local
def outside (n : Nat) := f n
open A B
def ambiguous (n : Nat) := f n
def explicit (n : Nat) := _root_.A.f n
""",
    )
    assert _calls(graph, "Sample:one")[0].target_id == "Sample:A.f"
    assert _calls(graph, "Sample:explicit")[0].target_id == "Sample:A.f"
    for name in ("one", "explicit"):
        assert [e.target_id for e in _references(graph, f"Sample:{name}")] == ["Sample:A.f"]
    for name in ("outside", "ambiguous"):
        assert graph.nodes[_calls(graph, f"Sample:{name}")[0].target_id].kind == NodeKind.PROXY
        assert not _references(graph, f"Sample:{name}")


@pytest.mark.mutation_fast
def test_reference_linking_waits_for_imported_types_and_rejects_ambiguity(tmp_path: Path) -> None:
    (tmp_path / "Types.lean").write_text("""\
namespace Shape
structure Dimensions where
  width : Nat
private def hidden : Nat := 1
def visible := hidden
end Shape
""")
    (tmp_path / "Bridge.lean").write_text("import Types\n")
    main = tmp_path / "Main.lean"
    main.write_text("""\
import Bridge
open Shape
def measure (d : Dimensions) := d.width
def widen (d : Dimensions) := { d with width := 1 }
def inaccessible := Shape.hidden
""")
    # A file parse must not discover sibling files by itself.
    isolated = parse_file(str(main), strict=True)
    assert not [e for e in isolated.edges if e.kind == EdgeKind.REFERENCES]
    graph = parse_directory(str(tmp_path), "lean", strict=True)
    for name, context in (("measure", "projection"), ("widen", "record_update")):
        refs = _references(graph, f"Main:{name}")
        assert [e.target_id for e in refs] == [
            "Types:Shape.Dimensions",
            "Types:Shape.Dimensions.width",
        ]
        assert dict(refs[1].attributes)["lean_reference_context"] == context
    assert not _references(graph, "Main:inaccessible")
    assert [e.target_id for e in _references(graph, "Types:Shape.visible")] == [
        "Types:Shape.hidden"
    ]

    (tmp_path / "Other.lean").write_text("""\
namespace Shape
structure Dimensions where
  width : Nat
end Shape
""")
    main.write_text("import Other\n" + main.read_text())
    ambiguous = parse_directory(str(tmp_path), "lean", strict=True)
    assert not _references(ambiguous, "Main:measure")
    assert not _references(ambiguous, "Main:widen")


def test_references_do_not_bind_other_languages_or_source_roots(tmp_path: Path) -> None:
    app = tmp_path / "app"
    lib = tmp_path / "lib"
    app.mkdir()
    lib.mkdir()
    (app / "Main.lean").write_text(
        "import Types\ndef value := Dimensions\ndef identity (d : Dimensions) := d\n"
    )
    (app / "Types.py").write_text("class Dimensions: pass\n")
    polyglot = parse_directory(str(app), "auto", strict=True)
    assert not [e for e in polyglot.edges if e.kind in {EdgeKind.REFERENCES, EdgeKind.TYPE_USES}]
    (lib / "Types.lean").write_text("structure Dimensions where\n  width : Nat\n")
    roots = parse_directory(
        str(tmp_path),
        "auto",
        strict=True,
        source_policy=SourcePolicy((SourceRoot(str(app), "app"), SourceRoot(str(lib), "lib"))),
    )
    assert not [e for e in roots.edges if e.kind in {EdgeKind.REFERENCES, EdgeKind.TYPE_USES}]


def test_destructured_binders_and_hypotheses_block_global_calls(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        """\
def helper (n : Nat) := n
def lambdaPair := fun (helper, n) => helper n
def letPair (n : Nat) := let (helper, x) := (n, n); helper x
def hypothesis (n : Nat) := if helper : n > 0 then helper n else helper n
""",
    )
    lambda_id = next(n for n in graph.nodes if n.startswith("Sample:lambdaPair.<lambda>@"))
    assert [p.name for p in graph.nodes[lambda_id].parameters] == ["helper", "n"]
    for owner in (lambda_id, "Sample:letPair", "Sample:hypothesis"):
        calls = _calls(graph, owner)
        assert calls
        assert all(dict(e.attributes).get("binding_blocked") is True for e in calls)
        assert all(graph.nodes[e.target_id].kind == NodeKind.PROXY for e in calls)


@pytest.mark.mutation_fast
def test_restricted_opens_do_not_guess_namespaces(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        """\
namespace N
def hidden (n : Nat) := n
end N
open N (other)
def f (n : Nat) := hidden n
""",
    )
    call = _calls(graph, "Sample:f")[0]
    assert graph.nodes[call.target_id].kind == NodeKind.PROXY


@pytest.mark.mutation_fast
def test_command_wrappers_preserve_declarations_without_leaking_scope(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        """\
namespace N
def f (n : Nat) := n
end N
open N in
set_option maxRecDepth 10000 in
def inside (n : Nat) := f n
def outside (n : Nat) := f n
def helper (n : Nat) := n
variable (helper : Nat → Nat) in
def scoped (n : Nat) := helper n
def after (n : Nat) := helper n
""",
    )
    assert _calls(graph, "Sample:inside")[0].target_id == "Sample:N.f"
    assert graph.nodes[_calls(graph, "Sample:outside")[0].target_id].kind == NodeKind.PROXY
    assert dict(_calls(graph, "Sample:scoped")[0].attributes)["binding_blocked"] is True
    assert _calls(graph, "Sample:after")[0].target_id == "Sample:helper"


@pytest.mark.parametrize("source", ["", "-- comment\n", "/- nested /- comment -/ -/\n"])
def test_empty_and_comment_only_files(tmp_path: Path, source: str) -> None:
    graph = _parse(tmp_path, source)
    assert list(graph.nodes) == ["Sample"]


def test_syntax_diagnostics_and_strict_mode(tmp_path: Path) -> None:
    path = tmp_path / "Broken.lean"
    path.write_text("def broken ( :=\n")
    assert parse_file(str(path)).parse_diagnostics
    with pytest.raises(ValueError, match="diagnostic"):
        parse_file(str(path), strict=True)


def test_directory_extensions_skip_rules_and_duplicate_stems(tmp_path: Path) -> None:
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    (tmp_path / ".lake").mkdir()
    for name in ("one/Defs.lean", "two/Defs.LEAN", ".lake/Hidden.lean"):
        (tmp_path / name).write_text("def idNat (n : Nat) := n\n")
    (tmp_path / "ignore.txt").write_text("def nope := 0\n")
    graph = parse_directory(str(tmp_path), "lean", strict=True)
    assert graph.source_file_counts == {"lean": 2}
    assert {"one.Defs:idNat", "two.Defs:idNat"} <= graph.nodes.keys()


def test_exception_support_is_explicit(tmp_path: Path) -> None:
    graph = _parse(tmp_path, 'def fail : IO Unit := throw (IO.userError "failed")\n')
    profile = QueryEngine.from_graph(graph).exception_support()[0]
    assert profile["events"] == "unsupported"
    assert "no exception events are emitted" in profile["notes"]
    assert not any(n.exception_events for n in graph.nodes.values())
