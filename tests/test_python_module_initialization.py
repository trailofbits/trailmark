from pathlib import Path

from trailmark.models.edges import EdgeKind
from trailmark.parse import parse_directory


def test_module_calls_include_decorators_and_exclude_function_bodies(tmp_path: Path) -> None:
    (tmp_path / "startup.py").write_text(
        "def init(): pass\n"
        "def hook(f): return f\n"
        "def later(): pass\n"
        "@hook\n"
        "@hook()\n"
        "def handler():\n"
        "    later()\n"
        "app = init()\n"
    )

    graph = parse_directory(str(tmp_path))
    module_calls = {
        edge.target_id
        for edge in graph.edges
        if edge.kind == EdgeKind.CALLS and edge.source_id == "startup"
    }
    assert module_calls == {"startup:init", "startup:hook"}
