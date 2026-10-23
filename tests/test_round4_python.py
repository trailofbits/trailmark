"""Python runtime identities and rebinding remain stable."""

from pathlib import Path

import pytest

from trailmark.models import EdgeKind
from trailmark.parse import parse_file


def test_nested_receiver_proxy_has_no_private_owner_id(tmp_path: Path) -> None:
    path = tmp_path / "app.py"
    path.write_text("def listen():\n class H:\n  def handle(self):\n   self.server.verify()\n")
    graph = parse_file(str(path))
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)
    assert call.target_id == "proxy.unresolved:app:listen.H.server.verify"
    assert "@" not in str(call.attributes)


@pytest.mark.parametrize("class_last", [False, True])
def test_last_class_or_function_definition_wins(tmp_path: Path, class_last: bool) -> None:
    path = tmp_path / "app.py"
    definitions = ["def Token(): pass\n", "class Token: pass\n"]
    path.write_text(
        "".join(definitions if class_last else definitions[::-1]) + "def run(): return Token()\n"
    )
    graph = parse_file(str(path))
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)
    assert call.target_id == ("app:Token" if class_last else "app:Token@2_0")


@pytest.mark.parametrize("module", ["typing", "typing_extensions"])
def test_overload_accepts_typing_import_alias(tmp_path: Path, module: str) -> None:
    path = tmp_path / "app.py"
    path.write_text(
        f"import {module} as t\n"
        "@t.overload\ndef get(x: int) -> int: ...\n"
        "@t.overload\ndef get(x: str) -> str: ...\n"
        "def get(x): return x\n"
    )
    graph = parse_file(str(path))
    assert [u.id for u in graph.nodes.values() if u.name == "get"] == ["app:get"]


def test_local_definition_rebinds_parameter(tmp_path: Path) -> None:
    path = tmp_path / "app.py"
    path.write_text("def outer(f=None):\n def f(): pass\n return f()\n")
    graph = parse_file(str(path))
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)
    assert call.target_id == "app:outer.f"
    assert call.confidence.value == "certain"


def test_named_factory_chain_retains_method_edge(tmp_path: Path) -> None:
    path = tmp_path / "app.py"
    path.write_text('def git(*a): return ""\ndef run(): return git("worktree").count("w")\n')
    graph = parse_file(str(path))
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert sum(e.target_id == "app:git" for e in calls) == 1
    assert len(calls) == 2
