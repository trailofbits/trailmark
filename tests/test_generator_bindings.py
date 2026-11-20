"""Generator declarations participate in lexical call binding."""

from pathlib import Path

import pytest

from trailmark.models import EdgeConfidence, EdgeKind
from trailmark.parse import parse_file


@pytest.mark.parametrize("extension", ["js", "ts", "tsx"])
@pytest.mark.parametrize("async_prefix", ["", "async "])
@pytest.mark.parametrize("namesake", [False, True])
@pytest.mark.parametrize("forward", [False, True])
def test_nested_generator_call_resolves_lexically(
    tmp_path: Path, extension: str, async_prefix: str, namesake: bool, forward: bool
) -> None:
    path = tmp_path / f"app.{extension}"
    declaration = f"    {async_prefix}function* iterate() {{ yield helper(); }}\n"
    invocation = "    iterate();\n"
    path.write_text(
        "function helper() {}\n"
        + ("function iterate() {}\n" if namesake else "")
        + "function outer() {\n"
        + (invocation + declaration if forward else declaration + invocation)
        + "}\n"
    )
    graph = parse_file(str(path), strict=True)
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    direct = [
        edge
        for edge in calls
        if edge.source_id == "app:outer" and not dict(edge.attributes).get("deferred")
    ]
    assert len(direct) == 1
    assert direct[0].target_id == "app:outer.iterate"
    assert direct[0].confidence == EdgeConfidence.CERTAIN
    assert not dict(direct[0].attributes).get("binding_blocked")
    assert direct[0].location is not None
    assert path.read_text().splitlines()[direct[0].location.start_line - 1].strip() == "iterate();"
    body = [edge for edge in calls if edge.source_id == "app:outer.iterate"]
    assert len(body) == 1 and body[0].target_id == "app:helper"


@pytest.mark.parametrize("extension", ["js", "ts", "tsx"])
def test_generator_parameter_stays_binding_blocked(tmp_path: Path, extension: str) -> None:
    path = tmp_path / f"app.{extension}"
    path.write_text(
        "function iterate() {}\nfunction outer() {\n"
        "    function* inner(iterate) { yield iterate(); }\n}\n"
    )
    graph = parse_file(str(path), strict=True)
    call = next(
        edge
        for edge in graph.edges
        if edge.kind == EdgeKind.CALLS and edge.source_id == "app:outer.inner"
    )
    assert call.target_id != "app:iterate"
    assert call.confidence == EdgeConfidence.UNCERTAIN
    assert dict(call.attributes)["binding_blocked"] is True
