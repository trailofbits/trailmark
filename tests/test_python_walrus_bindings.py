"""Assignment-expression locals must not resolve to unrelated named functions."""

from pathlib import Path

import pytest

from trailmark.models import EdgeConfidence, EdgeKind
from trailmark.parse import parse_directory, parse_file


@pytest.mark.mutation_fast
@pytest.mark.parametrize("directory", [False, True])
@pytest.mark.parametrize(
    "body",
    [
        "    if callback := getattr(obj, 'callback', None):\n        callback()\n",
        "    (callback := getattr(obj, 'callback', None))\n    callback()\n",
        "    callback()\n    (callback := getattr(obj, 'callback', None))\n",
        "    (callback := getattr(obj, 'callback', None))\n"
        "    def inner():\n        callback()\n    return inner\n",
    ],
)
def test_walrus_local_blocks_namesake(tmp_path: Path, directory: bool, body: str) -> None:
    """Retain the call, but reject the module target even before assignment."""
    path = tmp_path / "app.py"
    path.write_text("def callback(): pass\ndef outer(obj):\n" + body)
    graph = parse_directory(str(tmp_path), "python") if directory else parse_file(str(path))
    calls = [
        edge
        for edge in graph.edges
        if edge.kind == EdgeKind.CALLS
        and edge.location is not None
        and path.read_text().splitlines()[edge.location.start_line - 1].strip() == "callback()"
    ]
    assert len(calls) == 1
    assert calls[0].target_id != "app:callback"
    assert calls[0].confidence == EdgeConfidence.UNCERTAIN
    assert dict(calls[0].attributes)["binding_blocked"] is True


def test_walrus_local_does_not_block_other_functions(tmp_path: Path) -> None:
    path = tmp_path / "app.py"
    path.write_text(
        "def callback(): pass\ndef outer(obj):\n"
        "    (callback := getattr(obj, 'callback', None))\n"
        "def unshadowed():\n    callback()\n"
    )
    graph = parse_file(str(path))
    call = next(
        edge
        for edge in graph.edges
        if edge.kind == EdgeKind.CALLS and edge.source_id == "app:unshadowed"
    )
    assert call.target_id == "app:callback"
    assert call.confidence == EdgeConfidence.CERTAIN
    assert not dict(call.attributes).get("binding_blocked")


@pytest.mark.mutation_fast
def test_default_walrus_does_not_create_a_function_local(tmp_path: Path) -> None:
    path = tmp_path / "app.py"
    path.write_text(
        "def callback(): pass\ndef outer(value=(callback := callback)):\n    return callback()\n"
    )
    graph = parse_file(str(path), strict=True)
    call = next(edge for edge in graph.edges if edge.kind == EdgeKind.CALLS)
    assert call.target_id == "app:callback"
    assert call.confidence == EdgeConfidence.CERTAIN


@pytest.mark.parametrize(
    "declaration",
    [
        "    def inner(value=(callback := getattr(obj, 'callback', None))):\n        pass\n",
        "    inner = lambda value=(callback := getattr(obj, 'callback', None)): None\n",
    ],
)
def test_nested_default_walrus_binds_in_enclosing_function(
    tmp_path: Path, declaration: str
) -> None:
    path = tmp_path / "app.py"
    path.write_text("def callback(): pass\ndef outer(obj):\n" + declaration + "    callback()\n")
    graph = parse_file(str(path), strict=True)
    call = next(
        edge
        for edge in graph.edges
        if edge.kind == EdgeKind.CALLS
        and edge.location is not None
        and path.read_text().splitlines()[edge.location.start_line - 1].strip() == "callback()"
    )
    assert call.target_id != "app:callback"
    assert dict(call.attributes)["binding_blocked"] is True
