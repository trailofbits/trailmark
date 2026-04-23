"""Tests for Trailmark data models."""

from __future__ import annotations

from trailmark.models import (
    Annotation,
    AnnotationKind,
    AssetValue,
    BranchInfo,
    CodeEdge,
    CodeGraph,
    CodeUnit,
    EdgeConfidence,
    EdgeKind,
    EntrypointKind,
    EntrypointTag,
    NodeKind,
    Parameter,
    SourceLocation,
    TrustLevel,
    TypeRef,
)


def _make_location(
    file_path: str = "test.py",
    start_line: int = 1,
    end_line: int = 10,
) -> SourceLocation:
    return SourceLocation(
        file_path=file_path,
        start_line=start_line,
        end_line=end_line,
    )


def _make_unit(
    node_id: str = "mod:func",
    name: str = "func",
    kind: NodeKind = NodeKind.FUNCTION,
) -> CodeUnit:
    return CodeUnit(
        id=node_id,
        name=name,
        kind=kind,
        location=_make_location(),
    )


class TestSourceLocation:
    def test_basic_creation(self) -> None:
        loc = _make_location("foo.py", 5, 10)
        assert loc.file_path == "foo.py"
        assert loc.start_line == 5
        assert loc.end_line == 10
        assert loc.start_col is None

    def test_with_columns(self) -> None:
        loc = SourceLocation("f.py", 1, 1, start_col=0, end_col=10)
        assert loc.start_col == 0
        assert loc.end_col == 10

    def test_frozen(self) -> None:
        loc = _make_location()
        try:
            loc.file_path = "other.py"  # type: ignore[misc]  # ty: ignore[invalid-assignment]
            assert False, "Should be frozen"  # noqa: B011
        except AttributeError:
            pass


class TestTypeRef:
    def test_simple_type(self) -> None:
        t = TypeRef(name="int")
        assert t.name == "int"
        assert t.module is None
        assert t.generic_args == ()

    def test_generic_type(self) -> None:
        inner = TypeRef(name="int")
        t = TypeRef(name="list", generic_args=(inner,))
        assert len(t.generic_args) == 1
        assert t.generic_args[0].name == "int"


class TestParameter:
    def test_untyped(self) -> None:
        p = Parameter(name="x")
        assert p.name == "x"
        assert p.type_ref is None
        assert p.default is None

    def test_typed_with_default(self) -> None:
        p = Parameter(
            name="x",
            type_ref=TypeRef(name="int"),
            default="42",
        )
        assert p.type_ref is not None
        assert p.type_ref.name == "int"
        assert p.default == "42"


class TestCodeUnit:
    def test_function(self) -> None:
        unit = _make_unit()
        assert unit.kind == NodeKind.FUNCTION
        assert unit.cyclomatic_complexity is None
        assert unit.branches == ()

    def test_with_complexity(self) -> None:
        unit = CodeUnit(
            id="mod:f",
            name="f",
            kind=NodeKind.FUNCTION,
            location=_make_location(),
            cyclomatic_complexity=5,
            branches=(
                BranchInfo(
                    location=_make_location(),
                    condition="x > 0",
                ),
            ),
        )
        assert unit.cyclomatic_complexity == 5
        assert len(unit.branches) == 1


class TestCodeEdge:
    def test_call_edge(self) -> None:
        edge = CodeEdge(
            source_id="mod:a",
            target_id="mod:b",
            kind=EdgeKind.CALLS,
        )
        assert edge.confidence == EdgeConfidence.CERTAIN

    def test_uncertain_edge(self) -> None:
        edge = CodeEdge(
            source_id="mod:a",
            target_id="mod:b",
            kind=EdgeKind.CALLS,
            confidence=EdgeConfidence.UNCERTAIN,
        )
        assert edge.confidence == EdgeConfidence.UNCERTAIN


class TestAnnotations:
    def test_annotation(self) -> None:
        ann = Annotation(
            kind=AnnotationKind.ASSUMPTION,
            description="x is positive",
            source="docstring",
        )
        assert ann.kind == AnnotationKind.ASSUMPTION

    def test_entrypoint_tag_defaults(self) -> None:
        tag = EntrypointTag(kind=EntrypointKind.USER_INPUT)
        assert tag.trust_level == TrustLevel.UNTRUSTED_EXTERNAL
        assert tag.asset_value == AssetValue.LOW


class TestCodeGraph:
    def test_empty_graph(self) -> None:
        g = CodeGraph()
        assert len(g.nodes) == 0
        assert len(g.edges) == 0

    def test_add_annotation(self) -> None:
        g = CodeGraph(nodes={"a": _make_unit("a", "a")})
        ann = Annotation(
            kind=AnnotationKind.ASSUMPTION,
            description="x > 0",
            source="test",
        )
        g.add_annotation("a", ann)
        assert len(g.annotations["a"]) == 1
        assert g.annotations["a"][0] == ann

    def test_add_annotation_creates_list(self) -> None:
        g = CodeGraph()
        ann = Annotation(
            kind=AnnotationKind.PRECONDITION,
            description="not null",
            source="test",
        )
        g.add_annotation("x", ann)
        assert "x" in g.annotations
        assert g.annotations["x"] == [ann]

    def test_add_annotation_appends(self) -> None:
        g = CodeGraph()
        a1 = Annotation(
            kind=AnnotationKind.ASSUMPTION,
            description="first",
            source="test",
        )
        a2 = Annotation(
            kind=AnnotationKind.INVARIANT,
            description="second",
            source="test",
        )
        g.add_annotation("n", a1)
        g.add_annotation("n", a2)
        assert len(g.annotations["n"]) == 2

    def test_clear_annotations_all(self) -> None:
        g = CodeGraph()
        g.add_annotation(
            "a",
            Annotation(
                kind=AnnotationKind.ASSUMPTION,
                description="test",
                source="test",
            ),
        )
        g.clear_annotations("a")
        assert "a" not in g.annotations

    def test_clear_annotations_by_kind(self) -> None:
        g = CodeGraph()
        g.add_annotation(
            "a",
            Annotation(
                kind=AnnotationKind.ASSUMPTION,
                description="assume",
                source="test",
            ),
        )
        g.add_annotation(
            "a",
            Annotation(
                kind=AnnotationKind.INVARIANT,
                description="invariant",
                source="test",
            ),
        )
        g.clear_annotations("a", AnnotationKind.ASSUMPTION)
        assert len(g.annotations["a"]) == 1
        assert g.annotations["a"][0].kind == AnnotationKind.INVARIANT

    def test_clear_annotations_nonexistent_node(self) -> None:
        g = CodeGraph()
        g.clear_annotations("nonexistent")  # Should not raise

    def test_clear_annotations_removes_empty_list(self) -> None:
        g = CodeGraph()
        g.add_annotation(
            "a",
            Annotation(
                kind=AnnotationKind.ASSUMPTION,
                description="test",
                source="test",
            ),
        )
        g.clear_annotations("a", AnnotationKind.ASSUMPTION)
        assert "a" not in g.annotations

    def test_merge(self) -> None:
        g1 = CodeGraph(
            nodes={"a": _make_unit("a", "a")},
            edges=[],
            dependencies=["foo"],
        )
        g2 = CodeGraph(
            nodes={"b": _make_unit("b", "b")},
            edges=[],
            dependencies=["foo", "bar"],
        )
        g1.merge(g2)
        assert len(g1.nodes) == 2
        assert "bar" in g1.dependencies
        assert g1.dependencies.count("foo") == 1
