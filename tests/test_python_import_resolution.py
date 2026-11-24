from pathlib import Path

from trailmark.models.edges import EdgeKind
from trailmark.parse import parse_directory
from trailmark.query.api import QueryEngine


def test_package_import_edges_and_lexical_aliases(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("loaded = True\n")
    (tmp_path / "pkg" / "service.py").write_text("def start(): pass\ndef stop(): pass\n")
    (tmp_path / "pkg" / "app.py").write_text(
        "from .service import start as boot\n"
        "from pkg import service\n"
        "def run():\n"
        "    boot()\n"
        "    service.stop()\n"
        "def shadow(boot): boot()\n"
    )

    graph = parse_directory(str(tmp_path))
    edges = {(edge.kind, edge.source_id, edge.target_id) for edge in graph.edges}
    assert (EdgeKind.CALLS, "pkg.app:run", "pkg.service:start") in edges
    assert (EdgeKind.CALLS, "pkg.app:run", "pkg.service:stop") in edges
    assert (EdgeKind.IMPORTS, "pkg.app", "pkg") in edges
    assert (EdgeKind.IMPORTS, "pkg.app", "pkg.service") in edges
    assert (EdgeKind.CALLS, "pkg.app:shadow", "pkg.service:start") not in edges


def test_cross_file_fallback_only_matches_module_level_callables(tmp_path: Path) -> None:
    (tmp_path / "library.py").write_text(
        "def public(): pass\n"
        "def factory():\n"
        "    def hidden(): pass\n"
        "class Handler:\n"
        "    def method(self): pass\n"
    )
    (tmp_path / "app.py").write_text("def run():\n    public()\n    hidden()\n    method()\n")

    graph = parse_directory(str(tmp_path))
    targets = {
        edge.target_id
        for edge in graph.edges
        if edge.kind == EdgeKind.CALLS and edge.source_id == "app:run"
    }
    assert targets == {
        "library:public",
        "proxy.unresolved:app:hidden",
        "proxy.unresolved:app:method",
    }


def test_bases_obey_import_aliases_and_package_bindings(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "base.py").write_text("class Base: pass\n")
    (tmp_path / "pkg" / "child.py").write_text(
        "from .base import Base as Parent\n"
        "from . import base\n"
        "class Child(Parent): pass\n"
        "class Other(base.Base): pass\n"
    )
    (tmp_path / "unrelated.py").write_text("class Parent: pass\nclass Base: pass\n")
    graph = parse_directory(str(tmp_path))
    assert {(e.source_id, e.target_id) for e in graph.edges if e.kind == EdgeKind.INHERITS} == {
        ("pkg.child:Child", "pkg.base:Base"),
        ("pkg.child:Other", "pkg.base:Base"),
    }


def test_unavailable_conditional_and_shadowed_bases_never_use_name_fallback(tmp_path: Path) -> None:
    (tmp_path / "base.py").write_text("class Base: pass\n")
    (tmp_path / "child.py").write_text(
        "from unavailable import Base\n"
        "class External(Base): pass\n"
        "if condition:\n    from base import Base as Maybe\n"
        "class Conditional(Maybe): pass\n"
        "def factory(Base):\n    class Shadowed(Base): pass\n"
    )
    graph = parse_directory(str(tmp_path))
    bases = [e for e in graph.edges if e.kind == EdgeKind.INHERITS]
    assert len(bases) == 3
    assert all(e.target_id not in graph.nodes for e in bases)
    assert next(e.target_id for e in bases if e.source_id == "child:External") == "unavailable.Base"
    assert QueryEngine.from_graph(graph).callees_of("child:factory") == []
