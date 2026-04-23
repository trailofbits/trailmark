"""Tests for the newly exposed QueryEngine methods.

Covers ``ancestors_of``, ``reachable_from``, ``entrypoint_paths_to``,
``nodes_with_annotation``, and ``functions_that_raise`` — methods that
existed in ``GraphStore`` but had no public-facing analogue.
"""

from __future__ import annotations

from pathlib import Path

from trailmark.models.annotations import AnnotationKind
from trailmark.query.api import QueryEngine


class TestAncestorsOf:
    def test_direct_caller_is_ancestor(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text(
            "def sink():\n    pass\n\ndef caller():\n    sink()\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path))
        ancestors = engine.ancestors_of("sink")
        names = {a["name"] for a in ancestors}
        assert "caller" in names

    def test_transitive_ancestor_surfaces(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text(
            "def sink():\n    pass\ndef middle():\n    sink()\ndef top():\n    middle()\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path))
        names = {a["name"] for a in engine.ancestors_of("sink")}
        assert {"middle", "top"}.issubset(names)

    def test_unknown_node_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("def only():\n    pass\n")
        engine = QueryEngine.from_directory(str(tmp_path))
        assert engine.ancestors_of("does_not_exist") == []

    def test_node_with_no_callers_has_only_module_ancestor(self, tmp_path: Path) -> None:
        """A function with no callers still has the module as an ancestor
        via the `contains` edge. Function-level ancestors should be empty.
        """
        (tmp_path / "app.py").write_text("def solo():\n    pass\n")
        engine = QueryEngine.from_directory(str(tmp_path))
        ancestor_kinds = {a["kind"] for a in engine.ancestors_of("solo")}
        assert "function" not in ancestor_kinds
        assert "method" not in ancestor_kinds


class TestReachableFrom:
    def test_direct_callee_is_reachable(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text(
            "def a():\n    b()\n\ndef b():\n    pass\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path))
        names = {n["name"] for n in engine.reachable_from("a")}
        assert "b" in names

    def test_transitive_reachability(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text(
            "def a():\n    b()\ndef b():\n    c()\ndef c():\n    pass\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path))
        names = {n["name"] for n in engine.reachable_from("a")}
        assert {"b", "c"}.issubset(names)


class TestEntrypointPathsTo:
    def test_path_from_entrypoint_to_sink(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text(
            "def main():\n    validate()\n"
            "def validate():\n    sensitive()\n"
            "def sensitive():\n    pass\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path))
        paths = engine.entrypoint_paths_to("sensitive")
        assert paths, "Expected at least one entrypoint->sink path"
        assert any(p[0] == "app:main" and p[-1] == "app:sensitive" for p in paths), paths

    def test_unreachable_sink_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text(
            "def main():\n    pass\n\ndef orphan():\n    pass\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path))
        assert engine.entrypoint_paths_to("orphan") == []


class TestNodesWithAnnotation:
    def test_manual_annotation_retrievable(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("def risky():\n    pass\n")
        engine = QueryEngine.from_directory(str(tmp_path))
        engine.annotate(
            "risky",
            AnnotationKind.FINDING,
            "manually flagged",
            source="test",
        )
        finds = engine.nodes_with_annotation(AnnotationKind.FINDING)
        names = {n["name"] for n in finds}
        assert "risky" in names

    def test_empty_when_no_annotations(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("def ok():\n    pass\n")
        engine = QueryEngine.from_directory(str(tmp_path))
        assert engine.nodes_with_annotation(AnnotationKind.FINDING) == []


class TestFunctionsThatRaise:
    def test_raises_detected_from_python_source(self, tmp_path: Path) -> None:
        """Python parser populates exception_types from `raise` statements."""
        (tmp_path / "app.py").write_text(
            "def bad():\n    raise ValueError('nope')\n\ndef good():\n    return 1\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path))
        raisers = engine.functions_that_raise("ValueError")
        names = {n["name"] for n in raisers}
        assert "bad" in names
        assert "good" not in names

    def test_unknown_exception_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text(
            "def bad():\n    raise ValueError('nope')\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path))
        assert engine.functions_that_raise("KeyError") == []
