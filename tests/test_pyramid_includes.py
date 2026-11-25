from pathlib import Path

import pytest

from tests.parse_work import count_work
from trailmark.analysis._pyramid_includes import discover_pyramid_includes
from trailmark.models.edges import EdgeKind
from trailmark.query.api import QueryEngine


@pytest.mark.mutation_fast
def test_literal_includes_follow_a_proven_registrar_and_its_included_configurators(
    tmp_path: Path,
) -> None:
    package = tmp_path / "app"
    package.mkdir()
    (package / "__init__.py").write_text(
        "from pyramid.config import Configurator as Builder\n"
        "def main():\n    config = Builder()\n    config.include('.setup')\n"
    )
    (package / "setup.py").write_text("def includeme(config):\n    config.include('.csrf')\n")
    (package / "csrf.py").write_text("def includeme(config): pass\n")
    (package / "unrelated.py").write_text("def custom(config):\n    config.include('app.csrf')\n")
    engine, _ = count_work(lambda: QueryEngine.from_directory(str(tmp_path)), max_work=100_000)
    graph = engine._store._graph
    references = [
        e for e in graph.edges if dict(e.attributes).get("reference_context") == "pyramid_include"
    ]
    assert {(e.source_id, e.target_id) for e in references} == {
        ("app:main", "app.setup:includeme"),
        ("app.setup:includeme", "app.csrf:includeme"),
    }
    assert all(e.kind == EdgeKind.REFERENCES and e.location is not None for e in references)
    assert not engine.paths_between("app:main", "app.csrf:includeme")
    before = list(graph.edges)
    count_work(lambda: discover_pyramid_includes(graph), max_work=100_000)
    assert graph.edges == before


@pytest.mark.mutation_fast
def test_shadowed_or_custom_package_registrars_do_not_guess_relative_targets(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.py").write_text(
        "from pyramid.config import Configurator\n"
        "def shadow(Configurator):\n    config = Configurator()\n    config.include('target')\n"
        "def custom():\n    config = Configurator(package='other')\n    config.include('.target')\n"
        "def unrelated():\n    obj = unknown()\n    obj.include('target')\n"
    )
    (tmp_path / "target.py").write_text("def includeme(config): pass\n")
    engine, _ = count_work(lambda: QueryEngine.from_directory(str(tmp_path)), max_work=100_000)
    assert not any(
        dict(e.attributes).get("reference_context") == "pyramid_include"
        for e in engine._store._graph.edges
    )
