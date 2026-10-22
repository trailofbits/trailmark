"""R closures, environment mutation and package dependency boundaries."""

from pathlib import Path

import pytest
from tree_sitter import Parser
from tree_sitter_language_pack import get_language

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.nodes import NodeKind
from trailmark.parse import detect_languages, parse_directory, parse_file
from trailmark.query.api import QueryEngine


def _file(tmp_path: Path, source: str) -> Path:
    assert not Parser(get_language("r")).parse(source.encode()).root_node.has_error
    path = tmp_path / "sample.R"
    path.write_text(source)
    return path


def test_assignment_forms_closures_defaults_variadics_and_native_pipe(tmp_path: Path) -> None:
    path = _file(
        tmp_path,
        """helper <- function(x, y = 1, ...) { if (x > 0) x else y }
(function(x) helper(x)) -> other
main = function() { inner <- function(z) helper(z); inner(1); other(2) }
1 |> helper()
""",
    )
    graph = parse_file(str(path))
    helper = graph.nodes["sample:helper"]
    assert [p.name for p in helper.parameters] == ["x", "y", "..."]
    assert helper.parameters[1].default == "1" and helper.cyclomatic_complexity == 2
    assert "sample:other" in graph.nodes and "sample:main.inner" in graph.nodes
    calls = [
        e
        for e in graph.edges
        if e.kind == EdgeKind.CALLS and not dict(e.attributes).get("deferred")
    ]
    assert len(calls) == 5 and all(e.confidence == EdgeConfidence.INFERRED for e in calls)
    assert QueryEngine.from_directory(str(tmp_path), language="auto").entrypoint_paths_to("helper")


def test_callbacks_parameters_objects_and_namespaces_do_not_bind_namesakes(tmp_path: Path) -> None:
    _file(
        tmp_path,
        """helper <- function() 1
caller <- function(helper) { helper(); pkg::helper(); obj$helper() }
lapply(xs, function(helper) helper())
""",
    )
    (tmp_path / "other.r").write_text("helper <- function() 2")
    graph = parse_directory(str(tmp_path), language="auto")
    calls = [
        e
        for e in graph.edges
        if e.kind == EdgeKind.CALLS and not dict(e.attributes).get("deferred")
    ]
    assert len(calls) == 5 and all(graph.nodes[e.target_id].kind == NodeKind.PROXY for e in calls)
    anonymous = [u for u in graph.nodes.values() if u.name.startswith("<lambda>")]
    assert len(anonymous) == 1
    assert detect_languages(str(tmp_path)) == ["r"]
    assert graph.source_file_counts == {"r": 2}


@pytest.mark.parametrize(
    "setup",
    [
        "helper <- unknown",
        "helper <<- unknown",
        'assign("helper", unknown)',
        'source("other.R")',
        "eval(code)",
        "for (helper in xs) helper()",
    ],
)
def test_reassignment_and_environment_mutation_prevent_inference(
    tmp_path: Path, setup: str
) -> None:
    path = _file(tmp_path, f"helper <- function() 1\n{setup}\nmain <- function() helper()\n")
    graph = parse_file(str(path))
    assert all(
        e.confidence == EdgeConfidence.UNCERTAIN for e in graph.edges if e.kind == EdgeKind.CALLS
    )


def test_dependency_literals_character_only_and_shadowing(tmp_path: Path) -> None:
    path = _file(
        tmp_path,
        """library(utils)
require("stats")
source("helpers.R")
library(dynamic, character.only=TRUE)
caller <- function(library) library(fake)
""",
    )
    assert parse_file(str(path)).dependencies == ["utils", "stats", "helpers.R"]


def test_anonymous_ownership_declaration_order_and_repeated_names(tmp_path: Path) -> None:
    path = _file(
        tmp_path,
        """before()
before <- function() 1
helper <- function() 1
helper <- function() 2
main <- function() lapply(xs, function(x) { if (x) helper() })
""",
    )
    graph = parse_file(str(path))
    assert len([u for u in graph.nodes.values() if u.name == "helper"]) == 2
    assert graph.nodes["sample:main"].cyclomatic_complexity == 1
    anonymous = [u for u in graph.nodes.values() if u.name.startswith("<lambda>")]
    assert len(anonymous) == 1 and anonymous[0].cyclomatic_complexity == 2
    assert all(
        e.confidence == EdgeConfidence.UNCERTAIN for e in graph.edges if e.kind == EdgeKind.CALLS
    )


def test_parse_errors_warn(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "broken.r"
    path.write_text("f <- function(")
    parse_file(str(path))
    assert "R syntax errors" in caplog.text
