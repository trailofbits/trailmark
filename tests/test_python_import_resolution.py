from pathlib import Path

from trailmark.models.edges import EdgeKind
from trailmark.parse import parse_directory


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
