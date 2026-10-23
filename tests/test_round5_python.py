"""Python generic declarations, aliases, and long receiver chains stay bounded."""

from pathlib import Path
from time import perf_counter

import pytest

from trailmark.models import EdgeKind
from trailmark.parse import parse_file


def test_long_receiver_chain_is_linear(tmp_path: Path) -> None:
    path = tmp_path / "app.py"
    path.write_text("def run():\n return a()" + ".b()" * 3000 + "\n")
    start = perf_counter()
    graph = parse_file(str(path))
    assert perf_counter() - start < 2
    assert len([e for e in graph.edges if e.kind == EdgeKind.CALLS]) == 3001


def test_deep_python_declarations_use_parameter_fields(tmp_path: Path) -> None:
    path = tmp_path / "app.py"
    path.write_text("".join(" " * i + "def f():\n" for i in range(3000)) + " " * 3000 + "pass\n")
    start = perf_counter()
    graph = parse_file(str(path))
    assert perf_counter() - start < 2
    assert graph.parse_diagnostics and len(graph.nodes) < 100


def test_python_generic_declaration_parameters(tmp_path: Path) -> None:
    path = tmp_path / "app.py"
    path.write_text("def f[T](x: T) -> T: return x\nclass C[T]: pass\n")
    graph = parse_file(str(path))
    assert [p.name for p in graph.nodes["app:f"].type_parameters] == ["T"]
    assert [p.name for p in graph.nodes["app:C"].type_parameters] == ["T"]


@pytest.mark.parametrize("module", ["typing", "typing_extensions"])
def test_overload_from_import_alias(tmp_path: Path, module: str) -> None:
    path = tmp_path / "app.py"
    path.write_text(
        f"from {module} import overload as ov\n"
        "@ov\ndef get(x: int) -> int: ...\n"
        "@ov\ndef get(x: str) -> str: ...\n"
        "def get(x): return x\n"
    )
    graph = parse_file(str(path))
    assert [u.id for u in graph.nodes.values() if u.name == "get"] == ["app:get"]
