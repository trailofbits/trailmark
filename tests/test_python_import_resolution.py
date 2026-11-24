from pathlib import Path

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.parse import parse_directory, parse_roots
from trailmark.query.api import QueryEngine
from trailmark.source_policy import SourcePolicy, SourceRoot


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


def test_explicit_roots_link_python_import_and_call_aliases(tmp_path: Path) -> None:
    app, dependency = tmp_path / "app", tmp_path / "dependency"
    app.mkdir()
    package = dependency / "extpkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "api.py").write_text("def start(): pass\ndef stop(): pass\n")
    source = app / "app.py"
    source.write_text(
        "from extpkg.api import start as boot\n"
        "import extpkg.api as api\n"
        "def run():\n"
        "    boot()\n"
        "    api.stop()\n"
    )
    policy = SourcePolicy(
        (SourceRoot(str(app), "app"), SourceRoot(str(dependency), "dep", "dependency"))
    )

    graph = parse_roots(policy, "python")
    imports = [edge for edge in graph.edges if edge.kind == EdgeKind.IMPORTS]
    assert {(edge.source_id, edge.target_id) for edge in imports} == {
        ("app::app", "dep::extpkg"),
        ("app::app", "dep::extpkg.api"),
    }
    assert {edge.location.start_line for edge in imports if edge.location} == {1, 2}
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    assert [(edge.source_id, edge.target_id) for edge in calls] == [
        ("app::app:run", "dep::extpkg.api:start"),
        ("app::app:run", "dep::extpkg.api:stop"),
    ]
    assert all(edge.confidence == EdgeConfidence.INFERRED for edge in calls)
    assert [edge.location.start_line for edge in calls if edge.location] == [4, 5]
    assert graph.unresolved_calls() == []
    assert all(unit.kind.value != "proxy" for unit in graph.nodes.values())
    engine = QueryEngine.from_graph(graph)
    assert engine.paths_between("app::app:run", "dep::extpkg.api:start") == [
        ["app::app:run", "dep::extpkg.api:start"]
    ]


def test_python_imports_with_ambiguous_cross_root_modules_stay_unresolved(tmp_path: Path) -> None:
    app, first, second = (tmp_path / name for name in ("app", "first", "second"))
    for root in (app, first, second):
        root.mkdir()
    (app / "app.py").write_text("from vendor import start as boot\ndef run(): boot()\n")
    (first / "vendor.py").write_text("def start(): pass\n")
    (second / "vendor.py").write_text("def other(): pass\n")
    roots = tuple(SourceRoot(str(root), root.name) for root in (app, first, second))

    for policy in (SourcePolicy(roots), SourcePolicy(roots[::-1])):
        graph = parse_roots(policy, "python")
        assert all(edge.kind != EdgeKind.IMPORTS for edge in graph.edges)
        (call,) = graph.unresolved_calls()
        assert call.source_id == "app::app:run"
        assert dict(call.attributes)["raw_target"] == "vendor.start"


def test_cross_root_linking_preserves_local_shadowing_and_relative_imports(tmp_path: Path) -> None:
    app, dependency = tmp_path / "app", tmp_path / "dep"
    (app / "pkg").mkdir(parents=True)
    (dependency / "pkg").mkdir(parents=True)
    (dependency / "pkg" / "api.py").write_text("def start(): pass\n")
    (app / "pkg" / "app.py").write_text(
        "from pkg.api import start as boot\n"
        "from .api import start as relative_boot\n"
        "def bound(): boot()\n"
        "def shadow(boot): boot()\n"
        "def bare(): start()\n"
        "def relative(): relative_boot()\n"
    )
    graph = parse_roots(
        SourcePolicy((SourceRoot(str(app), "app"), SourceRoot(str(dependency), "dep"))),
        "python",
    )

    linked = [
        edge
        for edge in graph.edges
        if edge.kind == EdgeKind.CALLS and edge.target_id == "dep::pkg.api:start"
    ]
    assert [edge.source_id for edge in linked] == ["app::pkg.app:bound"]
    assert {edge.source_id for edge in graph.unresolved_calls()} == {
        "app::pkg.app:bare",
        "app::pkg.app:relative",
        "app::pkg.app:shadow",
    }
    assert [
        edge.location.start_line
        for edge in graph.edges
        if edge.kind == EdgeKind.IMPORTS and edge.location is not None
    ] == [1]


def test_local_python_package_keeps_its_imports_within_the_root(tmp_path: Path) -> None:
    app, dependency = tmp_path / "app", tmp_path / "dep"
    for root in (app, dependency):
        (root / "widgets").mkdir(parents=True)
    (app / "widgets/__init__.py").write_text("def start(): pass\n")
    (dependency / "widgets/api.py").write_text("def stop(): pass\n")
    (app / "main.py").write_text(
        "from widgets import start\n"
        "from widgets.api import stop\n"
        "def run():\n"
        "    start()\n"
        "    stop()\n"
    )
    graph = parse_roots(
        SourcePolicy((SourceRoot(str(app), "app"), SourceRoot(str(dependency), "dep"))),
        "python",
    )

    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    assert calls[0].target_id == "app::widgets:start"
    assert graph.unresolved_calls() == calls[1:]
    assert dict(calls[1].attributes)["raw_target"] == "widgets.api.stop"
    assert {edge.target_id for edge in graph.edges if edge.kind == EdgeKind.IMPORTS} == {
        "app::widgets"
    }
