"""Every registered frontend retains invocation evidence or declares its applicability."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests.kat_runner import fixture_pair
from tests.test_kat_parsers import PARSER_FIXTURES
from trailmark.models.constraint_support import constraint_support_for
from trailmark.models.edges import EdgeKind
from trailmark.parse import parse_file, supported_languages

pytestmark = pytest.mark.mutation_fast


@pytest.mark.parametrize(("language", "basename"), PARSER_FIXTURES)
def test_all_frontend_call_occurrences_keep_provenance(language: str, basename: str) -> None:
    path, _ = fixture_pair(language, basename)
    graph = parse_file(str(path), language)
    calls = {
        (edge.source_id, edge.location)
        for edge in graph.edges
        if edge.kind == EdgeKind.CALLS and edge.location is not None
    }
    sites = {(site.caller_id, site.location) for site in graph.call_sites.values()}
    assert calls <= sites
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    for key, site in graph.call_sites.items():
        assert key == site.id
        assert site.source_sha256 == digest
        assert tuple(argument.index for argument in site.arguments) == tuple(
            range(len(site.arguments))
        )
        for argument in site.arguments:
            assert argument.expression
        if site.execution != "call":
            assert site.gaps
    if constraint_support_for(language).call_sites in {"not_applicable", "unsupported"}:
        assert not calls
        assert not sites


def test_taxonomy_matrix_covers_every_registered_frontend() -> None:
    assert {language for language, _ in PARSER_FIXTURES} == set(supported_languages())


@pytest.mark.parametrize(
    ("language", "filename", "source"),
    [
        ("func", "app.fc", "() target(int x) { }\n() caller() { target(7); }\n"),
        (
            "move",
            "app.move",
            "module 0x1::app { fun target(x: u64) {} fun caller() { target(7); } }",
        ),
        ("tact", "app.tact", "fun target(x: Int) {}\nfun caller() { target(7); }\n"),
    ],
)
def test_nonempty_operands_in_frontends_with_empty_taxonomy_calls(
    tmp_path: Path, language: str, filename: str, source: str
) -> None:
    path = tmp_path / filename
    path.write_text(source)
    graph = parse_file(str(path), language)
    assert any(
        argument.expression == ("(7)" if language == "func" else "7")
        for site in graph.call_sites.values()
        for argument in site.arguments
    )


@pytest.mark.parametrize(
    ("language", "basename"),
    [
        pair
        for pair in PARSER_FIXTURES
        if pair[0]
        not in {
            "masm",
            "func",
            "move",
            "tact",
            "proto",
            "thrift",
            "graphql",
            "sql",
            "rocq",
            "tlaplus",
            "tamarin",
            "proverif",
        }
        and pair[1] not in {"verus_calls", "verus_declarations"}
    ],
)
def test_runtime_taxonomies_retain_nonempty_operands(language: str, basename: str) -> None:
    path, _ = fixture_pair(language, basename)
    graph = parse_file(str(path), language)
    assert any(site.arguments for site in graph.call_sites.values())
